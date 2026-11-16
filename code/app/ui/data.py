"""界面所需的只读数据；写操作统一调用现有业务函数。"""

import json
from pathlib import Path

from app.db import connect


class UiData:
    """按当前登录账号读取界面列表。"""

    def __init__(self, path: str | Path, account_id: str):
        self.path = Path(path)
        self.account_id = account_id

    def account(self) -> dict:
        with connect(self.path) as db:
            row = db.execute(
                "SELECT id, role, status, phone FROM accounts WHERE id = ?",
                (self.account_id,),
            ).fetchone()
            return dict(row) if row else {}

    def addresses(self) -> list[dict]:
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM addresses WHERE customer_id = ? ORDER BY rowid DESC",
                (self.account_id,),
            )]

    def store_info(self, store_id: str) -> dict | None:
        with connect(self.path) as db:
            row = db.execute(
                """SELECT id, name, status, location_text, delivery_areas, store_point_id,
                          delivery_fee_cents FROM stores WHERE id = ?""",
                (store_id,),
            ).fetchone()
            return dict(row) if row else None

    def store_point(self, store_id: str) -> dict | None:
        with connect(self.path) as db:
            row = db.execute("""SELECT p.* FROM map_points p JOIN stores s
                                ON s.store_point_id = p.id WHERE s.id = ?""",
                             (store_id,)).fetchone()
            return dict(row) if row else None

    def delivery_areas(self, store_id: str | None = None) -> list[str]:
        with connect(self.path) as db:
            if store_id:
                rows = db.execute("SELECT delivery_areas FROM stores WHERE id = ?", (store_id,))
                selected = {area for row in rows for area in json.loads(row["delivery_areas"])}
                return sorted(selected)
            else:
                return [row["code"] for row in db.execute(
                    "SELECT code FROM delivery_areas WHERE active = 1 ORDER BY name")]

    def area_names(self) -> dict[str, str]:
        with connect(self.path) as db:
            return {row["code"]: row["name"] for row in db.execute(
                "SELECT code, name FROM delivery_areas")}

    def stores(self) -> list[dict]:
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM stores WHERE merchant_id = ? ORDER BY name",
                (self.account_id,),
            )]

    def store_items(self, store_id: str) -> list[dict]:
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT m.* FROM menu_items AS m JOIN stores AS s ON s.id = m.store_id
                   WHERE m.store_id = ? AND s.merchant_id = ? ORDER BY m.name""",
                (store_id, self.account_id),
            )]

    def public_items(self, store_id: str) -> list[dict]:
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT m.*, s.name AS store_name FROM menu_items AS m
                   JOIN stores AS s ON s.id = m.store_id
                   WHERE m.store_id = ? AND s.status = 'open' AND m.status = 'listed'
                   ORDER BY m.name""", (store_id,),
            )]

    def cart(self, store_id: str | None = None) -> dict | None:
        with connect(self.path) as db:
            if store_id:
                row = db.execute(
                    "SELECT * FROM carts WHERE customer_id = ? AND store_id = ?",
                    (self.account_id, store_id),
                ).fetchone()
            else:
                row = db.execute(
                    """SELECT c.* FROM carts AS c JOIN cart_items AS ci ON ci.cart_id = c.id
                       WHERE c.customer_id = ? GROUP BY c.id ORDER BY c.rowid DESC LIMIT 1""",
                    (self.account_id,),
                ).fetchone()
            return dict(row) if row else None

    def nonempty_carts(self) -> list[dict]:
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT c.id, c.store_id, s.name AS store_name
                   FROM carts AS c JOIN stores AS s ON s.id = c.store_id
                   JOIN cart_items AS ci ON ci.cart_id = c.id
                   WHERE c.customer_id = ? GROUP BY c.id ORDER BY c.rowid DESC""",
                (self.account_id,),
            )]

    def cart_items(self, cart_id: str) -> list[dict]:
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT ci.item_id, ci.quantity, m.name, m.price_cents, m.status
                   FROM cart_items AS ci JOIN carts AS c ON c.id = ci.cart_id
                   JOIN menu_items AS m ON m.id = ci.item_id
                   WHERE ci.cart_id = ? AND c.customer_id = ? ORDER BY m.name""",
                (cart_id, self.account_id),
            )]

    def order_items(self, order_id: str) -> list[dict]:
        with connect(self.path) as db:
            role = self.account()["role"]
            if role == "customer":
                allowed = db.execute(
                    "SELECT 1 FROM orders WHERE id = ? AND customer_id = ?",
                    (order_id, self.account_id),
                ).fetchone()
            elif role == "merchant":
                allowed = db.execute(
                    """SELECT 1 FROM orders AS o JOIN stores AS s ON s.id = o.store_id
                       WHERE o.id = ? AND s.merchant_id = ?""",
                    (order_id, self.account_id),
                ).fetchone()
            elif role == "courier":
                allowed = db.execute(
                    """SELECT 1 FROM delivery_tasks WHERE order_id = ? AND courier_id = ?""",
                    (order_id, self.account_id),
                ).fetchone()
            else:
                allowed = role == "admin"
            if not allowed:
                return []
            return [dict(row) for row in db.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order_id,),
            )]

    def orders(self) -> list[dict]:
        role = self.account().get("role")
        with connect(self.path) as db:
            if role == "customer":
                sql = "SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at DESC"
            elif role == "merchant":
                sql = """SELECT o.* FROM orders AS o JOIN stores AS s ON s.id = o.store_id
                         WHERE s.merchant_id = ? ORDER BY o.placed_at DESC"""
            elif role == "courier":
                sql = """SELECT o.* FROM orders AS o JOIN delivery_tasks AS t ON t.order_id = o.id
                         WHERE t.courier_id = ? ORDER BY o.placed_at DESC"""
            else:
                sql = "SELECT * FROM orders WHERE ? IS NOT NULL ORDER BY placed_at DESC"
            return [dict(row) for row in db.execute(sql, (self.account_id,))]

    def approvals(self) -> list[dict]:
        if self.account().get("role") != "admin":
            return []
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT r.*, a.role, a.phone FROM approval_requests AS r
                   JOIN accounts AS a ON a.id = r.account_id
                   WHERE r.status = 'pending' ORDER BY r.submitted_at"""
            )]

    def couriers(self) -> list[dict]:
        if self.account().get("role") != "admin":
            return []
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                "SELECT id, phone FROM accounts WHERE role = 'courier' AND status = 'active' ORDER BY id"
            )]

    def accounts(self) -> list[dict]:
        if self.account().get("role") != "admin":
            return []
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                "SELECT id, role, status, phone FROM accounts ORDER BY role, id"
            )]

    def all_stores(self) -> list[dict]:
        if self.account().get("role") != "admin":
            return []
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                "SELECT id, name, merchant_id, status FROM stores ORDER BY name"
            )]

    def tasks(self) -> list[dict]:
        if self.account().get("role") != "courier":
            return []
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT t.*, o.status AS order_status, o.store_name_snapshot,
                          o.address_snapshot, o.phone_snapshot
                   FROM delivery_tasks AS t JOIN orders AS o ON o.id = t.order_id
                   WHERE t.courier_id = ? ORDER BY t.assigned_at DESC""",
                (self.account_id,),
            )]

    def exceptions(self) -> list[dict]:
        if self.account().get("role") != "admin":
            return []
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                """SELECT e.*, t.order_id FROM delivery_exceptions AS e
                   JOIN delivery_tasks AS t ON t.id = e.task_id
                   ORDER BY e.reported_at DESC"""
            )]

    def disputes(self) -> list[dict]:
        if self.account().get("role") != "admin":
            return []
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(
                "SELECT id, order_id, reason, status, opened_at FROM dispute_cases ORDER BY opened_at DESC"
            )]

    def pending_cancellations(self) -> list[dict]:
        role = self.account().get("role")
        with connect(self.path) as db:
            if role == "merchant":
                rows = db.execute(
                    """SELECT c.* FROM cancellation_requests AS c
                       JOIN orders AS o ON o.id = c.order_id JOIN stores AS s ON s.id = o.store_id
                       WHERE c.status = 'pending' AND s.merchant_id = ? ORDER BY c.submitted_at""",
                    (self.account_id,),
                )
            elif role == "admin":
                rows = db.execute(
                    "SELECT * FROM cancellation_requests WHERE status = 'pending' ORDER BY submitted_at"
                )
            else:
                return []
            return [dict(row) for row in rows]

    def payment_request(self, order_id: str) -> dict | None:
        with connect(self.path) as db:
            row = db.execute(
                """SELECT p.* FROM payment_records AS p JOIN orders AS o ON o.id = p.order_id
                   WHERE p.order_id = ? AND o.customer_id = ? ORDER BY p.requested_at DESC LIMIT 1""",
                (order_id, self.account_id),
            ).fetchone()
            return dict(row) if row else None

    def refund(self, order_id: str) -> dict | None:
        with connect(self.path) as db:
            row = db.execute(
                """SELECT r.* FROM refund_records AS r JOIN orders AS o ON o.id = r.order_id
                   WHERE r.order_id = ? AND o.customer_id = ?""",
                (order_id, self.account_id),
            ).fetchone()
            return dict(row) if row else None
