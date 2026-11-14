PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('customer', 'merchant', 'courier', 'admin')),
    status TEXT NOT NULL CHECK (status IN ('active', 'pending', 'disabled')),
    phone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    submitted_at TEXT NOT NULL,
    reviewed_by TEXT REFERENCES accounts(id),
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS credentials (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id),
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS addresses (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES accounts(id),
    recipient TEXT NOT NULL,
    phone TEXT NOT NULL,
    address_text TEXT NOT NULL,
    area_code TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stores (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES accounts(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'paused', 'disabled')),
    delivery_areas TEXT NOT NULL,
    delivery_fee_cents INTEGER NOT NULL CHECK (delivery_fee_cents >= 0),
    description TEXT NOT NULL DEFAULT '',
    business_hours TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS menu_items (
    id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL REFERENCES stores(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    available_quantity INTEGER NOT NULL CHECK (available_quantity >= 0),
    status TEXT NOT NULL CHECK (status IN ('listed', 'unlisted'))
);
CREATE TABLE IF NOT EXISTS carts (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES accounts(id),
    store_id TEXT NOT NULL REFERENCES stores(id)
);
CREATE TABLE IF NOT EXISTS cart_items (
    cart_id TEXT NOT NULL REFERENCES carts(id),
    item_id TEXT NOT NULL REFERENCES menu_items(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    last_shown_price_cents INTEGER NOT NULL CHECK (last_shown_price_cents >= 0),
    PRIMARY KEY (cart_id, item_id)
);
CREATE TABLE IF NOT EXISTS checkout_confirmations (
    id TEXT PRIMARY KEY,
    cart_id TEXT NOT NULL REFERENCES carts(id),
    customer_id TEXT NOT NULL REFERENCES accounts(id),
    address_id TEXT NOT NULL REFERENCES addresses(id),
    fingerprint TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    order_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES accounts(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    store_name_snapshot TEXT NOT NULL,
    recipient_snapshot TEXT NOT NULL,
    phone_snapshot TEXT NOT NULL,
    address_snapshot TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_payment', 'awaiting_merchant', 'preparing', 'awaiting_assignment', 'awaiting_courier', 'awaiting_pickup', 'delivering', 'delivered', 'completed', 'cancelled', 'delivery_exception')),
    food_amount_cents INTEGER NOT NULL,
    delivery_fee_cents INTEGER NOT NULL,
    total_amount_cents INTEGER NOT NULL,
    note TEXT NOT NULL,
    placed_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL REFERENCES orders(id),
    item_id TEXT NOT NULL REFERENCES menu_items(id),
    item_name_snapshot TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    subtotal_cents INTEGER NOT NULL,
    PRIMARY KEY (order_id, item_id)
);
CREATE TABLE IF NOT EXISTS stock_reservations (
    order_id TEXT NOT NULL REFERENCES orders(id),
    item_id TEXT NOT NULL REFERENCES menu_items(id),
    quantity INTEGER NOT NULL,
    restored INTEGER NOT NULL DEFAULT 0 CHECK (restored IN (0, 1)),
    PRIMARY KEY (order_id, item_id)
);
CREATE TABLE IF NOT EXISTS payment_records (
    request_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    result TEXT NOT NULL CHECK (result IN ('pending', 'failed', 'succeeded')),
    failure_reason TEXT,
    requested_at TEXT NOT NULL,
    result_at TEXT
);
CREATE TABLE IF NOT EXISTS merchant_responses (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    merchant_id TEXT NOT NULL REFERENCES accounts(id),
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
    reason TEXT,
    responded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refund_records (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
    payment_request_id TEXT NOT NULL REFERENCES payment_records(request_id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    result TEXT NOT NULL CHECK (result IN ('pending', 'failed', 'succeeded')),
    initiated_at TEXT NOT NULL,
    result_at TEXT,
    failure_reason TEXT
);
CREATE TRIGGER IF NOT EXISTS schedule_refund_settlement AFTER INSERT ON refund_records
BEGIN
    INSERT OR IGNORE INTO scheduled_jobs (id, kind, business_id, due_at, status)
    VALUES (lower(hex(randomblob(16))), 'refund_settle', NEW.order_id,
            strftime('%Y-%m-%dT%H:%M:%f+00:00', NEW.initiated_at, '+5 minutes'),
            'pending');
END;
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    business_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_by TEXT,
    lease_until TEXT,
    UNIQUE (kind, business_id)
);
CREATE TABLE IF NOT EXISTS cancellation_requests (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    customer_id TEXT NOT NULL REFERENCES accounts(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    submitted_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT REFERENCES accounts(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_cancel
ON cancellation_requests(order_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS delivery_tasks (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
    courier_id TEXT REFERENCES accounts(id),
    status TEXT NOT NULL CHECK (status IN ('offered', 'accepted', 'picked_up', 'delivered', 'exception')),
    assigned_at TEXT NOT NULL,
    arrived_at TEXT
);
CREATE TABLE IF NOT EXISTS assignment_records (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES delivery_tasks(id),
    admin_id TEXT NOT NULL REFERENCES accounts(id),
    previous_courier_id TEXT REFERENCES accounts(id),
    new_courier_id TEXT NOT NULL REFERENCES accounts(id),
    assigned_at TEXT NOT NULL,
    handover_confirmed INTEGER NOT NULL DEFAULT 0,
    response TEXT,
    response_reason TEXT,
    responded_at TEXT
);
CREATE TABLE IF NOT EXISTS delivery_contacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES delivery_tasks(id),
    courier_id TEXT NOT NULL REFERENCES accounts(id),
    contacted_at TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT 'customer'
        CHECK (target IN ('customer', 'merchant', 'admin')),
    method TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('reached', 'unreachable'))
);
CREATE TABLE IF NOT EXISTS delivery_exceptions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES delivery_tasks(id),
    reason TEXT NOT NULL,
    reported_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'continue', 'terminate')),
    resolved_action TEXT,
    decided_by TEXT REFERENCES accounts(id),
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS notification_records (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    recipient_id TEXT NOT NULL REFERENCES accounts(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    next_retry_at TEXT,
    force_failures INTEGER NOT NULL DEFAULT 0,
    admin_exception INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS notify_order_created AFTER INSERT ON orders
BEGIN
    INSERT INTO notification_records (id, order_id, recipient_id, content, created_at, status)
    VALUES (lower(hex(randomblob(16))), NEW.id, NEW.customer_id, NEW.status,
            strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'), 'pending');
END;
CREATE TRIGGER IF NOT EXISTS notify_order_status AFTER UPDATE OF status ON orders
WHEN OLD.status != NEW.status
BEGIN
    INSERT INTO notification_records (id, order_id, recipient_id, content, created_at, status)
    VALUES (lower(hex(randomblob(16))), NEW.id, NEW.customer_id, NEW.status,
            strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'), 'pending');
    INSERT INTO notification_records (id, order_id, recipient_id, content, created_at, status)
    SELECT lower(hex(randomblob(16))), NEW.id, s.merchant_id, NEW.status,
           strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'), 'pending'
    FROM stores AS s WHERE s.id = NEW.store_id;
    INSERT INTO notification_records (id, order_id, recipient_id, content, created_at, status)
    SELECT lower(hex(randomblob(16))), NEW.id, t.courier_id, NEW.status,
           strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'), 'pending'
    FROM delivery_tasks AS t WHERE t.order_id = NEW.id AND t.courier_id IS NOT NULL;
END;
CREATE TRIGGER IF NOT EXISTS notify_refund_result AFTER UPDATE OF result ON refund_records
WHEN OLD.result != NEW.result
BEGIN
    INSERT INTO notification_records (id, order_id, recipient_id, content, created_at, status)
    SELECT lower(hex(randomblob(16))), NEW.order_id, o.customer_id,
           'refund_' || NEW.result, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'), 'pending'
    FROM orders AS o WHERE o.id = NEW.order_id;
END;
CREATE TRIGGER IF NOT EXISTS guard_order_status BEFORE UPDATE OF status ON orders
WHEN OLD.status != NEW.status AND NOT (
    (OLD.status = 'pending_payment' AND NEW.status IN ('awaiting_merchant', 'cancelled')) OR
    (OLD.status = 'awaiting_merchant' AND NEW.status IN ('preparing', 'cancelled')) OR
    (OLD.status = 'preparing' AND NEW.status IN ('awaiting_assignment', 'cancelled')) OR
    (OLD.status = 'awaiting_assignment' AND NEW.status IN ('awaiting_courier', 'cancelled')) OR
    (OLD.status = 'awaiting_courier' AND NEW.status IN ('awaiting_assignment', 'awaiting_pickup', 'cancelled')) OR
    (OLD.status = 'awaiting_pickup' AND NEW.status IN ('delivering', 'cancelled')) OR
    (OLD.status = 'delivering' AND NEW.status IN ('delivered', 'delivery_exception', 'cancelled')) OR
    (OLD.status = 'delivery_exception' AND NEW.status IN ('delivering', 'cancelled')) OR
    (OLD.status = 'delivered' AND NEW.status = 'completed')
)
BEGIN
    SELECT RAISE(ABORT, 'illegal order transition');
END;
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    actor_id TEXT REFERENCES accounts(id),
    order_id TEXT REFERENCES orders(id),
    store_id TEXT REFERENCES stores(id),
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit logs are append only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit logs are append only');
END;
CREATE TRIGGER IF NOT EXISTS audit_order_status AFTER UPDATE OF status ON orders
WHEN OLD.status != NEW.status
BEGIN
    INSERT INTO audit_logs (id, actor_id, order_id, action, result, reason, occurred_at)
    VALUES (lower(hex(randomblob(16))), NULL, NEW.id, 'order_status', 'success',
            OLD.status || ' -> ' || NEW.status, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));
END;
CREATE TRIGGER IF NOT EXISTS audit_stock_update AFTER UPDATE OF available_quantity ON menu_items
WHEN OLD.available_quantity != NEW.available_quantity
BEGIN
    INSERT INTO audit_logs (id, actor_id, order_id, store_id, action, result, reason, occurred_at)
    VALUES (lower(hex(randomblob(16))), NULL, NULL, NEW.store_id, 'stock_change', 'success',
            NEW.id || ': ' || OLD.available_quantity || ' -> ' || NEW.available_quantity,
            strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));
END;
CREATE TRIGGER IF NOT EXISTS audit_refund_result AFTER UPDATE OF result ON refund_records
WHEN OLD.result != NEW.result
BEGIN
    INSERT INTO audit_logs (id, actor_id, order_id, action, result, reason, occurred_at)
    VALUES (lower(hex(randomblob(16))), NULL, NEW.order_id, 'refund_result', NEW.result,
            NEW.failure_reason, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));
END;
CREATE TRIGGER IF NOT EXISTS audit_refund_start AFTER INSERT ON refund_records
BEGIN
    INSERT INTO audit_logs (id, actor_id, order_id, action, result, reason, occurred_at)
    VALUES (lower(hex(randomblob(16))), NULL, NEW.order_id, 'refund_start', 'success',
            NULL, NEW.initiated_at);
END;
CREATE TRIGGER IF NOT EXISTS audit_assignment AFTER INSERT ON assignment_records
BEGIN
    INSERT INTO audit_logs (id, actor_id, order_id, action, result, reason, occurred_at)
    SELECT lower(hex(randomblob(16))), NEW.admin_id, t.order_id, 'assign_courier',
           'success', COALESCE(NEW.previous_courier_id, 'unassigned') || ' -> ' || NEW.new_courier_id,
           NEW.assigned_at FROM delivery_tasks AS t WHERE t.id = NEW.task_id;
END;
CREATE TRIGGER IF NOT EXISTS audit_exception AFTER INSERT ON delivery_exceptions
BEGIN
    INSERT INTO audit_logs (id, actor_id, order_id, action, result, reason, occurred_at)
    SELECT lower(hex(randomblob(16))), t.courier_id, t.order_id, 'delivery_exception',
           'success', NEW.reason, NEW.reported_at
    FROM delivery_tasks AS t WHERE t.id = NEW.task_id;
END;
CREATE TABLE IF NOT EXISTS dispute_cases (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    opened_by TEXT NOT NULL REFERENCES accounts(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    opened_at TEXT NOT NULL,
    decided_by TEXT REFERENCES accounts(id),
    decision TEXT,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_menu_store ON menu_items(store_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, placed_at);
CREATE INDEX IF NOT EXISTS idx_payment_order ON payment_records(order_id, result);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON scheduled_jobs(status, due_at);
CREATE INDEX IF NOT EXISTS idx_delivery_courier ON delivery_tasks(courier_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_order ON audit_logs(order_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_notification_pending
ON notification_records(status, next_retry_at, created_at);
