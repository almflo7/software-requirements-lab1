"""四类操作的三十用户、十分钟负载测试。"""

import argparse
import json
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.access import view_order
from app.catalog import search_catalog
from app.checkout import CheckoutError, confirm_checkout, create_order
from app.db import connect
from scripts.seed_data import seed_performance


def percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int((len(ordered) - 1) * 0.95 + 0.5))
    return ordered[index]


def run_load(path: Path, seconds: int = 600, users: int = 30,
             think_seconds: float = 0.1) -> dict:
    seed_performance(path)
    samples: dict[str, list[float]] = {
        "store_query": [], "menu_query": [], "order_query": [], "submit_order": []
    }
    failures: dict[str, int] = {name: 0 for name in samples}
    lock = threading.Lock()
    deadline = time.perf_counter() + seconds

    def worker(number: int):
        customer = f"customer-{number}"
        operations = tuple(samples)
        cycle = 0
        while time.perf_counter() < deadline:
            name = operations[cycle % len(operations)]
            started = time.perf_counter()
            try:
                if name == "store_query":
                    with connect(path) as connection:
                        connection.execute("SELECT id, name FROM stores WHERE name LIKE ?", ("%店铺%",)).fetchall()
                elif name == "menu_query":
                    search_catalog(path, "菜品1", "主食", 0, 5000)
                elif name == "order_query":
                    view_order(path, customer, f"history-{number}")
                else:
                    quote = confirm_checkout(path, customer, f"cart-{number}", f"address-{number}")
                    create_order(path, customer, quote.confirmation_id)
                failed = False
            except CheckoutError as error:
                # 预期的库存或状态拦截不算系统故障。
                failed = "库存不足" not in str(error)
            except Exception:
                failed = True
            elapsed = time.perf_counter() - started
            with lock:
                samples[name].append(elapsed)
                failures[name] += int(failed)
            cycle += 1
            time.sleep(think_seconds)

    with ThreadPoolExecutor(max_workers=users) as pool:
        list(pool.map(worker, range(users)))
    summary = {
        name: {"count": len(values), "p95_seconds": percentile_95(values),
               "success_rate": (len(values) - failures[name]) / len(values) if values else 0.0}
        for name, values in samples.items()
    }
    return {"users": users, "seconds": seconds, "think_seconds": think_seconds,
            "python": platform.python_version(),
            "platform": platform.platform(), "database": str(path), "network": "local",
            "operations": summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("performance.sqlite3"))
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--users", type=int, default=30)
    parser.add_argument("--think-seconds", type=float, default=0.1)
    arguments = parser.parse_args()
    result = run_load(arguments.db, arguments.seconds, arguments.users,
                      arguments.think_seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(item["p95_seconds"] > 2 or item["success_rate"] < 0.99
           for item in result["operations"].values()):
        raise SystemExit(1)
