"""与 SQLite 记录对应的不可变业务对象。金额统一使用整数分。"""

from dataclasses import dataclass, fields
from typing import TypeVar


@dataclass(frozen=True)
class Record:
    """允许既有业务流程按数据库列名读取不可变对象。"""

    def __getitem__(self, key: str):
        return getattr(self, key)


RecordType = TypeVar("RecordType", bound=Record)


def from_row(row, record_type: type[RecordType]) -> RecordType:
    """只接受与数据类声明一致的数据库列，避免位置参数错配。"""
    names = {field.name for field in fields(record_type)}
    return record_type(**{key: row[key] for key in row.keys() if key in names})


@dataclass(frozen=True)
class Account(Record):
    id: str
    role: str
    status: str
    phone: str


@dataclass(frozen=True)
class ApprovalRequest(Record):
    id: str
    account_id: str
    status: str
    submitted_at: str
    reviewed_by: str | None
    reviewed_at: str | None


@dataclass(frozen=True)
class CustomerAddress(Record):
    id: str
    customer_id: str
    recipient: str
    phone: str
    address_text: str
    area_code: str


@dataclass(frozen=True)
class Store(Record):
    id: str
    merchant_id: str
    name: str
    status: str
    delivery_areas: str
    delivery_fee_cents: int
    description: str
    business_hours: str


@dataclass(frozen=True)
class MenuItem(Record):
    id: str
    store_id: str
    name: str
    category: str
    price_cents: int
    available_quantity: int
    status: str


@dataclass(frozen=True)
class Cart(Record):
    id: str
    customer_id: str
    store_id: str


@dataclass(frozen=True)
class CartItem(Record):
    cart_id: str
    item_id: str
    quantity: int
    last_shown_price_cents: int


@dataclass(frozen=True)
class CheckoutItem(Record):
    item_id: str
    quantity: int
    last_shown_price_cents: int
    name: str
    price_cents: int
    available_quantity: int
    status: str
    store_id: str


@dataclass(frozen=True)
class CheckoutConfirmation(Record):
    id: str
    cart_id: str
    customer_id: str
    address_id: str
    fingerprint: str
    confirmed_at: str
    order_id: str | None


@dataclass(frozen=True)
class Order(Record):
    id: str
    customer_id: str
    store_id: str
    store_name_snapshot: str
    recipient_snapshot: str
    phone_snapshot: str
    address_snapshot: str
    status: str
    food_amount_cents: int
    delivery_fee_cents: int
    total_amount_cents: int
    note: str
    placed_at: str
    completed_at: str | None


@dataclass(frozen=True)
class OrderItemSnapshot(Record):
    order_id: str
    item_id: str
    item_name_snapshot: str
    unit_price_cents: int
    quantity: int
    subtotal_cents: int


@dataclass(frozen=True)
class DeliveryInfoSnapshot(Record):
    recipient: str
    phone: str
    address_text: str

    @classmethod
    def from_order(cls, order: Order):
        return cls(order.recipient_snapshot, order.phone_snapshot,
                   order.address_snapshot)


@dataclass(frozen=True)
class StockReservation(Record):
    order_id: str
    item_id: str
    quantity: int
    restored: int


@dataclass(frozen=True)
class PaymentRecord(Record):
    request_id: str
    order_id: str
    result: str
    failure_reason: str | None
    requested_at: str
    result_at: str | None


@dataclass(frozen=True)
class CancellationRequest(Record):
    id: str
    order_id: str
    customer_id: str
    reason: str
    status: str
    submitted_at: str
    decided_at: str | None
    decided_by: str | None


@dataclass(frozen=True)
class RefundRecord(Record):
    id: str
    order_id: str
    payment_request_id: str
    amount_cents: int
    result: str
    initiated_at: str
    result_at: str | None
    failure_reason: str | None


@dataclass(frozen=True)
class DisputeCase(Record):
    id: str
    order_id: str
    opened_by: str
    reason: str
    status: str
    opened_at: str
    decided_by: str | None
    decision: str | None
    decided_at: str | None


@dataclass(frozen=True)
class DeliveryTask(Record):
    id: str
    order_id: str
    courier_id: str | None
    status: str
    assigned_at: str
    arrived_at: str | None


@dataclass(frozen=True)
class AssignmentRecord(Record):
    id: str
    task_id: str
    admin_id: str
    previous_courier_id: str | None
    new_courier_id: str
    assigned_at: str
    handover_confirmed: int
    response: str | None
    response_reason: str | None
    responded_at: str | None


@dataclass(frozen=True)
class DeliveryContact(Record):
    id: str
    task_id: str
    courier_id: str
    contacted_at: str
    target: str
    method: str
    result: str


@dataclass(frozen=True)
class DeliveryException(Record):
    id: str
    task_id: str
    reason: str
    reported_at: str
    status: str
    resolved_action: str | None
    decided_by: str | None
    decided_at: str | None


@dataclass(frozen=True)
class NotificationRecord(Record):
    id: str
    order_id: str
    recipient_id: str
    content: str
    created_at: str
    status: str
    attempt_count: int
    delivered_at: str | None
    next_retry_at: str | None
    force_failures: int
    admin_exception: int


@dataclass(frozen=True)
class AuditLog(Record):
    id: str
    actor_id: str | None
    order_id: str | None
    store_id: str | None
    action: str
    result: str
    reason: str | None
    occurred_at: str


@dataclass(frozen=True)
class ScheduledJob(Record):
    id: str
    kind: str
    business_id: str
    due_at: str
    status: str
    claimed_by: str | None
    lease_until: str | None


@dataclass(frozen=True)
class Quote:
    food_amount_cents: int
    delivery_fee_cents: int
    total_amount_cents: int
    price_changed: bool
    fingerprint: str
    confirmation_id: str | None


@dataclass(frozen=True)
class CreatedOrder:
    order_id: str
    status: str
    total_amount_cents: int
    reused: bool
