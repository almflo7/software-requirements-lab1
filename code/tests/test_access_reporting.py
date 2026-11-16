"""订单隐私与统计日期口径。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.access import AccessError, view_order
from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.reporting import query_statistics


class AccessReportingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "report.sqlite3"
        seed_demo(self.path)
        token = confirm_checkout(self.path, "customer-demo", "cart-demo", "address-demo").confirmation_id
        self.order_id = create_order(self.path, "customer-demo", token).order_id

    def test_access_and_statistics(self):
        with connect(self.path) as connection:
            connection.execute("INSERT INTO accounts VALUES ('outsider', 'merchant', 'active', '123')")
        with self.assertRaises(AccessError):
            view_order(self.path, "outsider", self.order_id, True)
        self.assertEqual(view_order(self.path, "customer-demo", self.order_id, True)["address"], "校园东门一号")
        self.assertEqual(view_order(self.path, "merchant-demo", self.order_id)["phone"], "138****0001")
        completed = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        refunded = completed + timedelta(days=1)
        with connect(self.path) as connection:
            for status in ("awaiting_merchant", "preparing", "awaiting_assignment",
                           "awaiting_courier", "awaiting_pickup", "delivering", "delivered"):
                connection.execute("UPDATE orders SET status = ? WHERE id = ?", (status, self.order_id))
            connection.execute("UPDATE orders SET status = 'completed', completed_at = ? WHERE id = ?",
                               (completed.isoformat(), self.order_id))
            connection.execute("INSERT INTO payment_records (request_id, order_id, result, requested_at, result_at) VALUES ('paid', ?, 'succeeded', ?, ?)",
                               (self.order_id, completed.isoformat(), completed.isoformat()))
            connection.execute("INSERT INTO refund_records (id, order_id, payment_request_id, amount_cents, result, initiated_at, result_at) VALUES ('refund', ?, 'paid', 4500, 'succeeded', ?, ?)",
                               (self.order_id, refunded.isoformat(), refunded.isoformat()))
        first = query_statistics(self.path, "merchant-demo", "2026-11-14", "2026-11-14")
        second = query_statistics(self.path, "merchant-demo", "2026-11-15", "2026-11-15")
        self.assertEqual((first["completed_orders"], first["revenue_cents"], first["refund_cents"]), (1, 4500, 0))
        self.assertEqual((second["completed_orders"], second["refund_cents"]), (0, 4500))


if __name__ == "__main__":
    unittest.main()
