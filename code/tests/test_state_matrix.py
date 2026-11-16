"""对 11 种订单状态的有向转换逐项核对。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.cli import seed_demo
from app.db import connect
from app.order_state import ALLOWED_TRANSITIONS, StateError, transition_order


class StateMatrixTests(unittest.TestCase):
    def test_all_directed_status_pairs(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "states.sqlite3"
        seed_demo(path)
        states = tuple(ALLOWED_TRANSITIONS)
        self.assertEqual(len(states), 11)
        with connect(path) as connection:
            for source in states:
                connection.execute(
                    """INSERT INTO orders
                       (id, customer_id, store_id, store_name_snapshot, recipient_snapshot,
                        phone_snapshot, address_snapshot, status, food_amount_cents,
                        delivery_fee_cents, total_amount_cents, note, placed_at)
                       VALUES (?, 'customer-demo', 'store-demo', '店', '人', '100', '地址',
                               ?, 100, 0, 100, '', '2026-11-14T00:00:00+00:00')""",
                    (source, source),
                )
            checked = 0
            for source in states:
                for target in states:
                    if target == source:
                        continue
                    connection.execute("SAVEPOINT transition_case")
                    if target in ALLOWED_TRANSITIONS[source]:
                        transition_order(connection, source, source, target)
                        actual = connection.execute(
                            "SELECT status FROM orders WHERE id = ?", (source,)
                        ).fetchone()[0]
                        self.assertEqual(actual, target)
                    else:
                        with self.assertRaises(StateError):
                            transition_order(connection, source, source, target)
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                "UPDATE orders SET status = ? WHERE id = ?", (target, source)
                            )
                    connection.execute("ROLLBACK TO transition_case")
                    connection.execute("RELEASE transition_case")
                    checked += 1
            self.assertEqual(checked, 110)


if __name__ == "__main__":
    unittest.main()
