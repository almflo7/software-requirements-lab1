"""新增功能的四项基础验收；不重复已有主链测试。"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.access import AccessError, view_order
from app.after_sales import apply_refund_result, request_cancellation
from app.audit import query_audit_logs
from app.catalog import CatalogError, update_menu_item
from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.delivery import (DeliveryError, assign_courier, confirm_completion,
                          courier_step, record_arrival, record_contact,
                          report_exception, resolve_exception, respond_courier)
from app.disputes import open_dispute, resolve_dispute
from app.fulfillment import finish_preparation, respond_merchant
from app.notifications import list_notifications
from app.payment import apply_payment_result, start_payment
from app.reporting import query_statistics
from app.support import run_due_jobs


class TargetedCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "targeted.sqlite3"
        seed_demo(self.path)
        with connect(self.path) as connection:
            connection.execute(
                "INSERT INTO accounts VALUES ('courier-b', 'courier', 'active', '100')"
            )
        confirmation = confirm_checkout(
            self.path, "customer-demo", "cart-demo", "address-demo"
        ).confirmation_id
        self.order_id = create_order(
            self.path, "customer-demo", confirmation
        ).order_id
        with connect(self.path) as connection:
            placed_at = connection.execute(
                "SELECT placed_at FROM orders WHERE id = ?", (self.order_id,)
            ).fetchone()[0]
        self.now = datetime.fromisoformat(placed_at) + timedelta(seconds=1)

    def _paid_order(self):
        start_payment(self.path, "customer-demo", self.order_id, "targeted-pay", self.now)
        apply_payment_result(self.path, "targeted-pay", True, now=self.now)

    def _picked_up_order(self):
        self._paid_order()
        respond_merchant(self.path, "merchant-demo", self.order_id, True, now=self.now)
        finish_preparation(self.path, "merchant-demo", self.order_id)
        assign_courier(self.path, "admin-demo", self.order_id, "courier-demo", now=self.now)
        respond_courier(self.path, "courier-demo", self.order_id, True, now=self.now)
        courier_step(self.path, "courier-demo", self.order_id, "pickup", now=self.now)

    def _reported_exception(self):
        self._picked_up_order()
        record_arrival(self.path, "courier-demo", self.order_id, now=self.now)
        record_contact(self.path, "courier-demo", self.order_id, "phone", False,
                       self.now + timedelta(minutes=1))
        record_contact(self.path, "courier-demo", self.order_id, "phone", False,
                       self.now + timedelta(minutes=2))
        record_contact(self.path, "courier-demo", self.order_id, "phone", True,
                       self.now + timedelta(minutes=2), target="merchant")
        return report_exception(self.path, "courier-demo", self.order_id,
                                "无法联系顾客", self.now + timedelta(minutes=3))

    def test_exception_handover_revokes_previous_courier(self):
        exception_id = self._reported_exception()
        self.assertEqual(resolve_exception(
            self.path, "admin-demo", exception_id, False,
            now=self.now + timedelta(minutes=4), new_courier_id="courier-b",
            handover_confirmed=True), "delivering")
        with self.assertRaises(AccessError):
            view_order(self.path, "courier-demo", self.order_id, True)
        with self.assertRaises(DeliveryError):
            courier_step(self.path, "courier-demo", self.order_id, "deliver",
                         now=self.now + timedelta(minutes=5))
        self.assertEqual(courier_step(
            self.path, "courier-b", self.order_id, "deliver",
            now=self.now + timedelta(minutes=5)), "delivered")
        with connect(self.path) as connection:
            action = connection.execute(
                "SELECT resolved_action FROM delivery_exceptions WHERE id = ?",
                (exception_id,),
            ).fetchone()[0]
        self.assertEqual(action, "handover")

    def test_failed_refund_is_reviewed_once(self):
        self._paid_order()
        request_cancellation(self.path, "customer-demo", self.order_id,
                             "取消订单", self.now)
        failed_at = self.now + timedelta(minutes=2)
        apply_refund_result(self.path, self.order_id, False, "模拟失败", failed_at)
        run_due_jobs(self.path, failed_at + timedelta(hours=23, minutes=50))
        run_due_jobs(self.path, failed_at + timedelta(hours=23, minutes=55))
        with connect(self.path) as connection:
            refunds = connection.execute(
                "SELECT result, amount_cents FROM refund_records WHERE order_id = ?",
                (self.order_id,),
            ).fetchall()
        self.assertEqual([tuple(row) for row in refunds], [("succeeded", 4500)])

    def test_completed_dispute_refund_keeps_original_sales(self):
        self._picked_up_order()
        courier_step(self.path, "courier-demo", self.order_id, "deliver", now=self.now)
        confirm_completion(self.path, "customer-demo", self.order_id, now=self.now)
        dispute_id = open_dispute(self.path, "customer-demo", self.order_id,
                                  "餐品争议")
        resolve_dispute(self.path, "admin-demo", dispute_id,
                        "同意全额退款", refund_full=True)
        refund_day = self.now + timedelta(days=1)
        run_due_jobs(self.path, refund_day)
        completed_date = self.now.date().isoformat()
        refund_date = refund_day.date().isoformat()
        completed = query_statistics(self.path, "merchant-demo",
                                     completed_date, completed_date)
        refunded = query_statistics(self.path, "merchant-demo",
                                    refund_date, refund_date)
        self.assertEqual((completed["completed_orders"], completed["revenue_cents"]),
                         (1, 4500))
        self.assertEqual(completed["popular_items"], [("招牌饭", 2)])
        self.assertEqual((refunded["refund_cents"], refunded["completed_orders"]),
                         (4500, 0))

    def test_failed_audit_and_overdue_exception_alert(self):
        exception_id = self._reported_exception()
        with self.assertRaises(CatalogError):
            update_menu_item(self.path, "courier-demo", "item-demo",
                             2000, 3, "listed")
        run_due_jobs(self.path, self.now + timedelta(minutes=33))
        messages = list_notifications(self.path, "admin-demo")
        self.assertIn("delivery_exception_overdue",
                      [message["content"] for message in messages])
        logs = query_audit_logs(self.path, "admin-demo")
        self.assertTrue(any(row["action"] == "update_menu_item"
                            and row["result"] == "failed" for row in logs))
        with connect(self.path) as connection:
            status = connection.execute(
                "SELECT status FROM delivery_exceptions WHERE id = ?",
                (exception_id,),
            ).fetchone()[0]
        self.assertEqual(status, "pending")


if __name__ == "__main__":
    unittest.main()
