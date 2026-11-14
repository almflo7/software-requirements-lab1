"""模拟付款、重复结果和超时恢复验收。"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.payment import PaymentError, apply_payment_result, start_payment
from app.support import run_due_jobs


class PaymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "payment.sqlite3"
        seed_demo(self.path)
        confirmation = confirm_checkout(
            self.path, "customer-demo", "cart-demo", "address-demo"
        ).confirmation_id
        self.order = create_order(self.path, "customer-demo", confirmation)
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT placed_at FROM orders WHERE id = ?", (self.order.order_id,)
            ).fetchone()
            self.placed_at = datetime.fromisoformat(row["placed_at"])

    def test_failure_retry_and_duplicate_success(self):
        first = start_payment(self.path, "customer-demo", self.order.order_id,
                              "request-fail", self.placed_at + timedelta(seconds=1))
        self.assertEqual(first.payment_result, "pending")
        with self.assertRaisesRegex(PaymentError, "确认中"):
            start_payment(self.path, "customer-demo", self.order.order_id,
                          "request-other", self.placed_at + timedelta(seconds=2))
        failed = apply_payment_result(self.path, "request-fail", False, "模拟失败",
                                      self.placed_at + timedelta(seconds=3))
        self.assertEqual((failed.payment_result, failed.order_status),
                         ("failed", "pending_payment"))
        start_payment(self.path, "customer-demo", self.order.order_id,
                      "request-success", self.placed_at + timedelta(seconds=4))
        success = apply_payment_result(self.path, "request-success", True,
                                       now=self.placed_at + timedelta(seconds=5))
        repeated = apply_payment_result(self.path, "request-success", True,
                                        now=self.placed_at + timedelta(seconds=6))
        self.assertEqual(success.order_status, "awaiting_merchant")
        self.assertEqual(repeated, success)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM payment_records WHERE result = 'succeeded'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM scheduled_jobs WHERE kind = 'merchant_timeout'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT available_quantity FROM menu_items WHERE id = 'item-demo'"
            ).fetchone()[0], 3)

    def test_timeout_and_late_success_refund(self):
        start_payment(self.path, "customer-demo", self.order.order_id,
                      "request-late", self.placed_at + timedelta(minutes=1))
        due = self.placed_at + timedelta(minutes=10)
        self.assertEqual(run_due_jobs(self.path, due), 1)
        self.assertEqual(run_due_jobs(self.path, due), 0)
        late = apply_payment_result(self.path, "request-late", True,
                                    now=due + timedelta(seconds=1))
        repeated = apply_payment_result(self.path, "request-late", True,
                                        now=due + timedelta(seconds=2))
        self.assertEqual(late.order_status, "cancelled")
        self.assertTrue(late.refund_pending)
        self.assertEqual(repeated, late)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT available_quantity FROM menu_items WHERE id = 'item-demo'"
            ).fetchone()[0], 5)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM refund_records WHERE amount_cents = 4500"
            ).fetchone()[0], 1)

    def test_success_at_deadline_is_late(self):
        start_payment(self.path, "customer-demo", self.order.order_id,
                      "request-edge", self.placed_at + timedelta(minutes=9))
        result = apply_payment_result(self.path, "request-edge", True,
                                      now=self.placed_at + timedelta(minutes=10))
        self.assertEqual((result.order_status, result.refund_pending), ("cancelled", True))


if __name__ == "__main__":
    unittest.main()
