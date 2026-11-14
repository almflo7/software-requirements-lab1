"""人工派单和分阶段取消的集成路径。"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.after_sales import decide_cancellation, request_cancellation
from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.delivery import (DeliveryError, assign_courier, courier_step, record_arrival,
                          record_contact, report_exception, resolve_exception, respond_courier)
from app.fulfillment import finish_preparation, respond_merchant
from app.payment import apply_payment_result, start_payment
from app.support import run_due_jobs


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "delivery.sqlite3"
        seed_demo(self.path)
        with connect(self.path) as connection:
            connection.executemany(
                "INSERT INTO accounts VALUES (?, ?, 'active', '100')",
                [("admin", "admin"), ("courier-a", "courier"), ("courier-b", "courier")],
            )
        confirmation = confirm_checkout(
            self.path, "customer-demo", "cart-demo", "address-demo"
        ).confirmation_id
        self.order_id = create_order(self.path, "customer-demo", confirmation).order_id
        with connect(self.path) as connection:
            placed = connection.execute(
                "SELECT placed_at FROM orders WHERE id = ?", (self.order_id,)
            ).fetchone()[0]
        self.time = datetime.fromisoformat(placed) + timedelta(seconds=1)
        start_payment(self.path, "customer-demo", self.order_id, "pay", self.time)
        apply_payment_result(self.path, "pay", True, now=self.time)
        respond_merchant(self.path, "merchant-demo", self.order_id, True, now=self.time)
        finish_preparation(self.path, "merchant-demo", self.order_id)

    def test_delivery_reassignment_and_completion(self):
        assign_courier(self.path, "admin", self.order_id, "courier-a", now=self.time)
        respond_courier(self.path, "courier-a", self.order_id, True, now=self.time)
        courier_step(self.path, "courier-a", self.order_id, "pickup", now=self.time)
        with self.assertRaisesRegex(DeliveryError, "交接"):
            assign_courier(self.path, "admin", self.order_id, "courier-b", now=self.time)
        assign_courier(self.path, "admin", self.order_id, "courier-b", True, self.time)
        with self.assertRaises(DeliveryError):
            courier_step(self.path, "courier-a", self.order_id, "deliver", now=self.time)
        self.assertEqual(courier_step(self.path, "courier-b", self.order_id,
                                      "deliver", now=self.time), "delivered")
        run_due_jobs(self.path, self.time + timedelta(hours=24))
        with connect(self.path) as connection:
            status = connection.execute(
                "SELECT status FROM orders WHERE id = ?", (self.order_id,)
            ).fetchone()[0]
            self.assertEqual(status, "completed")

    def test_cancellation_pauses_pickup(self):
        assign_courier(self.path, "admin", self.order_id, "courier-a", now=self.time)
        respond_courier(self.path, "courier-a", self.order_id, True, now=self.time)
        request = request_cancellation(
            self.path, "customer-demo", self.order_id, "不需要了", self.time
        )
        with self.assertRaisesRegex(DeliveryError, "暂停取餐"):
            courier_step(self.path, "courier-a", self.order_id, "pickup", now=self.time)
        self.assertEqual(decide_cancellation(
            self.path, "merchant-demo", request, True, self.time), "approved")
        with connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM orders WHERE id = ?", (self.order_id,)
            ).fetchone()[0], "cancelled")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM refund_records WHERE order_id = ?", (self.order_id,)
            ).fetchone()[0], 1)

    def test_unreachable_customer_requires_two_contacts(self):
        assign_courier(self.path, "admin", self.order_id, "courier-a", now=self.time)
        respond_courier(self.path, "courier-a", self.order_id, True, now=self.time)
        courier_step(self.path, "courier-a", self.order_id, "pickup", now=self.time)
        record_arrival(self.path, "courier-a", self.order_id, now=self.time)
        record_contact(self.path, "courier-a", self.order_id, "phone", False,
                       self.time + timedelta(minutes=1))
        with self.assertRaisesRegex(DeliveryError, "两次"):
            report_exception(self.path, "courier-a", self.order_id, "无法联系顾客",
                             self.time + timedelta(minutes=2))
        record_contact(self.path, "courier-a", self.order_id, "phone", False,
                       self.time + timedelta(minutes=3))
        with self.assertRaisesRegex(DeliveryError, "商家或管理员"):
            report_exception(self.path, "courier-a", self.order_id,
                             "无法联系顾客", self.time + timedelta(minutes=4))
        record_contact(self.path, "courier-a", self.order_id, "phone", True,
                       self.time + timedelta(minutes=3), target="merchant")
        exception_id = report_exception(self.path, "courier-a", self.order_id,
                                        "无法联系顾客", self.time + timedelta(minutes=4))
        self.assertEqual(resolve_exception(self.path, "admin", exception_id, True,
                                           self.time + timedelta(minutes=5)), "cancelled")
        with connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT available_quantity FROM menu_items WHERE id = 'item-demo'"
            ).fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
