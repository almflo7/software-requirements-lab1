"""本地模拟通知发送与第 1、3、5 分钟重试。"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import connect, transaction
from app.models import NotificationRecord, from_row


def dispatch_pending(path: str | Path, now: datetime | None = None) -> int:
    """同一数据库写事务内领取和处理到期通知。"""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    handled = 0
    with connect(path) as connection:
        due = connection.execute(
            """SELECT 1 FROM notification_records WHERE status = 'pending'
               AND (attempt_count = 0 OR julianday(next_retry_at) <= julianday(?))
               LIMIT 1""",
            (moment.isoformat(),),
        ).fetchone()
        if not due:
            return 0
        with transaction(connection):
            rows = connection.execute(
                """SELECT * FROM notification_records WHERE status = 'pending'
                   AND (attempt_count = 0 OR julianday(next_retry_at) <= julianday(?))
                   ORDER BY created_at, id""",
                (moment.isoformat(),),
            ).fetchall()
            for row in rows:
                attempt = row["attempt_count"] + 1
                if attempt > row["force_failures"]:
                    connection.execute(
                        """UPDATE notification_records SET status = 'delivered',
                           attempt_count = ?, delivered_at = ?, next_retry_at = NULL
                           WHERE id = ?""",
                        (attempt, moment.isoformat(), row["id"]),
                    )
                elif attempt >= 4:
                    connection.execute(
                        """UPDATE notification_records SET status = 'failed',
                           attempt_count = ?, admin_exception = 1,
                           next_retry_at = NULL WHERE id = ?""",
                        (attempt, row["id"]),
                    )
                else:
                    first = datetime.fromisoformat(row["created_at"])
                    delay = (1, 3, 5)[attempt - 1]
                    connection.execute(
                        """UPDATE notification_records SET attempt_count = ?,
                           next_retry_at = ? WHERE id = ?""",
                        (attempt, (first + timedelta(minutes=delay)).isoformat(), row["id"]),
                    )
                handled += 1
    return handled


def list_notifications(path: str | Path, recipient_id: str) -> list[dict]:
    with connect(path) as connection:
        rows = connection.execute(
            """SELECT order_id, content, status, created_at, delivered_at
               FROM notification_records WHERE recipient_id = ? ORDER BY created_at""",
            (recipient_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_admin_exceptions(path: str | Path, admin_id: str) -> list[dict]:
    with connect(path) as connection:
        admin = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,),
        ).fetchone()
        if not admin or admin["role"] != "admin" or admin["status"] != "active":
            raise ValueError("只有管理员可查看通知异常")
        return [asdict(from_row(row, NotificationRecord)) for row in connection.execute(
            "SELECT * FROM notification_records WHERE admin_exception = 1 ORDER BY created_at"
        )]
