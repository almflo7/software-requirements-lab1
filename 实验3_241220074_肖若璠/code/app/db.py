"""SQLite 连接和写事务。"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class ClosingConnection(sqlite3.Connection):
    """退出 with 块时同时关闭文件句柄，避免 Windows 锁住测试数据库。"""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: str | Path) -> sqlite3.Connection:
    """每次调用创建独立连接；事务由调用者管理。"""
    connection = sqlite3.connect(
        str(path), timeout=5, isolation_level=None, factory=ClosingConnection
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_schema(path: str | Path) -> None:
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("DROP TRIGGER IF EXISTS audit_stock_update")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        refund_columns = {row["name"] for row in connection.execute("PRAGMA table_info(refund_records)")}
        if "failure_reason" not in refund_columns:
            connection.execute("ALTER TABLE refund_records ADD COLUMN failure_reason TEXT")
        order_columns = {row["name"] for row in connection.execute("PRAGMA table_info(orders)")}
        if "completed_at" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN completed_at TEXT")
        notification_columns = {row["name"] for row in connection.execute("PRAGMA table_info(notification_records)")}
        for name, definition in (
            ("next_retry_at", "TEXT"),
            ("force_failures", "INTEGER NOT NULL DEFAULT 0"),
            ("admin_exception", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in notification_columns:
                connection.execute(f"ALTER TABLE notification_records ADD COLUMN {name} {definition}")
        job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(scheduled_jobs)")}
        for name in ("claimed_by", "lease_until"):
            if name not in job_columns:
                connection.execute(f"ALTER TABLE scheduled_jobs ADD COLUMN {name} TEXT")
        assignment_columns = {row["name"] for row in connection.execute("PRAGMA table_info(assignment_records)")}
        for name in ("response", "response_reason", "responded_at"):
            if name not in assignment_columns:
                connection.execute(f"ALTER TABLE assignment_records ADD COLUMN {name} TEXT")
        contact_columns = {row["name"] for row in connection.execute("PRAGMA table_info(delivery_contacts)")}
        if "target" not in contact_columns:
            connection.execute("ALTER TABLE delivery_contacts ADD COLUMN target TEXT NOT NULL DEFAULT 'customer'")
        exception_columns = {row["name"] for row in connection.execute("PRAGMA table_info(delivery_exceptions)")}
        if "resolved_action" not in exception_columns:
            connection.execute("ALTER TABLE delivery_exceptions ADD COLUMN resolved_action TEXT")
        store_columns = {row["name"] for row in connection.execute("PRAGMA table_info(stores)")}
        for name in ("description", "business_hours"):
            if name not in store_columns:
                connection.execute(f"ALTER TABLE stores ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        audit_columns = {row["name"] for row in connection.execute("PRAGMA table_info(audit_logs)")}
        if "store_id" not in audit_columns:
            connection.execute("ALTER TABLE audit_logs ADD COLUMN store_id TEXT REFERENCES stores(id)")


@contextmanager
def transaction(connection: sqlite3.Connection, immediate: bool = True):
    """提交完整写入，异常时回滚。"""
    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def run_write(path: str | Path, operation, immediate: bool = True):
    """仅对锁冲突重试整个事务；业务异常直接返回调用者。"""
    delays = (0.0, 0.05, 0.1, 0.2)
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        connection = connect(path)
        try:
            with transaction(connection, immediate):
                result = operation(connection)
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or attempt == len(delays) - 1:
                raise
            continue
        finally:
            connection.close()
        try:
            from app.notifications import dispatch_pending
            dispatch_pending(path)
        except sqlite3.DatabaseError:
            # 业务已提交；未发出的通知留在数据库中等待定时任务重试。
            pass
        return result
    raise RuntimeError("写事务重试意外结束")
