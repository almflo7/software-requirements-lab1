"""店铺菜品查询与商家维护。"""

from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import json

from app.db import connect, run_write
from app.audit import audit_failures, write_audit_log


class CatalogError(ValueError):
    """店铺或菜品操作无效。"""


def search_catalog(path: str | Path, keyword: str = "", category: str = "",
                   min_price_cents: int = 0, max_price_cents: int | None = None) -> list[dict]:
    if min_price_cents < 0 or (max_price_cents is not None and max_price_cents < min_price_cents):
        raise CatalogError("价格区间无效")
    with connect(path) as connection:
        rows = connection.execute(
            """SELECT m.id, m.name, m.category, m.price_cents, m.available_quantity,
                      m.status, (m.available_quantity = 0) AS sold_out,
                      s.id AS store_id, s.name AS store_name, s.status AS store_status,
                      s.description, s.business_hours, s.delivery_fee_cents
               FROM menu_items AS m JOIN stores AS s ON s.id = m.store_id
               WHERE (m.name LIKE ? OR s.name LIKE ?) AND (? = '' OR m.category = ?)
                 AND m.price_cents >= ? AND (? IS NULL OR m.price_cents <= ?)
               ORDER BY s.name, m.name""",
            (f"%{keyword}%", f"%{keyword}%", category, category,
             min_price_cents, max_price_cents, max_price_cents),
        ).fetchall()
        return [dict(row) for row in rows]


@audit_failures("add_store", "merchant_id")
def add_store(path: str | Path, merchant_id: str, name: str,
              delivery_areas: tuple[str, ...], delivery_fee_cents: int,
              description: str = "", business_hours: str = "") -> str:
    """审核通过的商家创建单店配送范围和固定配送费。"""
    if (not name.strip() or not delivery_areas
            or any(not area.strip() for area in delivery_areas)
            or delivery_fee_cents < 0):
        raise CatalogError("店铺参数无效")

    def operation(connection):
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (merchant_id,),
        ).fetchone()
        if not actor or actor["role"] != "merchant" or actor["status"] != "active":
            raise CatalogError("商家账号不可创建店铺")
        store_id = uuid4().hex
        connection.execute(
            """INSERT INTO stores
               (id, merchant_id, name, status, delivery_areas, delivery_fee_cents,
                description, business_hours) VALUES (?, ?, ?, 'open', ?, ?, ?, ?)""",
            (store_id, merchant_id, name.strip(), json.dumps(delivery_areas),
             delivery_fee_cents, description.strip(), business_hours.strip()),
        )
        write_audit_log(connection, merchant_id, None, "add_store", "success",
                        store_id, store_id=store_id)
        return store_id

    return run_write(path, operation)


@audit_failures("create_cart", "customer_id")
def create_cart(path: str | Path, customer_id: str, store_id: str) -> str:
    """同一顾客与店铺复用一辆购物车。"""
    def operation(connection):
        customer = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (customer_id,),
        ).fetchone()
        store = connection.execute(
            "SELECT status FROM stores WHERE id = ?", (store_id,),
        ).fetchone()
        if not customer or customer["role"] != "customer" or customer["status"] != "active":
            raise CatalogError("顾客账号不可创建购物车")
        if not store or store["status"] != "open":
            raise CatalogError("店铺未营业")
        existing = connection.execute(
            "SELECT id FROM carts WHERE customer_id = ? AND store_id = ?",
            (customer_id, store_id),
        ).fetchone()
        if existing:
            return existing["id"]
        cart_id = uuid4().hex
        connection.execute("INSERT INTO carts VALUES (?, ?, ?)",
                           (cart_id, customer_id, store_id))
        write_audit_log(connection, customer_id, None, "create_cart", "success", cart_id)
        return cart_id

    return run_write(path, operation)


@audit_failures("update_menu_item", "merchant_id")
def update_menu_item(path: str | Path, merchant_id: str, item_id: str,
                     price_cents: int, quantity: int, status: str,
                     name: str | None = None, category: str | None = None) -> None:
    if (price_cents < 0 or quantity < 0 or status not in ("listed", "unlisted")
            or (name is not None and not name.strip())
            or (category is not None and not category.strip())):
        raise CatalogError("菜品参数无效")

    def operation(connection):
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (merchant_id,)
        ).fetchone()
        item = connection.execute(
            """SELECT m.id, m.store_id FROM menu_items AS m JOIN stores AS s ON s.id = m.store_id
               WHERE m.id = ? AND s.merchant_id = ?""", (item_id, merchant_id),
        ).fetchone()
        if not actor or actor["role"] != "merchant" or actor["status"] != "active" or not item:
            raise CatalogError("无权修改该菜品")
        connection.execute(
            """UPDATE menu_items SET price_cents = ?, available_quantity = ?, status = ?,
               name = COALESCE(?, name), category = COALESCE(?, category)
               WHERE id = ?""",
            (price_cents, quantity, status,
             name.strip() if name is not None else None,
             category.strip() if category is not None else None, item_id),
        )
        write_audit_log(connection, merchant_id, None, "update_menu_item", "success",
                        item_id, store_id=item["store_id"])

    run_write(path, operation)


@audit_failures("update_store", "merchant_id")
def update_store(path: str | Path, merchant_id: str, store_id: str,
                 name: str, status: str, delivery_fee_cents: int,
                 description: str | None = None, business_hours: str | None = None,
                 delivery_areas: tuple[str, ...] | None = None) -> None:
    if not name.strip() or status not in ("open", "paused") or delivery_fee_cents < 0:
        raise CatalogError("店铺参数无效")

    def operation(connection):
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (merchant_id,)
        ).fetchone()
        store = connection.execute(
            "SELECT merchant_id, status FROM stores WHERE id = ?", (store_id,)
        ).fetchone()
        if not actor or actor["role"] != "merchant" or actor["status"] != "active" or not store or store["merchant_id"] != merchant_id or store["status"] == "disabled":
            raise CatalogError("无权修改该店铺")
        if delivery_areas is not None and (not delivery_areas or any(not area.strip() for area in delivery_areas)):
            raise CatalogError("配送区域不能为空")
        areas = json.dumps(delivery_areas) if delivery_areas is not None else None
        connection.execute(
            """UPDATE stores SET name = ?, status = ?, delivery_fee_cents = ?,
               description = COALESCE(?, description),
               business_hours = COALESCE(?, business_hours),
               delivery_areas = COALESCE(?, delivery_areas)
               WHERE id = ?""",
            (name.strip(), status, delivery_fee_cents,
             description.strip() if description is not None else None,
             business_hours.strip() if business_hours is not None else None,
             areas, store_id),
        )
        write_audit_log(connection, merchant_id, None, "update_store", "success",
                        store_id, store_id=store_id)

    run_write(path, operation)


@audit_failures("review_account", "admin_id")
def review_account(path: str | Path, admin_id: str, request_id: str,
                   approve: bool) -> str:
    """审核商家或配送员申请。"""
    def operation(connection):
        admin = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,)
        ).fetchone()
        request = connection.execute(
            "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not admin or admin["role"] != "admin" or admin["status"] != "active":
            raise CatalogError("只有管理员可审核")
        if not request or request["status"] != "pending":
            raise CatalogError("申请不存在或已处理")
        account = connection.execute(
            "SELECT role FROM accounts WHERE id = ?", (request["account_id"],)
        ).fetchone()
        if not account or account["role"] not in ("merchant", "courier"):
            raise CatalogError("申请角色无效")
        decision = "approved" if approve else "rejected"
        connection.execute(
            """UPDATE approval_requests SET status = ?, reviewed_by = ?, reviewed_at = ?
               WHERE id = ?""",
            (decision, admin_id, datetime.now(timezone.utc).isoformat(), request_id),
        )
        connection.execute(
            "UPDATE accounts SET status = ? WHERE id = ?",
            ("active" if approve else "disabled", request["account_id"]),
        )
        write_audit_log(connection, admin_id, None, "review_account", "success",
                        f"{request['account_id']}: {decision}")
        return decision

    return run_write(path, operation)


@audit_failures("add_menu_item", "merchant_id")
def add_menu_item(path: str | Path, merchant_id: str, store_id: str,
                  name: str, category: str, price_cents: int, quantity: int) -> str:
    if not name.strip() or not category.strip() or price_cents < 0 or quantity < 0:
        raise CatalogError("菜品参数无效")

    def operation(connection):
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (merchant_id,)
        ).fetchone()
        store = connection.execute(
            "SELECT merchant_id, status FROM stores WHERE id = ?", (store_id,)
        ).fetchone()
        if not actor or actor["role"] != "merchant" or actor["status"] != "active" or not store or store["merchant_id"] != merchant_id or store["status"] == "disabled":
            raise CatalogError("无权为该店铺新增菜品")
        item_id = uuid4().hex
        connection.execute(
            """INSERT INTO menu_items
               (id, store_id, name, category, price_cents, available_quantity, status)
               VALUES (?, ?, ?, ?, ?, ?, 'listed')""",
            (item_id, store_id, name.strip(), category.strip(), price_cents, quantity),
        )
        write_audit_log(connection, merchant_id, None, "add_menu_item", "success",
                        item_id, store_id=store_id)
        return item_id

    return run_write(path, operation)


@audit_failures("change_cart", "customer_id")
def change_cart_quantity(path: str | Path, customer_id: str, cart_id: str,
                         item_id: str, quantity: int) -> None:
    if quantity < 0:
        raise CatalogError("购物车数量不能为负")

    def operation(connection):
        customer = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (customer_id,),
        ).fetchone()
        if not customer or customer["role"] != "customer" or customer["status"] != "active":
            raise CatalogError("顾客账号不可修改购物车")
        cart = connection.execute(
            "SELECT store_id FROM carts WHERE id = ? AND customer_id = ?", (cart_id, customer_id),
        ).fetchone()
        item = connection.execute(
            "SELECT store_id, price_cents, status FROM menu_items WHERE id = ?", (item_id,),
        ).fetchone()
        if not cart or not item or cart["store_id"] != item["store_id"]:
            raise CatalogError("购物车或菜品无效")
        if quantity == 0:
            connection.execute("DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?", (cart_id, item_id))
            write_audit_log(connection, customer_id, None, "change_cart", "success",
                            f"{item_id}: 0")
            return
        if item["status"] != "listed":
            raise CatalogError("已下架菜品不能加入购物车")
        connection.execute(
            """INSERT INTO cart_items (cart_id, item_id, quantity, last_shown_price_cents)
               VALUES (?, ?, ?, ?) ON CONFLICT(cart_id, item_id)
               DO UPDATE SET quantity = excluded.quantity""",
            (cart_id, item_id, quantity, item["price_cents"]),
        )
        write_audit_log(connection, customer_id, None, "change_cart", "success",
                        f"{item_id}: {quantity}")

    run_write(path, operation)
