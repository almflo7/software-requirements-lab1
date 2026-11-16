"""商家接单、拒单及接单超时。"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.db import run_write
from app.audit import audit_failures, write_audit_log
from app.payment import _restore_stock, _time


class FulfillmentError(ValueError):
    """商家操作条件不满足。"""


@dataclass(frozen=True)
class MerchantOutcome:
    order_id: str
    status: str
    refund_pending: bool


def _merchant_deadline(connection, order_id: str) -> datetime:
    job = connection.execute(
        "SELECT due_at FROM scheduled_jobs WHERE kind = 'merchant_timeout' AND business_id = ?",
        (order_id,),
    ).fetchone()
    if not job:
        raise FulfillmentError("商家接单期限不存在")
    return datetime.fromisoformat(job["due_at"])


def _merchant_cancel(connection, order, now: datetime) -> None:
    connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order["id"],))
    _restore_stock(connection, order["id"])
    payment = connection.execute(
        """SELECT request_id FROM payment_records
           WHERE order_id = ? AND result = 'succeeded' ORDER BY result_at LIMIT 1""",
        (order["id"],),
    ).fetchone()
    if payment:
        connection.execute(
            """INSERT OR IGNORE INTO refund_records
               (id, order_id, payment_request_id, amount_cents, result, initiated_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (uuid4().hex, order["id"], payment["request_id"],
             order["total_amount_cents"], now.isoformat()),
        )


@audit_failures("merchant_response", "merchant_id", "order_id")
def respond_merchant(
    path: str | Path, merchant_id: str, order_id: str,
    accept: bool, reason: str = "", now: datetime | None = None,
) -> MerchantOutcome:
    """商家在付款成功后五分钟内接单或说明原因拒单。"""
    moment = _time(now)

    def operation(connection):
        order = connection.execute(
            """SELECT orders.* FROM orders JOIN stores ON stores.id = orders.store_id
               WHERE orders.id = ? AND stores.merchant_id = ?""",
            (order_id, merchant_id),
        ).fetchone()
        merchant = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (merchant_id,)
        ).fetchone()
        if not order or not merchant or merchant["role"] != "merchant" or merchant["status"] != "active":
            raise FulfillmentError("无权处理该订单")
        if order["status"] != "awaiting_merchant":
            raise FulfillmentError("订单当前不待商家接单")
        if moment >= _merchant_deadline(connection, order_id):
            raise FulfillmentError("商家接单已超时，请处理到期任务")
        if accept:
            connection.execute(
                """INSERT INTO merchant_responses
                   (order_id, merchant_id, decision, responded_at)
                   VALUES (?, ?, 'accepted', ?)""",
                (order_id, merchant_id, moment.isoformat()),
            )
            connection.execute("UPDATE orders SET status = 'preparing' WHERE id = ?", (order_id,))
            write_audit_log(connection, merchant_id, order_id, "merchant_accept", "success",
                            occurred_at=moment)
            return MerchantOutcome(order_id, "preparing", False)
        if not reason.strip():
            raise FulfillmentError("拒单必须说明原因")
        connection.execute(
            """INSERT INTO merchant_responses
               (order_id, merchant_id, decision, reason, responded_at)
               VALUES (?, ?, 'rejected', ?, ?)""",
            (order_id, merchant_id, reason.strip(), moment.isoformat()),
        )
        _merchant_cancel(connection, order, moment)
        write_audit_log(connection, merchant_id, order_id, "merchant_reject", "success",
                        reason.strip(), moment)
        return MerchantOutcome(order_id, "cancelled", True)

    return run_write(path, operation)


@audit_failures("finish_preparation", "merchant_id", "order_id")
def finish_preparation(path: str | Path, merchant_id: str, order_id: str) -> MerchantOutcome:
    """本店商家确认备餐完成，订单进入待人工派单。"""
    def operation(connection):
        merchant = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (merchant_id,)
        ).fetchone()
        order = connection.execute(
            """SELECT orders.* FROM orders JOIN stores ON stores.id = orders.store_id
               WHERE orders.id = ? AND stores.merchant_id = ?""",
            (order_id, merchant_id),
        ).fetchone()
        if not merchant or merchant["role"] != "merchant" or merchant["status"] != "active" or not order:
            raise FulfillmentError("无权处理该订单")
        if order["status"] != "preparing":
            raise FulfillmentError("订单当前不在制作中")
        connection.execute(
            "UPDATE orders SET status = 'awaiting_assignment' WHERE id = ?", (order_id,)
        )
        return MerchantOutcome(order_id, "awaiting_assignment", False)

    return run_write(path, operation)
