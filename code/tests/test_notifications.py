"""通知即时发送、失败重试及管理员异常列表。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.notifications import dispatch_pending, list_admin_exceptions


class NotificationTests(unittest.TestCase):
    def test_retries_at_one_three_five_minutes(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "notify.sqlite3"
        seed_demo(path)
        token = confirm_checkout(path, "customer-demo", "cart-demo", "address-demo").confirmation_id
        order_id = create_order(path, "customer-demo", token).order_id
        start = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
        with connect(path) as connection:
            connection.execute(
                """INSERT INTO notification_records
                   (id, order_id, recipient_id, content, created_at, status, force_failures)
                   VALUES ('forced', ?, 'customer-demo', 'test', ?, 'pending', 4)""",
                (order_id, start.isoformat()),
            )
        for count, minutes in enumerate((0, 1, 3, 5), start=1):
            self.assertEqual(dispatch_pending(path, start + timedelta(minutes=minutes)), 1)
            with connect(path) as connection:
                row = connection.execute(
                    "SELECT attempt_count, status FROM notification_records WHERE id = 'forced'"
                ).fetchone()
                self.assertEqual(row["attempt_count"], count)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(len(list_admin_exceptions(path, "admin-demo")), 1)


if __name__ == "__main__":
    unittest.main()
