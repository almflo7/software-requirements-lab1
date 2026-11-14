"""追加式关键操作日志及授权查询。"""

from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.db import connect, transaction
from app.models import AuditLog, from_row


class AuditError(ValueError):
    """无权查询日志。"""


def write_audit_log(connection, actor_id: str | None, order_id: str | None,
                    action: str, result: str, reason: str | None = None,
                    occurred_at: datetime | None = None,
                    store_id: str | None = None) -> None:
    moment = occurred_at or datetime.now(timezone.utc)
    connection.execute(
        """INSERT INTO audit_logs
           (id, actor_id, order_id, store_id, action, result, reason, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid4().hex, actor_id, order_id, store_id, action, result,
         reason, moment.isoformat()),
    )


def audit_failures(action: str, actor_field: str | None = None,
                   order_field: str | None = None):
    """业务函数抛错并回滚后，在独立事务中记录失败事实。"""
    def decorate(function):
        parameters = signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except (ValueError, sqlite3.DatabaseError) as error:
                bound = parameters.bind_partial(*args, **kwargs).arguments
                path = bound.get("path")
                if path is not None:
                    try:
                        with connect(path) as connection:
                            with transaction(connection):
                                actor_id = bound.get(actor_field) if actor_field else None
                                order_id = bound.get(order_field) if order_field else None
                                if actor_id and not connection.execute(
                                    "SELECT 1 FROM accounts WHERE id = ?", (actor_id,)
                                ).fetchone():
                                    actor_id = None
                                if order_id and not connection.execute(
                                    "SELECT 1 FROM orders WHERE id = ?", (order_id,)
                                ).fetchone():
                                    order_id = None
                                write_audit_log(connection, actor_id, order_id,
                                                action, "failed", str(error))
                    except (OSError, sqlite3.DatabaseError):
                        pass
                raise

        return wrapped

    return decorate


def query_audit_logs(path: str | Path, actor_id: str, order_id: str | None = None,
                     operator_id: str | None = None,
                     date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    with connect(path) as connection:
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (actor_id,),
        ).fetchone()
        if not actor or actor["status"] != "active" or actor["role"] not in ("admin", "merchant"):
            raise AuditError("无权查询操作日志")
        clauses = ["1 = 1"]
        values = []
        if order_id:
            clauses.append("l.order_id = ?")
            values.append(order_id)
        if operator_id:
            clauses.append("l.actor_id = ?")
            values.append(operator_id)
        if date_from:
            clauses.append("substr(l.occurred_at, 1, 10) >= ?")
            values.append(date_from)
        if date_to:
            clauses.append("substr(l.occurred_at, 1, 10) <= ?")
            values.append(date_to)
        if actor["role"] == "merchant":
            clauses.append("(s.merchant_id = ? OR own_store.merchant_id = ?)")
            values.extend((actor_id, actor_id))
        rows = connection.execute(
            """SELECT l.* FROM audit_logs AS l
               LEFT JOIN orders AS o ON o.id = l.order_id
               LEFT JOIN stores AS s ON s.id = o.store_id
               LEFT JOIN stores AS own_store ON own_store.id = l.store_id
               WHERE """ + " AND ".join(clauses) + " ORDER BY l.occurred_at, l.id",
            values,
        ).fetchall()
        return [asdict(from_row(row, AuditLog)) for row in rows]
