"""中断并重启到期任务，核对订单和资金记录。"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.checkout import confirm_checkout, create_order
from app.cli import seed_demo
from app.db import connect


def run_recovery(path: Path) -> dict:
    seed_demo(path)
    with connect(path) as connection:
        connection.execute("UPDATE menu_items SET available_quantity = 200 WHERE id = 'item-demo'")
    for number in range(100):
        confirmation = confirm_checkout(
            path, "customer-demo", "cart-demo", "address-demo"
        ).confirmation_id
        create_order(path, "customer-demo", confirmation, f"recovery-{number}")
    with connect(path) as connection:
        connection.execute("UPDATE orders SET placed_at = '2000-01-01T00:00:00+00:00'")
        connection.execute("UPDATE scheduled_jobs SET due_at = '2000-01-01T00:00:00+00:00'")
    command = [sys.executable, "-m", "app.cli", "--db", str(path), "run-jobs"]
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.05)
    if process.poll() is None:
        process.kill()
    process.wait()
    time.sleep(6)
    restarted = subprocess.run(command, capture_output=True, text=True, check=True)
    elapsed = time.monotonic() - started
    with connect(path) as connection:
        orders = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        cancelled = connection.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'cancelled'"
        ).fetchone()[0]
        payments = connection.execute("SELECT COUNT(*) FROM payment_records").fetchone()[0]
        refunds = connection.execute("SELECT COUNT(*) FROM refund_records").fetchone()[0]
        pending_jobs = connection.execute(
            "SELECT COUNT(*) FROM scheduled_jobs WHERE status != 'completed'"
        ).fetchone()[0]
    return {"orders": orders, "cancelled": cancelled, "payments": payments,
            "refunds": refunds, "pending_jobs": pending_jobs, "restart_seconds": elapsed,
            "restart_output": restarted.stdout.strip(),
            "passed": orders == 100 and cancelled == 100 and payments == 0
            and refunds == 0 and pending_jobs == 0 and elapsed <= 600}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    arguments = parser.parse_args()
    if arguments.db:
        result = run_recovery(arguments.db)
    else:
        with tempfile.TemporaryDirectory() as folder:
            result = run_recovery(Path(folder) / "recovery.sqlite3")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
