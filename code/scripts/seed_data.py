"""生成约百家店铺、千个菜品和万笔历史订单。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import connect, initialize_schema


def seed_performance(path: str | Path) -> None:
    initialize_schema(path)
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.executemany(
                "INSERT OR IGNORE INTO accounts VALUES (?, ?, 'active', '100')",
                [(f"merchant-{number}", "merchant") for number in range(100)] +
                [(f"customer-{number}", "customer") for number in range(30)],
            )
            connection.executemany(
                """INSERT OR IGNORE INTO stores
                   (id, merchant_id, name, status, delivery_areas, delivery_fee_cents)
                   VALUES (?, ?, ?, 'open', ?, 500)""",
                [(f"store-{number}", f"merchant-{number}", f"店铺{number}",
                  json.dumps([("area", "east", "west")[number % 3]]))
                 for number in range(100)],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO map_points VALUES (?, ?, 'store', 3, 0, 1)",
                [(f"store-point-{number}", f"店铺{number}取餐点") for number in range(100)],
            )
            connection.execute("""UPDATE stores SET store_point_id = 'store-point-' ||
                                  substr(id, 7) WHERE id LIKE 'store-%'""")
            connection.executemany(
                "INSERT OR IGNORE INTO menu_items VALUES (?, ?, ?, '主食', 2000, 100000, 'listed')",
                [(f"item-{number}", f"store-{number // 10}", f"菜品{number}")
                 for number in range(1000)],
            )
            for number in range(30):
                connection.execute(
                    "INSERT OR IGNORE INTO addresses (id, customer_id, recipient, phone, address_text, area_code) VALUES (?, ?, '顾客', '100', '地址', 'area')",
                    (f"address-{number}", f"customer-{number}"),
                )
                connection.execute("UPDATE addresses SET pickup_point_id = 'pickup-xianlin' WHERE id = ?",
                                   (f"address-{number}",))
                connection.execute(
                    "INSERT OR IGNORE INTO carts VALUES (?, ?, 'store-0')",
                    (f"cart-{number}", f"customer-{number}"),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO cart_items VALUES (?, 'item-0', 1, 2000)",
                    (f"cart-{number}",),
                )
            connection.executemany(
                """INSERT OR IGNORE INTO orders
                   (id, customer_id, store_id, store_name_snapshot, recipient_snapshot,
                    phone_snapshot, address_snapshot, status, food_amount_cents,
                    delivery_fee_cents, total_amount_cents, note, placed_at, completed_at)
                   VALUES (?, ?, ?, ?, '顾客', '100', '地址', 'completed',
                           2000, 500, 2500, '', ?, ?)""",
                [(f"history-{number}", f"customer-{number % 30}",
                  f"store-{number % 100}", f"店铺{number % 100}", now, now)
                 for number in range(10000)],
            )
            connection.execute("DELETE FROM notification_records")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("performance.sqlite3"))
    args = parser.parse_args()
    seed_performance(args.db)
    print(f"性能数据已写入 {args.db}")
