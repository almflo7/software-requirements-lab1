"""管理员查看争议信息须留痕。"""

import tempfile
import unittest
from pathlib import Path

from app.audit import query_audit_logs
from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.disputes import open_dispute, resolve_dispute, view_dispute


class DisputeTests(unittest.TestCase):
    def test_admin_view_is_logged(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "dispute.sqlite3"
        seed_demo(path)
        token = confirm_checkout(path, "customer-demo", "cart-demo", "address-demo").confirmation_id
        order = create_order(path, "customer-demo", token)
        dispute_id = open_dispute(path, "customer-demo", order.order_id, "需要核查")
        self.assertEqual(view_dispute(path, "admin-demo", dispute_id)["status"], "open")
        logs = query_audit_logs(path, "admin-demo", order.order_id)
        self.assertIn("view_dispute", [record["action"] for record in logs])
        resolve_dispute(path, "admin-demo", dispute_id, "已核查")
        self.assertEqual(view_dispute(path, "admin-demo", dispute_id)["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
