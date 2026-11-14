"""模拟付款请求、结果去重和迟到成功处理。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.db import run_write
from app.audit import audit_failures, write_audit_log


class PaymentError(ValueError):
    """付款状态或权限不满足。"""


@dataclass(frozen=True)
class PaymentOutcome:
    request_id: str
    payment_result: str
    order_status: str
    refund_pending: bool


def _time(value: datetime | None) -> datetime:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return moment.astimezone(timezone.utc)


def _deadline(order) -> datetime:
    return datetime.fromisoformat(order["placed_at"]) + timedelta(minutes=10)


def _restore_stock(connection, order_id: str) -> None:
    reservations = connection.execute(
        "SELECT item_id, quantity FROM stock_reservations WHERE order_id = ? AND restored = 0",
        (order_id,),
    ).fetchall()
    for reservation in reservations:
        updated = connection.execute(
            """UPDATE stock_reservations SET restored = 1
               WHERE order_id = ? AND item_id = ? AND restored = 0""",
            (order_id, reservation["item_id"]),
        )
        if updated.rowcount == 1:
            connection.execute(
                "UPDATE menu_items SET available_quantity = available_quantity + ? WHERE id = ?",
                (reservation["quantity"], reservation["item_id"]),
            )


def _expire(connection, order, now: datetime) -> bool:
    if order["status"] != "pending_payment" or now < _deadline(order):
        return False
    connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order["id"],))
    _restore_stock(connection, order["id"])
    return True


@audit_failures("start_payment", "customer_id", "order_id")
def start_payment(
    path: str | Path, customer_id: str, order_id: str,
    request_id: str, now: datetime | None = None,
) -> PaymentOutcome:
    """同一请求返回原结果；一笔订单同时只允许一个未决请求。"""
    moment = _time(now)

    def operation(connection):
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order or order["customer_id"] != customer_id:
            raise PaymentError("订单不存在或不属于该顾客")
        old = connection.execute(
            "SELECT * FROM payment_records WHERE request_id = ?", (request_id,)
        ).fetchone()
        if old:
            if old["order_id"] != order_id:
                raise PaymentError("付款请求标识已被其他订单使用")
            return PaymentOutcome(request_id, old["result"], order["status"], False)
        if moment >= _deadline(order):
            raise PaymentError("付款期限已过，请处理到期任务")
        if order["status"] != "pending_payment":
            raise PaymentError("订单当前不可付款")
        pending = connection.execute(
            "SELECT request_id FROM payment_records WHERE order_id = ? AND result = 'pending'",
            (order_id,),
        ).fetchone()
        if pending:
            raise PaymentError("支付结果确认中，请勿重复付款")
        connection.execute(
            """INSERT INTO payment_records
               (request_id, order_id, result, requested_at) VALUES (?, ?, 'pending', ?)""",
            (request_id, order_id, moment.isoformat()),
        )
        return PaymentOutcome(request_id, "pending", "pending_payment", False)

    return run_write(path, operation)


@audit_failures("payment_result")
def apply_payment_result(
    path: str | Path, request_id: str, succeeded: bool,
    reason: str = "", now: datetime | None = None,
) -> PaymentOutcome:
    """模拟支付回调；重复结果只返回已经保存的处理结果。"""
    moment = _time(now)
    result = "succeeded" if succeeded else "failed"

    def operation(connection):
        payment = connection.execute(
            "SELECT * FROM payment_records WHERE request_id = ?", (request_id,)
        ).fetchone()
        if not payment:
            raise PaymentError("付款请求不存在")
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (payment["order_id"],)
        ).fetchone()
        if payment["result"] != "pending":
            if payment["result"] != result:
                raise PaymentError("同一付款请求出现冲突结果")
            refund = connection.execute(
                "SELECT id FROM refund_records WHERE order_id = ?", (order["id"],)
            ).fetchone()
            return PaymentOutcome(request_id, result, order["status"], bool(refund))
        expired = _expire(connection, order, moment)
        connection.execute(
            """UPDATE payment_records SET result = ?, failure_reason = ?, result_at = ?
               WHERE request_id = ? AND result = 'pending'""",
            (result, reason if not succeeded else None, moment.isoformat(), request_id),
        )
        write_audit_log(connection, order["customer_id"], order["id"],
                        "payment_result", result, reason or None, moment)
        if succeeded and not expired and order["status"] == "pending_payment":
            connection.execute(
                "UPDATE orders SET status = 'awaiting_merchant' WHERE id = ?", (order["id"],)
            )
            connection.execute(
                """INSERT OR IGNORE INTO scheduled_jobs
                   (id, kind, business_id, due_at, status)
                   VALUES (?, 'merchant_timeout', ?, ?, 'pending')""",
                (uuid4().hex, order["id"], (moment + timedelta(minutes=5)).isoformat()),
            )
            return PaymentOutcome(request_id, result, "awaiting_merchant", False)
        if succeeded and (expired or order["status"] == "cancelled"):
            connection.execute(
                """INSERT OR IGNORE INTO refund_records
                   (id, order_id, payment_request_id, amount_cents, result, initiated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (uuid4().hex, order["id"], request_id,
                 order["total_amount_cents"], moment.isoformat()),
            )
            return PaymentOutcome(request_id, result, "cancelled", True)
        return PaymentOutcome(request_id, result,
                              "cancelled" if expired else order["status"], False)

    return run_write(path, operation)
