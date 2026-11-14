"""人工派单、配送响应和交付。"""

from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.db import run_write
from app.payment import _time
from app.audit import audit_failures, write_audit_log
from app.models import DeliveryException, DeliveryTask, from_row


class DeliveryError(ValueError):
    """配送操作不满足权限或状态条件。"""


def _account(connection, account_id, role):
    row = connection.execute("SELECT role, status FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not row or row["role"] != role or row["status"] != "active":
        raise DeliveryError("账号无权执行该操作")


def _pending_cancel(connection, order_id):
    return connection.execute(
        "SELECT 1 FROM cancellation_requests WHERE order_id = ? AND status = 'pending'",
        (order_id,),
    ).fetchone() is not None


@audit_failures("assign_courier", "admin_id", "order_id")
def assign_courier(path: str | Path, admin_id: str, order_id: str, courier_id: str,
                   handover_confirmed: bool = False, now: datetime | None = None) -> str:
    """管理员首次派单或改派，已取餐时必须确认交接。"""
    moment = _time(now)

    def operation(connection):
        _account(connection, admin_id, "admin")
        _account(connection, courier_id, "courier")
        order = connection.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order or order["status"] not in (
            "awaiting_assignment", "awaiting_courier", "awaiting_pickup", "delivering"
        ) or _pending_cancel(connection, order_id):
            raise DeliveryError("订单当前不可派单")
        task = connection.execute("SELECT * FROM delivery_tasks WHERE order_id = ?", (order_id,)).fetchone()
        if task and task["courier_id"] == courier_id:
            raise DeliveryError("该配送员已是当前责任人")
        if order["status"] == "delivering" and not handover_confirmed:
            raise DeliveryError("已取餐改派必须确认餐品交接")
        if order["status"] in ("awaiting_pickup", "delivering"):
            occupied = connection.execute(
                """SELECT 1 FROM delivery_tasks AS t JOIN orders AS o ON o.id = t.order_id
                   WHERE t.courier_id = ? AND t.status IN ('accepted', 'picked_up')
                     AND o.status NOT IN ('completed', 'cancelled') AND t.order_id != ?""",
                (courier_id, order_id),
            ).fetchone()
            if occupied:
                raise DeliveryError("目标配送员已有进行中任务")
        task_id = task["id"] if task else uuid4().hex
        previous = task["courier_id"] if task else None
        if task:
            new_status = "picked_up" if order["status"] == "delivering" else (
                "accepted" if order["status"] == "awaiting_pickup" else "offered"
            )
            connection.execute(
                """UPDATE delivery_tasks SET courier_id = ?, status = ?, assigned_at = ?
                   WHERE id = ?""", (courier_id, new_status, moment.isoformat(), task_id),
            )
        else:
            connection.execute(
                """INSERT INTO delivery_tasks
                   (id, order_id, courier_id, status, assigned_at)
                   VALUES (?, ?, ?, 'offered', ?)""",
                (task_id, order_id, courier_id, moment.isoformat()),
            )
        connection.execute(
            """INSERT INTO assignment_records
               (id, task_id, admin_id, previous_courier_id, new_courier_id,
                assigned_at, handover_confirmed) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uuid4().hex, task_id, admin_id, previous, courier_id,
             moment.isoformat(), int(handover_confirmed)),
        )
        if order["status"] == "awaiting_assignment":
            connection.execute(
                "UPDATE orders SET status = 'awaiting_courier' WHERE id = ?", (order_id,)
            )
        if order["status"] in ("awaiting_assignment", "awaiting_courier"):
            connection.execute(
                """INSERT OR REPLACE INTO scheduled_jobs
                   (id, kind, business_id, due_at, status)
                   VALUES (?, 'courier_timeout', ?, ?, 'pending')""",
                (uuid4().hex, order_id, (moment + timedelta(minutes=3)).isoformat()),
            )
        return task_id

    return run_write(path, operation)


@audit_failures("courier_response", "courier_id", "order_id")
def respond_courier(path: str | Path, courier_id: str, order_id: str,
                    accept: bool, reason: str = "", now: datetime | None = None) -> str:
    moment = _time(now)

    def operation(connection):
        _account(connection, courier_id, "courier")
        task = connection.execute(
            "SELECT * FROM delivery_tasks WHERE order_id = ? AND courier_id = ?",
            (order_id, courier_id),
        ).fetchone()
        order = connection.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not task or task["status"] != "offered" or not order or order["status"] != "awaiting_courier":
            raise DeliveryError("当前没有可响应的本人派单")
        deadline = datetime.fromisoformat(task["assigned_at"]) + timedelta(minutes=3)
        if moment >= deadline:
            raise DeliveryError("派单响应已超时")
        if accept:
            active = connection.execute(
                """SELECT 1 FROM delivery_tasks AS t JOIN orders AS o ON o.id = t.order_id
                   WHERE t.courier_id = ? AND t.id != ?
                     AND t.status IN ('accepted', 'picked_up')
                     AND o.status NOT IN ('completed', 'cancelled')""",
                (courier_id, task["id"]),
            ).fetchone()
            if active:
                raise DeliveryError("配送员已有进行中任务")
            connection.execute("UPDATE delivery_tasks SET status = 'accepted' WHERE id = ?", (task["id"],))
            connection.execute(
                """UPDATE assignment_records SET response = 'accepted', responded_at = ?
                   WHERE id = (SELECT id FROM assignment_records WHERE task_id = ?
                               ORDER BY assigned_at DESC, id DESC LIMIT 1)""",
                (moment.isoformat(), task["id"]),
            )
            connection.execute("UPDATE orders SET status = 'awaiting_pickup' WHERE id = ?", (order_id,))
            write_audit_log(connection, courier_id, order_id, "courier_accept", "success",
                            None, moment)
            return "awaiting_pickup"
        if not reason.strip():
            raise DeliveryError("拒接必须说明原因")
        connection.execute(
            """UPDATE assignment_records SET response = 'rejected', response_reason = ?,
               responded_at = ? WHERE id = (SELECT id FROM assignment_records
               WHERE task_id = ? ORDER BY assigned_at DESC, id DESC LIMIT 1)""",
            (reason.strip(), moment.isoformat(), task["id"]),
        )
        connection.execute(
            "UPDATE delivery_tasks SET courier_id = NULL, status = 'offered' WHERE id = ?",
            (task["id"],),
        )
        connection.execute("UPDATE orders SET status = 'awaiting_assignment' WHERE id = ?", (order_id,))
        write_audit_log(connection, courier_id, order_id, "courier_reject", "success",
                        reason.strip(), moment)
        return "awaiting_assignment"

    return run_write(path, operation)


@audit_failures("courier_step", "courier_id", "order_id")
def courier_step(path: str | Path, courier_id: str, order_id: str,
                 step: str, now: datetime | None = None) -> str:
    """确认取餐或送达；每一步都核对当前配送员。"""
    moment = _time(now)
    expected = {"pickup": ("awaiting_pickup", "accepted", "delivering", "picked_up"),
                "deliver": ("delivering", "picked_up", "delivered", "delivered")}
    if step not in expected:
        raise DeliveryError("未知配送步骤")
    old_order, old_task, new_order, new_task = expected[step]

    def operation(connection):
        _account(connection, courier_id, "courier")
        task = connection.execute(
            "SELECT * FROM delivery_tasks WHERE order_id = ? AND courier_id = ?",
            (order_id, courier_id),
        ).fetchone()
        order = connection.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not task or not order or task["status"] != old_task or order["status"] != old_order:
            raise DeliveryError("当前配送状态不允许该操作")
        if step == "pickup" and _pending_cancel(connection, order_id):
            raise DeliveryError("取消申请处理中，暂停取餐")
        connection.execute("UPDATE delivery_tasks SET status = ? WHERE id = ?", (new_task, task["id"]))
        connection.execute("UPDATE orders SET status = ? WHERE id = ?", (new_order, order_id))
        write_audit_log(connection, courier_id, order_id, step, "success", None, moment)
        if step == "deliver":
            connection.execute(
                """INSERT OR IGNORE INTO scheduled_jobs
                   (id, kind, business_id, due_at, status)
                   VALUES (?, 'auto_complete', ?, ?, 'pending')""",
                (uuid4().hex, order_id, (moment + timedelta(hours=24)).isoformat()),
            )
        return new_order

    return run_write(path, operation)


@audit_failures("record_arrival", "courier_id", "order_id")
def record_arrival(path: str | Path, courier_id: str, order_id: str,
                   now: datetime | None = None) -> None:
    moment = _time(now)

    def operation(connection):
        _account(connection, courier_id, "courier")
        task = connection.execute(
            "SELECT id, status FROM delivery_tasks WHERE order_id = ? AND courier_id = ?",
            (order_id, courier_id),
        ).fetchone()
        if not task or task["status"] != "picked_up":
            raise DeliveryError("只有当前配送员可记录到达")
        connection.execute(
            "UPDATE delivery_tasks SET arrived_at = COALESCE(arrived_at, ?) WHERE id = ?",
            (moment.isoformat(), task["id"]),
        )
        write_audit_log(connection, courier_id, order_id, "arrival", "success", None, moment)

    run_write(path, operation)


@audit_failures("record_contact", "courier_id", "order_id")
def record_contact(path: str | Path, courier_id: str, order_id: str,
                   method: str, reached: bool, now: datetime | None = None,
                   target: str = "customer") -> str:
    moment = _time(now)
    if target not in ("customer", "merchant", "admin"):
        raise DeliveryError("联系对象无效")

    def operation(connection):
        _account(connection, courier_id, "courier")
        task = connection.execute(
            "SELECT * FROM delivery_tasks WHERE order_id = ? AND courier_id = ?",
            (order_id, courier_id),
        ).fetchone()
        if not task or task["status"] != "picked_up" or not task["arrived_at"]:
            raise DeliveryError("应先由当前配送员记录到达")
        if not method.strip() or moment < datetime.fromisoformat(task["arrived_at"]):
            raise DeliveryError("联系方式或时间无效")
        contact_id = uuid4().hex
        connection.execute(
            """INSERT INTO delivery_contacts
               (id, task_id, courier_id, contacted_at, target, method, result)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (contact_id, task["id"], courier_id, moment.isoformat(), target, method.strip(),
             "reached" if reached else "unreachable"),
        )
        write_audit_log(connection, courier_id, order_id, "contact", "success",
                        f"{target}/{method.strip()}: {'reached' if reached else 'unreachable'}", moment)
        return contact_id

    return run_write(path, operation)


@audit_failures("report_exception", "courier_id", "order_id")
def report_exception(path: str | Path, courier_id: str, order_id: str,
                     reason: str, now: datetime | None = None) -> str:
    moment = _time(now)

    def operation(connection):
        _account(connection, courier_id, "courier")
        task = connection.execute(
            "SELECT * FROM delivery_tasks WHERE order_id = ? AND courier_id = ?",
            (order_id, courier_id),
        ).fetchone()
        order = connection.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not task or not order or task["status"] != "picked_up" or order["status"] != "delivering":
            raise DeliveryError("当前不可报告配送异常")
        if not reason.strip():
            raise DeliveryError("异常原因不能为空")
        if reason.strip() == "无法联系顾客":
            if not task["arrived_at"]:
                raise DeliveryError("尚未记录到达")
            arrival = datetime.fromisoformat(task["arrived_at"])
            contacts = connection.execute(
                """SELECT COUNT(*) FROM delivery_contacts
                   WHERE task_id = ? AND target = 'customer' AND result = 'unreachable'
                     AND contacted_at >= ? AND contacted_at <= ?""",
                (task["id"], arrival.isoformat(),
                 (arrival + timedelta(minutes=10)).isoformat()),
            ).fetchone()[0]
            if contacts < 2 or moment > arrival + timedelta(minutes=10):
                raise DeliveryError("到达后十分钟内须有两次联系失败记录")
            informed = connection.execute(
                """SELECT 1 FROM delivery_contacts WHERE task_id = ?
                   AND target IN ('merchant', 'admin') AND contacted_at <= ? LIMIT 1""",
                (task["id"], moment.isoformat()),
            ).fetchone()
            if not informed:
                raise DeliveryError("须联系商家或管理员说明异常")
        exception_id = uuid4().hex
        connection.execute(
            """INSERT INTO delivery_exceptions
               (id, task_id, reason, reported_at, status)
               VALUES (?, ?, ?, ?, 'pending')""",
            (exception_id, task["id"], reason.strip(), moment.isoformat()),
        )
        connection.execute("UPDATE delivery_tasks SET status = 'exception' WHERE id = ?", (task["id"],))
        connection.execute("UPDATE orders SET status = 'delivery_exception' WHERE id = ?", (order_id,))
        connection.execute(
            """INSERT OR IGNORE INTO scheduled_jobs
               (id, kind, business_id, due_at, status)
               VALUES (?, 'delivery_exception_overdue', ?, ?, 'pending')""",
            (uuid4().hex, exception_id,
             (moment + timedelta(minutes=30)).isoformat()),
        )
        return exception_id

    return run_write(path, operation)


@audit_failures("resolve_exception", "admin_id")
def resolve_exception(path: str | Path, admin_id: str, exception_id: str,
                      terminate: bool, now: datetime | None = None,
                      new_courier_id: str | None = None,
                      handover_confirmed: bool = False) -> str:
    moment = _time(now)
    if terminate and new_courier_id:
        raise DeliveryError("终止配送不能同时改派")
    if new_courier_id and not handover_confirmed:
        raise DeliveryError("异常改派必须确认餐品交接")

    def operation(connection):
        _account(connection, admin_id, "admin")
        exception_row = connection.execute(
            "SELECT * FROM delivery_exceptions WHERE id = ?", (exception_id,)
        ).fetchone()
        if not exception_row or exception_row["status"] != "pending":
            raise DeliveryError("配送异常不存在或已处理")
        exception = from_row(exception_row, DeliveryException)
        task_row = connection.execute(
            "SELECT * FROM delivery_tasks WHERE id = ?", (exception["task_id"],)
        ).fetchone()
        task = from_row(task_row, DeliveryTask)
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (task["order_id"],)).fetchone()
        if order["status"] != "delivery_exception":
            raise DeliveryError("订单状态已变化")
        if moment > datetime.fromisoformat(exception["reported_at"]) + timedelta(minutes=30):
            write_audit_log(connection, admin_id, order["id"],
                            "delivery_exception_overdue", "failed",
                            exception_id, moment)
        if new_courier_id:
            _account(connection, new_courier_id, "courier")
            if new_courier_id == task["courier_id"]:
                raise DeliveryError("目标配送员已是当前责任人")
            occupied = connection.execute(
                """SELECT 1 FROM delivery_tasks AS t JOIN orders AS o ON o.id = t.order_id
                   WHERE t.courier_id = ? AND t.id != ?
                     AND t.status IN ('accepted', 'picked_up', 'exception')
                     AND o.status NOT IN ('completed', 'cancelled') LIMIT 1""",
                (new_courier_id, task["id"]),
            ).fetchone()
            if occupied:
                raise DeliveryError("目标配送员已有进行中任务")
            connection.execute(
                """INSERT INTO assignment_records
                   (id, task_id, admin_id, previous_courier_id, new_courier_id,
                    assigned_at, handover_confirmed, response, responded_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, 'accepted', ?)""",
                (uuid4().hex, task["id"], admin_id, task["courier_id"],
                 new_courier_id, moment.isoformat(), moment.isoformat()),
            )
            connection.execute(
                "UPDATE delivery_tasks SET courier_id = ? WHERE id = ?",
                (new_courier_id, task["id"]),
            )
        action = "terminate" if terminate else ("handover" if new_courier_id else "continue")
        connection.execute(
            """UPDATE delivery_exceptions SET status = ?, resolved_action = ?,
               decided_by = ?, decided_at = ?
               WHERE id = ?""",
            ("terminate" if terminate else "continue", action,
             admin_id, moment.isoformat(), exception_id),
        )
        if terminate:
            from app.after_sales import _refund_for_order
            connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order["id"],))
            _refund_for_order(connection, order, moment)
            write_audit_log(connection, admin_id, order["id"], "resolve_exception",
                            "success", "terminate", moment)
            return "cancelled"
        connection.execute("UPDATE orders SET status = 'delivering' WHERE id = ?", (order["id"],))
        connection.execute("UPDATE delivery_tasks SET status = 'picked_up' WHERE id = ?", (task["id"],))
        write_audit_log(connection, admin_id, order["id"], "resolve_exception",
                        "success", action, moment)
        return "delivering"

    return run_write(path, operation)


@audit_failures("confirm_completion", "customer_id", "order_id")
def confirm_completion(path: str | Path, customer_id: str, order_id: str,
                       now: datetime | None = None) -> str:
    """顾客确认已送达订单完成。"""
    moment = _time(now)

    def operation(connection):
        order = connection.execute(
            "SELECT status FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ).fetchone()
        if not order or order["status"] != "delivered":
            raise DeliveryError("订单不属于该顾客或尚未送达")
        pending = connection.execute(
            """SELECT 1 FROM delivery_exceptions AS e
               JOIN delivery_tasks AS t ON t.id = e.task_id
               WHERE t.order_id = ? AND e.status = 'pending'""",
            (order_id,),
        ).fetchone()
        if pending:
            raise DeliveryError("尚有未处理的配送异常")
        connection.execute(
            "UPDATE orders SET status = 'completed', completed_at = ? WHERE id = ?",
            (moment.isoformat(), order_id),
        )
        return "completed"

    return run_write(path, operation)
