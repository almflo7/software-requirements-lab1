"""固定虚拟地图点位、审核与距离计费。坐标单位为虚拟公里。"""
import math
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.db import connect, run_write
from app.audit import write_audit_log


def _admin(db, actor_id):
    row = db.execute("SELECT role, status FROM accounts WHERE id = ?", (actor_id,)).fetchone()
    if not row or row["role"] != "admin" or row["status"] != "active":
        raise ValueError("仅管理员可维护点位")


def _coordinates(x, y):
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("虚拟坐标须为有限数值")


def list_points(path: str | Path, kind: str = "pickup", active_only: bool = True) -> list[dict]:
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM map_points WHERE kind = ? AND (? = 0 OR active = 1) ORDER BY name, id",
            (kind, int(active_only)))]


def nearby_points(path: str | Path, x: float, y: float, limit: int = 5,
                  keyword: str = "") -> list[dict]:
    _coordinates(x, y)
    points = list_points(path)
    if keyword.strip():
        points = [point for point in points if keyword.strip().lower() in point["name"].lower()]
    for point in points:
        point["distance_km"] = math.hypot(point["x"] - x, point["y"] - y)
    points.sort(key=lambda point: (point["distance_km"], point["name"], point["id"]))
    return points[:limit] if not keyword.strip() else points


def delivery_fee(store_point: dict, pickup_point: dict) -> tuple[float, int | None]:
    distance = math.hypot(store_point["x"] - pickup_point["x"],
                          store_point["y"] - pickup_point["y"])
    return distance, 300 if distance <= 2 else 500 if distance <= 5 else None


def save_point(path: str | Path, admin_id: str, name: str, x: float, y: float,
               point_id: str | None = None, active: bool = True) -> str:
    _coordinates(x, y)
    if not name.strip():
        raise ValueError("点位名称不能为空")
    def operation(db):
        _admin(db, admin_id)
        target = point_id or uuid4().hex
        if point_id:
            result = db.execute("""UPDATE map_points SET name = ?, x = ?, y = ?, active = ?
                                  WHERE id = ? AND kind = 'pickup'""",
                                (name.strip(), x, y, int(active), target))
            if result.rowcount != 1:
                raise ValueError("取件点不存在")
        else:
            db.execute("INSERT INTO map_points VALUES (?, ?, 'pickup', ?, ?, ?)",
                       (target, name.strip(), x, y, int(active)))
        write_audit_log(db, admin_id, None, "save_point", "success", target)
        return target
    return run_write(path, operation)


def import_points(path: str | Path, admin_id: str, rows: list[tuple[str, float, float]],
                  preview: bool = True) -> list[dict]:
    prepared = []
    names = set()
    for name, x, y in rows:
        _coordinates(x, y)
        name = name.strip()
        if not name or name in names:
            raise ValueError("导入点位名称不能为空或重复")
        names.add(name)
        prepared.append({"name": name, "x": x, "y": y})
    with connect(path) as db:
        _admin(db, admin_id)
        if names & {row["name"] for row in db.execute("SELECT name FROM map_points")}:
            raise ValueError("导入点位与现有名称重复")
    if not preview:
        def operation(db):
            _admin(db, admin_id)
            db.executemany("INSERT INTO map_points VALUES (?, ?, 'pickup', ?, ?, 1)",
                           [(uuid4().hex, row["name"], row["x"], row["y"]) for row in prepared])
            write_audit_log(db, admin_id, None, "import_points", "success", str(len(prepared)))
        run_write(path, operation)
    return prepared


def request_store_point(path: str | Path, merchant_id: str, store_id: str,
                        name: str, x: float, y: float) -> str:
    _coordinates(x, y)
    if not name.strip():
        raise ValueError("取餐点名称不能为空")
    def operation(db):
        store = db.execute("""SELECT s.id FROM stores s JOIN accounts a ON a.id = s.merchant_id
                              WHERE s.id = ? AND s.merchant_id = ? AND a.status = 'active'""",
                           (store_id, merchant_id)).fetchone()
        if not store:
            raise ValueError("无权申请该店取餐点")
        request_id = uuid4().hex
        db.execute("""INSERT INTO store_point_requests
            (id,store_id,name,x,y,status,requested_at) VALUES (?,?,?,?,?,'pending',?)""",
            (request_id, store_id, name.strip(), x, y, datetime.now(timezone.utc).isoformat()))
        write_audit_log(db, merchant_id, None, "request_store_point", "success", request_id, store_id=store_id)
        return request_id
    return run_write(path, operation)


def list_store_point_requests(path: str | Path, admin_id: str) -> list[dict]:
    with connect(path) as db:
        _admin(db, admin_id)
        return [dict(row) for row in db.execute(
            "SELECT * FROM store_point_requests ORDER BY requested_at DESC")]


def review_store_point(path: str | Path, admin_id: str, request_id: str,
                       approve: bool) -> None:
    def operation(db):
        _admin(db, admin_id)
        request = db.execute("SELECT * FROM store_point_requests WHERE id = ? AND status = 'pending'",
                             (request_id,)).fetchone()
        if not request:
            raise ValueError("申请不存在或已处理")
        if approve:
            current = db.execute("SELECT store_point_id FROM stores WHERE id = ?",
                                 (request["store_id"],)).fetchone()
            point_id = current["store_point_id"] if current else None
            if point_id:
                db.execute("""UPDATE map_points SET name = ?, x = ?, y = ?, active = 1
                              WHERE id = ? AND kind = 'store'""",
                           (request["name"], request["x"], request["y"], point_id))
            else:
                point_id = uuid4().hex
                db.execute("INSERT INTO map_points VALUES (?, ?, 'store', ?, ?, 1)",
                           (point_id, request["name"], request["x"], request["y"]))
            db.execute("UPDATE stores SET store_point_id = ?, location_text = ? WHERE id = ?",
                       (point_id, request["name"], request["store_id"]))
        db.execute("""UPDATE store_point_requests SET status = ?, reviewed_by = ?,
                      reviewed_at = ? WHERE id = ?""",
                   ("approved" if approve else "rejected", admin_id,
                    datetime.now(timezone.utc).isoformat(), request_id))
        write_audit_log(db, admin_id, None, "review_store_point", "success", request_id,
                        store_id=request["store_id"])
    run_write(path, operation)
