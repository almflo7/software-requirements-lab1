"""带租约的到期工作处理与中断恢复。"""

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.db import run_write
from app.fulfillment import _merchant_cancel
from app.payment import _expire, _time


def run_due_jobs(path: str | Path, now: datetime | None = None) -> int:
    """先原子领取，到期工作失败或中断后由租约恢复。"""
    moment = _time(now)

    def claim(connection):
        job = connection.execute(
            """SELECT id, kind, business_id FROM scheduled_jobs
               WHERE julianday(due_at) <= julianday(?) AND (status = 'pending'
                   OR (status = 'claimed' AND julianday(lease_until) <= julianday(?)))
               ORDER BY due_at, id LIMIT 1""",
            (moment.isoformat(), moment.isoformat()),
        ).fetchone()
        if not job:
            return None
        claimant = uuid4().hex
        connection.execute(
            """UPDATE scheduled_jobs SET status = 'claimed', claimed_by = ?, lease_until = ?
               WHERE id = ?""",
            (claimant, (moment + timedelta(seconds=5)).isoformat(), job["id"]),
        )
        return dict(job) | {"claimant": claimant}

    def execute(connection, job):
        current = connection.execute(
            "SELECT claimed_by, status FROM scheduled_jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        if current["status"] != "claimed" or current["claimed_by"] != job["claimant"]:
            return False
        order = connection.execute(
            "SELECT * FROM orders WHERE id = ?", (job["business_id"],)
        ).fetchone()
        if order and job["kind"] == "payment_timeout":
            _expire(connection, order, moment)
        elif order and job["kind"] == "merchant_timeout" and order["status"] == "awaiting_merchant":
            _merchant_cancel(connection, order, moment)
        elif order and job["kind"] == "courier_timeout" and order["status"] == "awaiting_courier":
            task = connection.execute(
                "SELECT status FROM delivery_tasks WHERE order_id = ?", (order["id"],)
            ).fetchone()
            if task and task["status"] == "offered":
                connection.execute("UPDATE delivery_tasks SET courier_id = NULL WHERE order_id = ?",
                                   (order["id"],))
                connection.execute("UPDATE orders SET status = 'awaiting_assignment' WHERE id = ?",
                                   (order["id"],))
        elif order and job["kind"] == "auto_complete" and order["status"] == "delivered":
            pending = connection.execute(
                """SELECT 1 FROM delivery_exceptions AS e JOIN delivery_tasks AS t
                   ON t.id = e.task_id WHERE t.order_id = ? AND e.status = 'pending'""",
                (order["id"],),
            ).fetchone()
            if not pending:
                connection.execute(
                    "UPDATE orders SET status = 'completed', completed_at = ? WHERE id = ?",
                    (moment.isoformat(), order["id"]),
                )
        elif job["kind"] == "refund_settle":
            connection.execute(
                """UPDATE refund_records SET result = 'succeeded', result_at = ?
                   WHERE order_id = ? AND result = 'pending'""",
                (moment.isoformat(), job["business_id"]),
            )
        elif job["kind"] == "refund_review":
            failed = connection.execute(
                "SELECT result FROM refund_records WHERE order_id = ?",
                (job["business_id"],),
            ).fetchone()
            if failed and failed["result"] == "failed":
                connection.execute(
                    """UPDATE refund_records SET result = 'pending',
                       failure_reason = NULL, result_at = NULL
                       WHERE order_id = ?""", (job["business_id"],)
                )
                connection.execute(
                    """UPDATE scheduled_jobs SET status = 'pending', due_at = ?,
                       claimed_by = NULL, lease_until = NULL
                       WHERE kind = 'refund_settle' AND business_id = ?""",
                    ((moment + timedelta(minutes=5)).isoformat(), job["business_id"]),
                )
        elif job["kind"] == "delivery_exception_overdue":
            exception = connection.execute(
                """SELECT e.status, t.order_id FROM delivery_exceptions AS e
                   JOIN delivery_tasks AS t ON t.id = e.task_id WHERE e.id = ?""",
                (job["business_id"],),
            ).fetchone()
            if exception and exception["status"] == "pending":
                from app.audit import write_audit_log
                write_audit_log(connection, None, exception["order_id"],
                                "delivery_exception_overdue", "failed",
                                "管理员超过三十分钟未处理", moment)
                connection.execute(
                    """INSERT INTO notification_records
                       (id, order_id, recipient_id, content, created_at, status)
                       SELECT lower(hex(randomblob(16))), ?, id,
                              'delivery_exception_overdue', ?, 'pending'
                       FROM accounts WHERE role = 'admin' AND status = 'active'""",
                    (exception["order_id"], moment.isoformat()),
                )
        connection.execute(
            """UPDATE scheduled_jobs SET status = 'completed', claimed_by = NULL,
               lease_until = NULL WHERE id = ? AND claimed_by = ?""",
            (job["id"], job["claimant"]),
        )
        return True

    processed = 0
    while True:
        job = run_write(path, claim)
        if not job:
            break
        if run_write(path, lambda connection: execute(connection, job)):
            processed += 1
    from app.notifications import dispatch_pending
    return processed + dispatch_pending(path, moment)
