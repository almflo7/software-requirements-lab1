"""订单查询权限和收货信息脱敏。"""

from pathlib import Path

from app.db import connect
from app.models import DeliveryInfoSnapshot, Order, from_row


class AccessError(ValueError):
    """无权查看业务记录。"""


def mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return "****"
    return phone[:3] + "****" + phone[-4:]


def view_order(path: str | Path, account_id: str, order_id: str,
               detail: bool = False) -> dict:
    """按当前责任和订单阶段决定可见范围。"""
    with connect(path) as connection:
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        order_row = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not actor or not order_row:
            raise AccessError("账号或订单不存在")
        order = from_row(order_row, Order)
        delivery_info = DeliveryInfoSnapshot.from_order(order)
        store = connection.execute(
            "SELECT merchant_id FROM stores WHERE id = ?", (order["store_id"],)
        ).fetchone()
        task = connection.execute(
            "SELECT courier_id FROM delivery_tasks WHERE order_id = ?", (order_id,)
        ).fetchone()
        role = actor["role"]
        own = role == "customer" and order["customer_id"] == account_id
        merchant = role == "merchant" and store["merchant_id"] == account_id
        courier = role == "courier" and task and task["courier_id"] == account_id
        admin = role == "admin" and actor["status"] == "active"
        if not (own or merchant or courier or admin):
            raise AccessError("无权查看该订单")
        active = order["status"] not in ("cancelled", "completed", "delivered")
        full = own or (detail and active and actor["status"] == "active"
                       and (merchant or courier or admin))
        refund = connection.execute(
            """SELECT result, amount_cents, failure_reason, initiated_at, result_at
               FROM refund_records WHERE order_id = ?""", (order_id,),
        ).fetchone()
        cancellation = connection.execute(
            """SELECT status, reason, decided_at FROM cancellation_requests
               WHERE order_id = ? ORDER BY submitted_at DESC LIMIT 1""", (order_id,),
        ).fetchone()
        merchant_response = connection.execute(
            "SELECT decision, reason FROM merchant_responses WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        delivery_exception = connection.execute(
            """SELECT e.reason, e.status, e.resolved_action, e.reported_at, e.decided_at
               FROM delivery_exceptions AS e JOIN delivery_tasks AS t ON t.id = e.task_id
               WHERE t.order_id = ? ORDER BY e.reported_at DESC LIMIT 1""",
            (order_id,),
        ).fetchone()
        return {
            "order_id": order_id,
            "status": order["status"],
            "total_amount_cents": order["total_amount_cents"],
            "coupon_discount_cents": order_row["coupon_discount_cents"],
            "delivery_fee_cents": order_row["delivery_fee_cents"],
            "pickup_point": order_row["pickup_point_name_snapshot"] if full else None,
            "pickup_coordinates": ((order_row["pickup_x_snapshot"], order_row["pickup_y_snapshot"])
                                   if full and order_row["pickup_x_snapshot"] is not None else None),
            "store_point": order_row["store_point_name_snapshot"] if full else None,
            "store_coordinates": ((order_row["store_x_snapshot"], order_row["store_y_snapshot"])
                                  if full and order_row["store_x_snapshot"] is not None else None),
            "distance_km": order_row["distance_km_snapshot"] if full else None,
            "recipient": delivery_info.recipient if full else "***",
            "phone": delivery_info.phone if full else mask_phone(delivery_info.phone),
            "address": delivery_info.address_text if full else "***",
            "refund": dict(refund) if refund else None,
            "cancellation": dict(cancellation) if cancellation else None,
            "merchant_response": dict(merchant_response) if merchant_response else None,
            "delivery_exception": (dict(delivery_exception)
                                   if delivery_exception else None),
        }
