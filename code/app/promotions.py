"""全员固定金额优惠券与顾客举报。"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.db import connect, run_write
from app.audit import write_audit_log


def _admin(db, account_id):
    row = db.execute("SELECT role, status FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not row or row["role"] != "admin" or row["status"] != "active":
        raise ValueError("仅管理员可执行此操作")


def issue_coupon(path: str | Path, admin_id: str, title: str, amount_cents: int) -> str:
    if not title.strip() or amount_cents <= 0:
        raise ValueError("优惠券名称或金额无效")
    def operation(db):
        _admin(db, admin_id)
        coupon_id = uuid4().hex
        db.execute("INSERT INTO coupons VALUES (?, ?, ?, 1, ?, ?)",
                   (coupon_id, title.strip(), amount_cents, admin_id,
                    datetime.now(timezone.utc).isoformat()))
        write_audit_log(db, admin_id, None, "issue_coupon", "success", coupon_id)
        return coupon_id
    return run_write(path, operation)


def list_coupons(path: str | Path, customer_id: str) -> list[dict]:
    with connect(path) as db:
        actor = db.execute("SELECT role, status FROM accounts WHERE id = ?", (customer_id,)).fetchone()
        if not actor or actor["role"] != "customer" or actor["status"] != "active":
            raise ValueError("仅有效顾客可选优惠券")
        return [dict(row) for row in db.execute(
            "SELECT id, title, amount_cents FROM coupons WHERE active = 1 ORDER BY issued_at DESC")]


def list_issued_coupons(path: str | Path, admin_id: str) -> list[dict]:
    with connect(path) as db:
        _admin(db, admin_id)
        return [dict(row) for row in db.execute(
            "SELECT id, title, amount_cents, active, issued_at FROM coupons ORDER BY issued_at DESC")]


def set_coupon_active(path: str | Path, admin_id: str, coupon_id: str,
                      active: bool) -> None:
    def operation(db):
        _admin(db, admin_id)
        result = db.execute("UPDATE coupons SET active = ? WHERE id = ?",
                            (int(active), coupon_id))
        if result.rowcount != 1:
            raise ValueError("优惠券不存在")
        write_audit_log(db, admin_id, None, "set_coupon_active", "success", coupon_id)
    run_write(path, operation)


def submit_report(path: str | Path, customer_id: str, store_id: str,
                  item_id: str | None, description: str) -> str:
    if not description.strip():
        raise ValueError("举报描述不能为空")
    def operation(db):
        actor = db.execute("SELECT role, status FROM accounts WHERE id = ?", (customer_id,)).fetchone()
        store = db.execute("SELECT id FROM stores WHERE id = ?", (store_id,)).fetchone()
        if not actor or actor["role"] != "customer" or actor["status"] != "active" or not store:
            raise ValueError("无权举报或店铺不存在")
        item = None
        if item_id:
            item = db.execute("SELECT name, price_cents FROM menu_items WHERE id = ? AND store_id = ?",
                              (item_id, store_id)).fetchone()
            if not item:
                raise ValueError("菜品不属于所选店铺")
        report_id = uuid4().hex
        db.execute("""INSERT INTO customer_reports
            (id, customer_id, store_id, item_id, item_name_snapshot,
             item_price_snapshot_cents, description, status, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (report_id, customer_id, store_id, item_id, item["name"] if item else None,
             item["price_cents"] if item else None, description.strip(),
             datetime.now(timezone.utc).isoformat()))
        write_audit_log(db, customer_id, None, "submit_report", "success", report_id, store_id=store_id)
        return report_id
    return run_write(path, operation)


def list_reports(path: str | Path, admin_id: str) -> list[dict]:
    def operation(db):
        _admin(db, admin_id)
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM customer_reports ORDER BY created_at DESC")]
        write_audit_log(db, admin_id, None, "view_reports", "success", str(len(rows)))
        return rows
    return run_write(path, operation)


def resolve_report(path: str | Path, admin_id: str, report_id: str, decision: str) -> None:
    if not decision.strip():
        raise ValueError("处理结果不能为空")
    def operation(db):
        _admin(db, admin_id)
        result = db.execute("""UPDATE customer_reports SET status = 'resolved', decision = ?,
                          decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'""",
                          (decision.strip(), admin_id, datetime.now(timezone.utc).isoformat(), report_id))
        if result.rowcount != 1:
            raise ValueError("举报不存在或已处理")
        write_audit_log(db, admin_id, None, "resolve_report", "success", report_id)
    run_write(path, operation)
