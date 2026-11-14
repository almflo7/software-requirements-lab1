"""按角色查询完成订单、退款和热销菜品。"""

from pathlib import Path

from app.db import connect


class ReportingError(ValueError):
    """统计查询权限或日期无效。"""


def query_statistics(path: str | Path, account_id: str,
                     date_from: str, date_to: str) -> dict:
    """营业额按完成日，退款按成功日，热销数量不回改。"""
    if date_from > date_to:
        raise ReportingError("日期范围无效")
    with connect(path) as connection:
        actor = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not actor or actor["status"] != "active" or actor["role"] not in ("merchant", "admin"):
            raise ReportingError("无权查看经营统计")
        store_ids = None
        if actor["role"] == "merchant":
            store_ids = [row["id"] for row in connection.execute(
                "SELECT id FROM stores WHERE merchant_id = ?", (account_id,)
            )]
        completed = connection.execute(
            """SELECT id, store_id, total_amount_cents FROM orders
               WHERE status = 'completed' AND substr(completed_at, 1, 10) BETWEEN ? AND ?""",
            (date_from, date_to),
        ).fetchall()
        selected = [row for row in completed if store_ids is None or row["store_id"] in store_ids]
        order_ids = [row["id"] for row in selected]
        hot = {}
        for order_id in order_ids:
            for item in connection.execute(
                "SELECT item_name_snapshot, quantity FROM order_items WHERE order_id = ?", (order_id,)
            ):
                hot[item["item_name_snapshot"]] = hot.get(item["item_name_snapshot"], 0) + item["quantity"]
        refunds = connection.execute(
            """SELECT r.amount_cents, o.store_id FROM refund_records AS r
               JOIN orders AS o ON o.id = r.order_id
               WHERE r.result = 'succeeded' AND substr(r.result_at, 1, 10) BETWEEN ? AND ?""",
            (date_from, date_to),
        ).fetchall()
        refunded = sum(row["amount_cents"] for row in refunds
                       if store_ids is None or row["store_id"] in store_ids)
        revenue = sum(row["total_amount_cents"] for row in selected)
        return {
            "completed_orders": len(selected),
            "revenue_cents": revenue,
            "refund_cents": refunded,
            "difference_cents": revenue - refunded,
            "popular_items": sorted(hot.items(), key=lambda pair: (-pair[1], pair[0])),
        }


def list_history(path: str | Path, account_id: str) -> list[dict]:
    """顾客查本人订单，商家查本店订单，配送员查本人历史任务。"""
    with connect(path) as connection:
        actor = connection.execute(
            "SELECT role FROM accounts WHERE id = ?", (account_id,),
        ).fetchone()
        if not actor:
            raise ReportingError("账号不存在")
        if actor["role"] == "customer":
            rows = connection.execute(
                """SELECT id, status, total_amount_cents, placed_at FROM orders
                   WHERE customer_id = ? ORDER BY placed_at DESC""", (account_id,),
            ).fetchall()
        elif actor["role"] == "merchant":
            rows = connection.execute(
                """SELECT o.id, o.status, o.total_amount_cents, o.placed_at
                   FROM orders AS o JOIN stores AS s ON s.id = o.store_id
                   WHERE s.merchant_id = ? ORDER BY o.placed_at DESC""",
                (account_id,),
            ).fetchall()
        elif actor["role"] == "courier":
            rows = connection.execute(
                """SELECT DISTINCT o.id, o.status, o.total_amount_cents, o.placed_at
                   FROM orders AS o JOIN delivery_tasks AS t ON t.order_id = o.id
                   JOIN assignment_records AS a ON a.task_id = t.id
                   WHERE a.new_courier_id = ? AND o.status IN ('completed', 'cancelled')
                   ORDER BY o.placed_at DESC""",
                (account_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, status, total_amount_cents, placed_at FROM orders ORDER BY placed_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]
