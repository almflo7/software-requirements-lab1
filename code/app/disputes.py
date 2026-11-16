"""订单争议及管理员查看留痕。"""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.audit import audit_failures, write_audit_log
from app.db import run_write
from app.models import DisputeCase, from_row
from app.order_state import transition_order


class DisputeError(ValueError):
    """争议操作无权或状态不满足。"""


@audit_failures("open_dispute", "actor_id", "order_id")
def open_dispute(path: str | Path, actor_id: str, order_id: str, reason: str) -> str:
    if not reason.strip():
        raise DisputeError("争议原因不能为空")

    def operation(connection):
        order = connection.execute(
            "SELECT customer_id, store_id FROM orders WHERE id = ?", (order_id,),
        ).fetchone()
        if not order:
            raise DisputeError("订单不存在")
        store = connection.execute("SELECT merchant_id FROM stores WHERE id = ?", (order["store_id"],)).fetchone()
        task = connection.execute("SELECT courier_id FROM delivery_tasks WHERE order_id = ?", (order_id,)).fetchone()
        if actor_id not in (order["customer_id"], store["merchant_id"],
                            task["courier_id"] if task else None):
            raise DisputeError("无权提出该订单争议")
        dispute_id = uuid4().hex
        now = datetime.now(timezone.utc)
        connection.execute(
            """INSERT INTO dispute_cases (id, order_id, opened_by, reason, status, opened_at)
               VALUES (?, ?, ?, ?, 'open', ?)""",
            (dispute_id, order_id, actor_id, reason.strip(), now.isoformat()),
        )
        write_audit_log(connection, actor_id, order_id, "open_dispute", "success",
                        occurred_at=now)
        return dispute_id

    return run_write(path, operation)


@audit_failures("view_dispute", "admin_id")
def view_dispute(path: str | Path, admin_id: str, dispute_id: str) -> dict:
    """管理员查看争议信息时写入审计日志。"""
    def operation(connection):
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,),
        ).fetchone()
        dispute = connection.execute(
            "SELECT * FROM dispute_cases WHERE id = ?", (dispute_id,),
        ).fetchone()
        if not actor or actor["role"] != "admin" or actor["status"] != "active" or not dispute:
            raise DisputeError("无权查看争议")
        write_audit_log(connection, admin_id, dispute["order_id"], "view_dispute", "success")
        return asdict(from_row(dispute, DisputeCase))

    return run_write(path, operation)


@audit_failures("resolve_dispute", "admin_id")
def resolve_dispute(path: str | Path, admin_id: str, dispute_id: str,
                    decision: str, refund_full: bool = False) -> None:
    if not decision.strip():
        raise DisputeError("争议决定不能为空")

    def operation(connection):
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,),
        ).fetchone()
        dispute = connection.execute(
            "SELECT order_id, status FROM dispute_cases WHERE id = ?", (dispute_id,),
        ).fetchone()
        if not actor or actor["role"] != "admin" or actor["status"] != "active":
            raise DisputeError("只有管理员可处理争议")
        if not dispute or dispute["status"] != "open":
            raise DisputeError("争议不存在或已处理")
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (dispute["order_id"],)
        ).fetchone()
        if refund_full:
            if order["status"] not in ("delivered", "completed", "cancelled"):
                raise DisputeError("只有已送达、已完成或已取消订单可由争议处理发起退款")
            payment = connection.execute(
                """SELECT 1 FROM payment_records WHERE order_id = ?
                   AND result = 'succeeded' LIMIT 1""", (order["id"],)
            ).fetchone()
            if not payment:
                raise DisputeError("订单没有成功付款，不能退款")
        now = datetime.now(timezone.utc)
        if refund_full:
            from app.after_sales import _refund_for_order
            _refund_for_order(connection, order, now)
            if order["status"] == "delivered":
                transition_order(connection, order["id"], "delivered", "cancelled")
                connection.execute("""UPDATE scheduled_jobs SET status = 'completed'
                                   WHERE kind = 'auto_complete' AND business_id = ?
                                     AND status = 'pending'""", (order["id"],))
        connection.execute(
            """UPDATE dispute_cases SET status = 'resolved', decided_by = ?,
               decision = ?, decided_at = ? WHERE id = ?""",
            (admin_id, decision.strip(), now.isoformat(), dispute_id),
        )
        write_audit_log(connection, admin_id, dispute["order_id"], "resolve_dispute",
                        "success", "full_refund" if refund_full else decision.strip(), now)

    run_write(path, operation)
