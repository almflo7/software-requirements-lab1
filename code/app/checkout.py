"""结算确认、库存占用和订单快照。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.db import run_write
from app.audit import audit_failures, write_audit_log
from app.models import (CheckoutItem, CreatedOrder, CustomerAddress, Quote,
                        Store, from_row)
from app.map_points import delivery_fee


class CheckoutError(ValueError):
    """结算条件不满足。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _price_details(connection, store_id: str, address_id: str,
                   coupon_id: str | None, food: int):
    store = connection.execute("SELECT store_point_id FROM stores WHERE id = ?", (store_id,)).fetchone()
    address = connection.execute("SELECT pickup_point_id FROM addresses WHERE id = ?", (address_id,)).fetchone()
    point_snapshot = None
    pickup = connection.execute("SELECT * FROM map_points WHERE id = ? AND kind = 'pickup' AND active = 1",
                                (address["pickup_point_id"],)).fetchone()
    origin = connection.execute("SELECT * FROM map_points WHERE id = ? AND kind = 'store' AND active = 1",
                                (store["store_point_id"],)).fetchone()
    if not pickup or not origin:
        raise CheckoutError("请先选择开放取件点，并等待商家取餐点审核通过")
    distance, fee = delivery_fee(origin, pickup)
    if fee is None:
        raise CheckoutError("取件点超过店铺5公里配送范围")
    point_snapshot = (pickup["name"], pickup["x"], pickup["y"],
                      origin["name"], origin["x"], origin["y"], distance)
    discount = 0
    if coupon_id:
        coupon = connection.execute("SELECT amount_cents FROM coupons WHERE id = ? AND active = 1",
                                    (coupon_id,)).fetchone()
        if not coupon:
            raise CheckoutError("优惠券不存在或已停用")
        discount = min(food, coupon["amount_cents"])
    return fee, discount, point_snapshot


def _checkout_data(connection, customer_id: str, cart_id: str, address_id: str):
    account = connection.execute(
        "SELECT role, status FROM accounts WHERE id = ?", (customer_id,)
    ).fetchone()
    if not account or account["role"] != "customer" or account["status"] != "active":
        raise CheckoutError("顾客账号不可下单")
    cart = connection.execute(
        "SELECT * FROM carts WHERE id = ? AND customer_id = ?", (cart_id, customer_id)
    ).fetchone()
    if not cart:
        raise CheckoutError("购物车不存在或不属于该顾客")
    store = connection.execute(
        "SELECT * FROM stores WHERE id = ?", (cart["store_id"],)
    ).fetchone()
    merchant = connection.execute(
        "SELECT status FROM accounts WHERE id = ?", (store["merchant_id"],)
    ).fetchone()
    if store["status"] != "open" or merchant["status"] != "active":
        raise CheckoutError("店铺当前不能接新单")
    address = connection.execute(
        "SELECT * FROM addresses WHERE id = ? AND customer_id = ?",
        (address_id, customer_id),
    ).fetchone()
    if not address or not all(address[key].strip() for key in ("recipient", "phone", "address_text")):
        raise CheckoutError("收货地址不完整或不属于该顾客")
    if not address["pickup_point_id"]:
        raise CheckoutError("旧地址须重新选择开放取件点")
    items = connection.execute(
        """SELECT ci.item_id, ci.quantity, ci.last_shown_price_cents,
                  mi.name, mi.price_cents, mi.available_quantity, mi.status,
                  mi.store_id
           FROM cart_items AS ci JOIN menu_items AS mi ON mi.id = ci.item_id
           WHERE ci.cart_id = ? ORDER BY ci.item_id""",
        (cart_id,),
    ).fetchall()
    if not items:
        raise CheckoutError("购物车为空")
    for item in items:
        if item["store_id"] != store["id"] or item["status"] != "listed":
            raise CheckoutError("购物车包含其他店铺或已下架菜品")
        if item["available_quantity"] < item["quantity"]:
            raise CheckoutError("库存不足")
    return (from_row(store, Store), from_row(address, CustomerAddress),
            [from_row(item, CheckoutItem) for item in items])


def _fingerprint(store, address, items, coupon_id=None, fee=None, discount=0, points=None) -> str:
    payload = [
        store["id"], store["name"], store["delivery_fee_cents"],
        address["recipient"], address["phone"], address["address_text"],
        [(item["item_id"], item["name"], item["price_cents"], item["quantity"])
         for item in items],
        coupon_id, fee, discount, points,
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@audit_failures("confirm_checkout", "customer_id")
def confirm_checkout(
    path: str | Path, customer_id: str, cart_id: str, address_id: str,
    accept_price_change: bool = False, expected_fingerprint: str | None = None,
    coupon_id: str | None = None,
) -> Quote:
    """返回当前报价；改价时须显式接受才能获得确认标识。"""
    def operation(connection):
        store, address, items = _checkout_data(connection, customer_id, cart_id, address_id)
        changed = any(item["price_cents"] != item["last_shown_price_cents"] for item in items)
        food = sum(item["price_cents"] * item["quantity"] for item in items)
        fee, discount, points = _price_details(connection, store["id"], address["id"],
                                                coupon_id, food)
        fingerprint = _fingerprint(store, address, items, coupon_id, fee, discount, points)
        if changed and not accept_price_change:
            return Quote(food, fee, food - discount + fee, True, fingerprint, None, discount)
        if changed and expected_fingerprint != fingerprint:
            raise CheckoutError("报价再次变化，请重新查看并确认")
        confirmation_id = uuid4().hex
        connection.execute(
            """INSERT INTO checkout_confirmations
               (id, cart_id, customer_id, address_id, fingerprint, confirmed_at, coupon_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (confirmation_id, cart_id, customer_id, address_id,
             fingerprint, _now().isoformat(), coupon_id),
        )
        connection.executemany(
            "UPDATE cart_items SET last_shown_price_cents = ? WHERE cart_id = ? AND item_id = ?",
            [(item["price_cents"], cart_id, item["item_id"]) for item in items],
        )
        return Quote(food, fee, food - discount + fee, changed, fingerprint, confirmation_id, discount)
    return run_write(path, operation)


@audit_failures("create_order", "customer_id")
def create_order(path: str | Path, customer_id: str, confirmation_id: str, note: str = "") -> CreatedOrder:
    """同一确认只建一单；库存、快照和超时任务原子提交。"""
    def operation(connection):
        confirmation = connection.execute(
            "SELECT * FROM checkout_confirmations WHERE id = ? AND customer_id = ?",
            (confirmation_id, customer_id),
        ).fetchone()
        if not confirmation:
            raise CheckoutError("结算确认不存在或不属于该顾客")
        if confirmation["order_id"]:
            order = connection.execute(
                "SELECT id, status, total_amount_cents FROM orders WHERE id = ?",
                (confirmation["order_id"],),
            ).fetchone()
            return CreatedOrder(order["id"], order["status"], order["total_amount_cents"], True)
        store, address, items = _checkout_data(
            connection, customer_id, confirmation["cart_id"], confirmation["address_id"]
        )
        food = sum(item["price_cents"] * item["quantity"] for item in items)
        fee, discount, points = _price_details(connection, store["id"], address["id"],
            confirmation["coupon_id"], food)
        if confirmation["fingerprint"] != _fingerprint(
                store, address, items, confirmation["coupon_id"], fee, discount, points):
            raise CheckoutError("结算信息已变化，请重新确认")
        order_id = uuid4().hex
        placed_at = _now()
        for item in items:
            result = connection.execute(
                """UPDATE menu_items SET available_quantity = available_quantity - ?
                   WHERE id = ? AND available_quantity >= ? AND status = 'listed'""",
                (item["quantity"], item["item_id"], item["quantity"]),
            )
            if result.rowcount != 1:
                raise CheckoutError("库存不足")
        connection.execute(
            """INSERT INTO orders
               (id, customer_id, store_id, store_name_snapshot, recipient_snapshot,
                phone_snapshot, address_snapshot, status, food_amount_cents,
                delivery_fee_cents, total_amount_cents, note, placed_at,
                coupon_id, coupon_discount_cents, pickup_point_name_snapshot,
                pickup_x_snapshot, pickup_y_snapshot, store_point_name_snapshot,
                store_x_snapshot, store_y_snapshot, distance_km_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_payment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, customer_id, store["id"], store["name"], address["recipient"],
             address["phone"], address["address_text"], food, fee, food - discount + fee,
             note, placed_at.isoformat(), confirmation["coupon_id"], discount,
             *(points or (None,) * 7)),
        )
        for item in items:
            connection.execute(
                """INSERT INTO order_items
                   (order_id, item_id, item_name_snapshot, unit_price_cents, quantity, subtotal_cents)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (order_id, item["item_id"], item["name"], item["price_cents"],
                 item["quantity"], item["price_cents"] * item["quantity"]),
            )
            connection.execute(
                "INSERT INTO stock_reservations (order_id, item_id, quantity) VALUES (?, ?, ?)",
                (order_id, item["item_id"], item["quantity"]),
            )
        connection.execute(
            "UPDATE checkout_confirmations SET order_id = ? WHERE id = ?",
            (order_id, confirmation_id),
        )
        connection.execute(
            "INSERT INTO scheduled_jobs (id, kind, business_id, due_at, status) VALUES (?, ?, ?, ?, 'pending')",
            (uuid4().hex, "payment_timeout", order_id,
             (placed_at + timedelta(minutes=10)).isoformat()),
        )
        write_audit_log(connection, customer_id, order_id, "create_order", "success",
                        occurred_at=placed_at)
        return CreatedOrder(order_id, "pending_payment", food - discount + fee, False)
    return run_write(path, operation)
