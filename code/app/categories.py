"""管理员统一菜品分类。"""
from pathlib import Path
from app.db import connect, run_write
from app.audit import write_audit_log


def list_categories(path: str | Path, active_only: bool = True) -> list[dict]:
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            "SELECT name, active FROM menu_categories WHERE (? = 0 OR active = 1) ORDER BY name",
            (int(active_only),))]


def manage_category(path: str | Path, admin_id: str, action: str,
                    name: str, new_name: str = "") -> None:
    if action not in ("add", "rename", "disable", "enable") or not name.strip():
        raise ValueError("分类操作无效")
    name = name.strip()
    def operation(db):
        actor = db.execute("SELECT role, status FROM accounts WHERE id = ?", (admin_id,)).fetchone()
        if not actor or actor["role"] != "admin" or actor["status"] != "active":
            raise ValueError("仅管理员可管理菜品分类")
        if action == "add":
            db.execute("INSERT INTO menu_categories(name) VALUES (?)", (name,))
        elif action == "rename":
            target = new_name.strip()
            if not target:
                raise ValueError("新分类名称不能为空")
            old = db.execute("SELECT active FROM menu_categories WHERE name = ?", (name,)).fetchone()
            if not old:
                raise ValueError("分类不存在")
            db.execute("INSERT INTO menu_categories(name,active) VALUES (?,1)", (target,))
            db.execute("UPDATE menu_items SET category = ? WHERE category = ?", (target, name))
            db.execute("UPDATE menu_categories SET active = ? WHERE name = ?", (old["active"], target))
            db.execute("DELETE FROM menu_categories WHERE name = ?", (name,))
        else:
            result = db.execute("UPDATE menu_categories SET active = ? WHERE name = ?",
                                (int(action == "enable"), name))
            if result.rowcount != 1:
                raise ValueError("分类不存在")
        write_audit_log(db, admin_id, None, "manage_category", "success", f"{action}: {name}")
    run_write(path, operation)
