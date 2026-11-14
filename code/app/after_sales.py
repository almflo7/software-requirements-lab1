"""分阶段取消与全额模拟退款。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.db import connect, run_write
from app.payment import PaymentError, _restore_stock, _time
from app.audit import audit_failures, write_audit_log


@dataclass(frozen=True)
class RefundOutcome:
    order_id: str
    result: str
    amount_cents: int
    failure_reason: str | None


@audit_failures("refund_result", order_field="order_id")
def apply_refund_result(
    path: str | Path, order_id: str, succeeded: bool,
    reason: str = "", now: datetime | None = None,
) -> RefundOutcome:
    """处理一次模拟退款结果；相同结果重放不产生新退款。"""
    moment = _time(now)
    target = "succeeded" if succeeded else "failed"

    def operation(connection):
        refund = connection.execute(
            "SELECT * FROM refund_records WHERE order_id = ?", (order_id,)
        ).fetchone()
        if not refund:
            raise PaymentError("该订单没有待处理退款")
        if refund["result"] != "pending":
            if refund["result"] != target:
                raise PaymentError("同一退款出现冲突结果")
            return RefundOutcome(order_id, target, refund["amount_cents"],
                                 refund["failure_reason"])
        if not succeeded and not reason.strip():
            raise PaymentError("退款失败必须记录原因")
        connection.execute(
            """UPDATE refund_records SET result = ?, result_at = ?, failure_reason = ?
               WHERE order_id = ? AND result = 'pending'""",
            (target, moment.isoformat(), None if succeeded else reason, order_id),
        )
        if not succeeded:
            connection.execute(
                """INSERT OR IGNORE INTO scheduled_jobs
                   (id, kind, business_id, due_at, status)
                   VALUES (?, 'refund_review', ?, ?, 'pending')""",
                (uuid4().hex, order_id,
                 (moment + timedelta(hours=23, minutes=50)).isoformat()),
            )
        write_audit_log(connection, None, order_id, "refund_result", target,
                        None if succeeded else reason, moment)
        return RefundOutcome(order_id, target, refund["amount_cents"],
                             None if succeeded else reason)

    return run_write(path, operation)


class CancellationError(ValueError):
    """取消阶段、权限或申请状态不满足。"""


def _refund_for_order(connection, order, now):
    payment = connection.execute(
        """SELECT request_id FROM payment_records WHERE order_id = ?
           AND result = 'succeeded' ORDER BY result_at LIMIT 1""",
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


@audit_failures("request_cancel", "customer_id", "order_id")
def request_cancellation(path: str | Path, customer_id: str, order_id: str,
                         reason: str, now: datetime | None = None) -> str:
    moment = _time(now)

    def operation(connection):
        customer = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (customer_id,),
        ).fetchone()
        if not customer or customer["role"] != "customer" or customer["status"] != "active":
            raise CancellationError("顾客账号不能发起取消")
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?", (order_id, customer_id)
        ).fetchone()
        if not order:
            raise CancellationError("订单不存在或不属于该顾客")
        status = order["status"]
        if status in ("pending_payment", "awaiting_merchant"):
            connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            _restore_stock(connection, order_id)
            _refund_for_order(connection, order, moment)
            write_audit_log(connection, customer_id, order_id, "cancel_order",
                            "success", reason, moment)
            return "cancelled"
        if status not in ("preparing", "awaiting_assignment", "awaiting_courier", "awaiting_pickup"):
            raise CancellationError("当前阶段不能普通取消")
        if not reason.strip():
            raise CancellationError("取消申请必须说明原因")
        existing = connection.execute(
            "SELECT id FROM cancellation_requests WHERE order_id = ? AND status = 'pending'",
            (order_id,),
        ).fetchone()
        if existing:
            return existing["id"]
        request_id = uuid4().hex
        connection.execute(
            """INSERT INTO cancellation_requests
               (id, order_id, customer_id, reason, status, submitted_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (request_id, order_id, customer_id, reason.strip(), moment.isoformat()),
        )
        connection.execute(
            """INSERT INTO scheduled_jobs (id, kind, business_id, due_at, status)
               VALUES (?, 'cancel_escalation', ?, ?, 'pending')""",
            (uuid4().hex, request_id,
             (moment + timedelta(minutes=5)).isoformat()),
        )
        write_audit_log(connection, customer_id, order_id, "request_cancel",
                        "success", reason.strip(), moment)
        return request_id

    return run_write(path, operation)


@audit_failures("decide_cancel", "actor_id")
def decide_cancellation(path: str | Path, actor_id: str, request_id: str,
                        approve: bool, now: datetime | None = None) -> str:
    moment = _time(now)

    def operation(connection):
        request = connection.execute(
            "SELECT * FROM cancellation_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not request or request["status"] != "pending":
            raise CancellationError("取消申请不存在或已处理")
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (request["order_id"],)).fetchone()
        actor = connection.execute("SELECT role, status FROM accounts WHERE id = ?", (actor_id,)).fetchone()
        store = connection.execute("SELECT merchant_id FROM stores WHERE id = ?", (order["store_id"],)).fetchone()
        if not actor or actor["status"] != "active":
            raise CancellationError("处理人无权操作")
        is_merchant = actor["role"] == "merchant" and store["merchant_id"] == actor_id
        is_admin = actor["role"] == "admin"
        if not (is_merchant or is_admin):
            raise CancellationError("处理人无权操作")
        if is_admin and moment < datetime.fromisoformat(request["submitted_at"]) + timedelta(minutes=5):
            raise CancellationError("尚未到管理员接手时间")
        if order["status"] not in ("preparing", "awaiting_assignment", "awaiting_courier", "awaiting_pickup"):
            raise CancellationError("订单状态已变化")
        decision = "approved" if approve else "rejected"
        connection.execute(
            """UPDATE cancellation_requests SET status = ?, decided_by = ?, decided_at = ?
               WHERE id = ?""",
            (decision, actor_id, moment.isoformat(), request_id),
        )
        if approve:
            connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order["id"],))
            # 制作开始后，取消不自动恢复可售库存。
            _refund_for_order(connection, order, moment)
        write_audit_log(connection, actor_id, order["id"], "decide_cancel",
                        "success", decision, moment)
        return decision

    return run_write(path, operation)


@audit_failures("retry_refund", "admin_id", "order_id")
def retry_failed_refund(path: str | Path, admin_id: str, order_id: str,
                        now: datetime | None = None) -> str:
    """管理员在失败后重新提交同一笔退款，不生成第二笔记录。"""
    moment = _time(now)

    def operation(connection):
        admin = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,),
        ).fetchone()
        refund = connection.execute(
            "SELECT result FROM refund_records WHERE order_id = ?", (order_id,),
        ).fetchone()
        if not admin or admin["role"] != "admin" or admin["status"] != "active":
            raise CancellationError("只有管理员可重试失败退款")
        if not refund or refund["result"] != "failed":
            raise CancellationError("该订单没有失败退款")
        connection.execute(
            """UPDATE refund_records SET result = 'pending', failure_reason = NULL,
               result_at = NULL WHERE order_id = ?""",
            (order_id,),
        )
        connection.execute(
            """UPDATE scheduled_jobs SET status = 'pending', due_at = ?,
               claimed_by = NULL, lease_until = NULL
               WHERE kind = 'refund_settle' AND business_id = ?""",
            ((moment + timedelta(minutes=5)).isoformat(), order_id),
        )
        write_audit_log(connection, admin_id, order_id, "retry_refund",
                        "success", None, moment)
        return "pending"

    return run_write(path, operation)


def list_failed_refunds(path: str | Path, admin_id: str) -> list[dict]:
    """管理员查询待人工处理的退款及最迟处理时间。"""
    with connect(path) as connection:
        admin = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,),
        ).fetchone()
        if not admin or admin["role"] != "admin" or admin["status"] != "active":
            raise CancellationError("只有管理员可查询退款异常")
        rows = connection.execute(
            """SELECT r.order_id, r.amount_cents, r.failure_reason, r.result_at,
                      j.due_at AS review_due_at
               FROM refund_records AS r LEFT JOIN scheduled_jobs AS j
                 ON j.kind = 'refund_review' AND j.business_id = r.order_id
               WHERE r.result = 'failed' ORDER BY r.result_at"""
        ).fetchall()
        return [dict(row) | {"resolve_by": (
            datetime.fromisoformat(row["review_due_at"]) + timedelta(minutes=5)
            if row["review_due_at"] else
            datetime.fromisoformat(row["result_at"]) + timedelta(hours=24)
        ).isoformat()} for row in rows]
