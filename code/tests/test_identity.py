"""注册、登录与停用后的只读权限。"""

import tempfile
import unittest
from pathlib import Path

from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect
from app.identity import IdentityError, disable_account, login, register, session_account
from app.access import view_order


class IdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "identity.sqlite3"
        seed_demo(self.path)

    def test_registration_and_session(self):
        self.assertEqual(register(self.path, "new-customer", "strong-pass", "100"), "active")
        token = login(self.path, "new-customer", "strong-pass")
        self.assertEqual(session_account(self.path, token), "new-customer")
        with self.assertRaises(IdentityError):
            login(self.path, "new-customer", "wrong-pass")
        self.assertEqual(register(self.path, "new-courier", "strong-pass", "200", "courier"), "pending")
        with self.assertRaisesRegex(IdentityError, "审核"):
            login(self.path, "new-courier", "strong-pass")

    def test_disabled_customer_sees_existing_order(self):
        token = confirm_checkout(self.path, "customer-demo", "cart-demo", "address-demo").confirmation_id
        order = create_order(self.path, "customer-demo", token)
        disable_account(self.path, "admin-demo", "customer-demo")
        login_token = login(self.path, "customer-demo", "demo-pass-123")
        self.assertEqual(session_account(self.path, login_token), "customer-demo")
        self.assertEqual(view_order(self.path, "customer-demo", order.order_id)["status"], "pending_payment")
        with self.assertRaisesRegex(ValueError, "不可下单"):
            confirm_checkout(self.path, "customer-demo", "cart-demo", "address-demo")


if __name__ == "__main__":
    unittest.main()
