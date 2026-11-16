"""第一阶段结算与库存验收。"""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.checkout import CheckoutError, confirm_checkout, create_order
from app.db import connect, initialize_schema


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        initialize_schema(self.path)
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO accounts VALUES (?, ?, ?, ?)",
                [("merchant", "merchant", "active", "100"),
                 *[(f"customer-{number}", "customer", "active", "200")
                   for number in range(10)]],
            )
            connection.execute(
                """INSERT INTO stores
                   (id, merchant_id, name, status, delivery_areas, delivery_fee_cents)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("store", "merchant", "原店名", "open", '["area"]', 500),
            )
            connection.execute("INSERT INTO map_points VALUES ('store-point', '原店取餐点', 'store', 3, 0, 1)")
            connection.execute("UPDATE stores SET store_point_id = 'store-point' WHERE id = 'store'")
            connection.execute(
                "INSERT INTO menu_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("item", "store", "原菜名", "主食", 2000, 10, "listed"),
            )
            for number in range(10):
                customer = f"customer-{number}"
                connection.execute(
                    "INSERT INTO addresses (id, customer_id, recipient, phone, address_text, area_code) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"address-{number}", customer, "收货人", "300", "原地址", "area"),
                )
                connection.execute("UPDATE addresses SET pickup_point_id = 'pickup-xianlin' WHERE id = ?",
                                   (f"address-{number}",))
                connection.execute("INSERT INTO carts VALUES (?, ?, ?)",
                                   (f"cart-{number}", customer, "store"))
                connection.execute("INSERT INTO cart_items VALUES (?, ?, ?, ?)",
                                   (f"cart-{number}", "item", 1, 2000))
            connection.commit()

    def quote(self, number=0, accept=False, fingerprint=None):
        return confirm_checkout(self.path, f"customer-{number}", f"cart-{number}",
                                f"address-{number}", accept, fingerprint)

    def test_order_snapshot_and_repeat_submission(self):
        confirmation = self.quote().confirmation_id
        created = create_order(self.path, "customer-0", confirmation, "少辣")
        self.assertFalse(created.reused)
        with connect(self.path) as connection:
            connection.execute("UPDATE menu_items SET name = '新菜名', price_cents = 3000 WHERE id = 'item'")
            connection.execute("UPDATE addresses SET address_text = '新地址' WHERE id = 'address-0'")
            order = connection.execute("SELECT * FROM orders WHERE id = ?", (created.order_id,)).fetchone()
            item = connection.execute("SELECT * FROM order_items WHERE order_id = ?", (created.order_id,)).fetchone()
            self.assertEqual((order["total_amount_cents"], order["address_snapshot"], order["note"]),
                             (2500, "原地址", "少辣"))
            self.assertEqual((item["item_name_snapshot"], item["unit_price_cents"]),
                             ("原菜名", 2000))
        repeated = create_order(self.path, "customer-0", confirmation)
        self.assertTrue(repeated.reused)
        self.assertEqual(repeated.order_id, created.order_id)

    def test_price_change_requires_new_confirmation(self):
        with connect(self.path) as connection:
            connection.execute("UPDATE menu_items SET price_cents = 2200 WHERE id = 'item'")
        changed = self.quote()
        self.assertTrue(changed.price_changed)
        self.assertIsNone(changed.confirmation_id)
        self.assertEqual(changed.total_amount_cents, 2700)
        with self.assertRaisesRegex(CheckoutError, "重新查看"):
            self.quote(accept=True, fingerprint="old-quote")
        accepted = self.quote(accept=True, fingerprint=changed.fingerprint)
        self.assertIsNotNone(accepted.confirmation_id)
        with connect(self.path) as connection:
            connection.execute("UPDATE menu_items SET price_cents = 2300 WHERE id = 'item'")
        with self.assertRaisesRegex(CheckoutError, "重新确认"):
            create_order(self.path, "customer-0", accepted.confirmation_id)

    def test_outside_delivery_distance_is_rejected(self):
        with connect(self.path) as connection:
            connection.execute("INSERT INTO map_points VALUES ('far-point', '远处取件点', 'pickup', 9, 0, 1)")
            connection.execute("UPDATE addresses SET pickup_point_id = 'far-point' WHERE id = 'address-0'")
        with self.assertRaisesRegex(CheckoutError, "配送范围"):
            self.quote()

    def test_last_item_concurrent_competition(self):
        with connect(self.path) as connection:
            connection.execute("UPDATE menu_items SET available_quantity = 1 WHERE id = 'item'")
        confirmations = [self.quote(number).confirmation_id for number in range(10)]

        def attempt(number):
            try:
                create_order(self.path, f"customer-{number}", confirmations[number])
                return "success"
            except CheckoutError as error:
                return str(error)
            except sqlite3.OperationalError as error:
                return f"database error: {error}"

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(attempt, range(10)))
        self.assertEqual(results.count("success"), 1, results)
        self.assertEqual(results.count("库存不足"), 9, results)
        with connect(self.path) as connection:
            stock = connection.execute("SELECT available_quantity FROM menu_items WHERE id = 'item'").fetchone()[0]
            orders = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            self.assertEqual((stock, orders), (0, 1))


if __name__ == "__main__":
    unittest.main()
