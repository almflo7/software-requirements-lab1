"""订单状态图的集中定义与事务内状态转换。"""

ALLOWED_TRANSITIONS = {
    "pending_payment": frozenset(("awaiting_merchant", "cancelled")),
    "awaiting_merchant": frozenset(("preparing", "cancelled")),
    "preparing": frozenset(("awaiting_assignment", "cancelled")),
    "awaiting_assignment": frozenset(("awaiting_courier", "cancelled")),
    "awaiting_courier": frozenset(("awaiting_assignment", "awaiting_pickup", "cancelled")),
    "awaiting_pickup": frozenset(("delivering", "cancelled")),
    "delivering": frozenset(("delivered", "delivery_exception", "cancelled")),
    "delivery_exception": frozenset(("delivering", "cancelled")),
    "delivered": frozenset(("completed",)),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


class StateError(ValueError):
    """请求的状态转换不在订单状态图中。"""


def transition_order(connection, order_id: str, expected: str, target: str) -> None:
    """服务已完成角色检查后，在当前写事务中核对并更新状态。"""
    if target not in ALLOWED_TRANSITIONS.get(expected, frozenset()):
        raise StateError("订单状态不允许此转换")
    result = connection.execute(
        "UPDATE orders SET status = ? WHERE id = ? AND status = ?",
        (target, order_id, expected),
    )
    if result.rowcount != 1:
        raise StateError("订单状态已变化，请刷新后重试")
