"""店铺维护、停用、退款、通知及审计的跨模块验收。"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.access import view_order
from app.audit import query_audit_logs, write_audit_log
from app.after_sales import apply_refund_result, retry_failed_refund
from app.catalog import (add_menu_item, add_store, create_cart, search_catalog,
                         update_menu_item, update_store)
from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.identity import disable_account, disable_store, save_address, update_phone
from app.notifications import list_notifications
from app.payment import apply_payment_result, start_payment
from app.support import run_due_jobs


class RemainingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "remaining.sqlite3"
        seed_demo(self.path)

    def _order(self):
        token = confirm_checkout(self.path, "customer-demo", "cart-demo",
                                 "address-demo").confirmation_id
        return create_order(self.path, "customer-demo", token).order_id

    def test_store_cart_address_and_search(self):
        store_id = add_store(self.path, "merchant-demo", "新店", ("campus",),
                             300, "简介", "09:00-21:00")
        item_id = add_menu_item(self.path, "merchant-demo", store_id,
                                "面", "主食", 1200, 0)
        cart_id = create_cart(self.path, "customer-demo", store_id)
        self.assertEqual(create_cart(self.path, "customer-demo", store_id), cart_id)
        self.assertTrue(search_catalog(self.path, "面")[0]["sold_out"])
        update_menu_item(self.path, "merchant-demo", item_id, 1300, 2, "listed")
        update_store(self.path, "merchant-demo", store_id, "新店", "paused", 400,
                     "新简介", "10:00-20:00", ("campus", "west"))
        result = search_catalog(self.path, "面", "主食", 1300, 1300)[0]
        self.assertEqual((result["description"], result["business_hours"],
                          result["store_status"]), ("新简介", "10:00-20:00", "paused"))
        merchant_actions = {row["action"] for row in query_audit_logs(
            self.path, "merchant-demo")}
        self.assertTrue({"add_store", "add_menu_item", "update_store",
                         "update_menu_item"}.issubset(merchant_actions))
        address_id = save_address(self.path, "customer-demo", "新收货人",
                                  "13800000009", "西门", "west")
        update_phone(self.path, "customer-demo", "13800000008")
        with connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT area_code FROM addresses WHERE id = ?", (address_id,)
            ).fetchone()[0], "west")

    def test_disable_only_selected_order_and_read_refund(self):
        first = self._order()
        second = self._order()
        start_payment(self.path, "customer-demo", first, "paid-first")
        apply_payment_result(self.path, "paid-first", True)
        disable_account(self.path, "admin-demo", "merchant-demo", (first,))
        with connect(self.path) as connection:
            states = dict(connection.execute("SELECT id, status FROM orders"))
            self.assertEqual(states[first], "cancelled")
            self.assertEqual(states[second], "pending_payment")
            refund = connection.execute(
                "SELECT result, amount_cents FROM refund_records WHERE order_id = ?", (first,)
            ).fetchone()
            self.assertEqual(tuple(refund), ("pending", 4500))
        self.assertEqual(view_order(self.path, "customer-demo", first)["refund"]["result"],
                         "pending")

    def test_store_disable_and_refund_retry(self):
        order_id = self._order()
        start_payment(self.path, "customer-demo", order_id, "paid")
        apply_payment_result(self.path, "paid", True)
        disable_store(self.path, "admin-demo", "store-demo", (order_id,))
        apply_refund_result(self.path, order_id, False, "模拟失败")
        retry_at = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(retry_failed_refund(
            self.path, "admin-demo", order_id, retry_at), "pending")
        run_due_jobs(self.path, retry_at + timedelta(minutes=5))
        with connect(self.path) as connection:
            refunds = connection.execute(
                "SELECT result FROM refund_records WHERE order_id = ?", (order_id,)
            ).fetchall()
            self.assertEqual(len(refunds), 1)
            self.assertEqual(refunds[0][0], "succeeded")
        self.assertEqual(view_order(self.path, "merchant-demo", order_id, True)["address"],
                         "***")

    def test_normal_notification_and_immutable_audit(self):
        order_id = self._order()
        messages = list_notifications(self.path, "customer-demo")
        self.assertTrue(messages)
        for message in messages:
            self.assertEqual(message["status"], "delivered")
            sent = datetime.fromisoformat(message["delivered_at"])
            created = datetime.fromisoformat(message["created_at"])
            self.assertLessEqual((sent - created).total_seconds(), 10)
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT id FROM audit_logs WHERE order_id = ? LIMIT 1", (order_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM audit_logs WHERE id = ?", (row[0],))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE audit_logs SET result = 'x' WHERE id = ?",
                                   (row[0],))
            old = datetime(2025, 11, 14, tzinfo=timezone.utc)
            write_audit_log(connection, "admin-demo", order_id,
                            "retention_case", "success", occurred_at=old)
        retained = query_audit_logs(self.path, "admin-demo", order_id=order_id,
                                    date_from="2025-11-14", date_to="2026-11-14")
        self.assertIn("retention_case", {row["action"] for row in retained})


if __name__ == "__main__":
    unittest.main()
