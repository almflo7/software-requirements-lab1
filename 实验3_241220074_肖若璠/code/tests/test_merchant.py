"""商家接单、拒单、超时及全额退款验收。"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.after_sales import apply_refund_result
from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.fulfillment import FulfillmentError, finish_preparation, respond_merchant
from app.payment import apply_payment_result, start_payment
from app.support import run_due_jobs


class MerchantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "merchant.sqlite3"
        seed_demo(self.path)
        confirmation = confirm_checkout(
            self.path, "customer-demo", "cart-demo", "address-demo"
        ).confirmation_id
        self.order_id = create_order(self.path, "customer-demo", confirmation).order_id
        with connect(self.path) as connection:
            placed = connection.execute(
                "SELECT placed_at FROM orders WHERE id = ?", (self.order_id,)
            ).fetchone()[0]
        self.placed = datetime.fromisoformat(placed)
        self.paid = self.placed + timedelta(seconds=10)
        start_payment(self.path, "customer-demo", self.order_id, "pay-1", self.placed)
        apply_payment_result(self.path, "pay-1", True, now=self.paid)

    def test_accept_at_four_minutes_fifty_nine_seconds(self):
        result = respond_merchant(
            self.path, "merchant-demo", self.order_id, True,
            now=self.paid + timedelta(minutes=4, seconds=59),
        )
        self.assertEqual(result.status, "preparing")
        run_due_jobs(self.path, self.paid + timedelta(minutes=5))
        finished = finish_preparation(self.path, "merchant-demo", self.order_id)
        self.assertEqual(finished.status, "awaiting_assignment")
        with connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM orders WHERE id = ?", (self.order_id,)
            ).fetchone()[0], "awaiting_assignment")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM refund_records"
            ).fetchone()[0], 0)

    def test_rejection_requires_reason_and_refunds_once(self):
        with self.assertRaisesRegex(FulfillmentError, "原因"):
            respond_merchant(self.path, "merchant-demo", self.order_id, False,
                             now=self.paid + timedelta(minutes=1))
        result = respond_merchant(self.path, "merchant-demo", self.order_id, False,
                                  "无法制作", self.paid + timedelta(minutes=1))
        self.assertEqual((result.status, result.refund_pending), ("cancelled", True))
        first = apply_refund_result(self.path, self.order_id, True,
                                    now=self.paid + timedelta(minutes=2))
        repeated = apply_refund_result(self.path, self.order_id, True,
                                       now=self.paid + timedelta(minutes=3))
        self.assertEqual(first, repeated)
        self.assertEqual((first.amount_cents, first.result), (4500, "succeeded"))
        with connect(self.path) as connection:
            response = connection.execute(
                "SELECT reason FROM merchant_responses WHERE order_id = ?", (self.order_id,)
            ).fetchone()
            self.assertEqual(response["reason"], "无法制作")
            self.assertEqual(connection.execute(
                "SELECT available_quantity FROM menu_items WHERE id = 'item-demo'"
            ).fetchone()[0], 5)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM refund_records"
            ).fetchone()[0], 1)

    def test_five_minute_timeout(self):
        deadline = self.paid + timedelta(minutes=5)
        with self.assertRaisesRegex(FulfillmentError, "超时"):
            respond_merchant(self.path, "merchant-demo", self.order_id, True,
                             now=deadline)
        self.assertEqual(run_due_jobs(self.path, deadline), 1)
        self.assertEqual(run_due_jobs(self.path, deadline), 0)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM orders WHERE id = ?", (self.order_id,)
            ).fetchone()[0], "cancelled")
            self.assertEqual(connection.execute(
                "SELECT available_quantity FROM menu_items WHERE id = 'item-demo'"
            ).fetchone()[0], 5)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM refund_records"
            ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
