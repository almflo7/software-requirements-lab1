"""管理员维护可选配送区域。"""

from pathlib import Path

from app.db import connect, run_write


def list_areas(path: str | Path) -> list[dict]:
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            "SELECT code, name FROM delivery_areas WHERE active = 1 ORDER BY name"
        )]


def add_area(path: str | Path, admin_id: str, code: str, name: str) -> None:
    if not code.strip() or not name.strip():
        raise ValueError("区域编号和名称不能为空")

    def operation(db):
        admin = db.execute(
            "SELECT 1 FROM accounts WHERE id = ? AND role = 'admin' AND status = 'active'",
            (admin_id,),
        ).fetchone()
        if not admin:
            raise ValueError("只有管理员可新增配送区域")
        db.execute("INSERT INTO delivery_areas (code, name) VALUES (?, ?)",
                   (code.strip(), name.strip()))

    run_write(path, operation)
