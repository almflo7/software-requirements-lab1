"""账号申请、密码验证和管理员停用处置。"""

import hashlib
import hmac
import secrets
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.db import connect, run_write
from app.payment import _restore_stock, _time
from app.audit import audit_failures, write_audit_log


class IdentityError(ValueError):
    """身份或停用操作不满足要求。"""


def _password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000
    ).hex()


@audit_failures("register", "account_id")
def register(path: str | Path, account_id: str, password: str, phone: str,
             role: str = "customer", now: datetime | None = None) -> str:
    """顾客立即生效；商家和配送员进入审核队列。"""
    if role not in ("customer", "merchant", "courier") or len(password) < 8:
        raise IdentityError("角色无效或密码少于八位")
    if not account_id.strip() or not phone.strip():
        raise IdentityError("账号和电话不能为空")
    if not re.fullmatch(r"[0-9-]+", phone) or not any(char.isdigit() for char in phone):
        raise IdentityError("电话只能包含数字和连字符，且至少有一位数字")
    moment = _time(now)
    salt = secrets.token_hex(16)

    def operation(connection):
        if connection.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
            raise IdentityError("账号已存在")
        status = "active" if role == "customer" else "pending"
        connection.execute(
            "INSERT INTO accounts (id, role, status, phone) VALUES (?, ?, ?, ?)",
            (account_id, role, status, phone),
        )
        connection.execute(
            "INSERT INTO credentials VALUES (?, ?, ?)",
            (account_id, salt, _password_hash(password, salt)),
        )
        if status == "pending":
            connection.execute(
                """INSERT INTO approval_requests
                   (id, account_id, status, submitted_at) VALUES (?, ?, 'pending', ?)""",
                (uuid4().hex, account_id, moment.isoformat()),
            )
        write_audit_log(connection, account_id, None, "register", "success", role, moment)
        return status

    return run_write(path, operation)


@audit_failures("login", "account_id")
def login(path: str | Path, account_id: str, password: str,
          now: datetime | None = None) -> str:
    """返回仅在本机命令行使用的随机会话令牌。"""
    moment = _time(now)

    def operation(connection):
        row = connection.execute(
            """SELECT a.status, a.role, c.salt, c.password_hash
               FROM accounts AS a JOIN credentials AS c ON c.account_id = a.id
               WHERE a.id = ?""", (account_id,),
        ).fetchone()
        if not row or not hmac.compare_digest(
            row["password_hash"], _password_hash(password, row["salt"])
        ):
            raise IdentityError("账号或密码错误")
        if row["status"] == "pending":
            raise IdentityError("账号尚未审核通过")
        if row["status"] == "disabled" and row["role"] != "customer":
            raise IdentityError("账号已停用")
        token = secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO login_sessions VALUES (?, ?, ?)",
            (hashlib.sha256(token.encode()).hexdigest(), account_id,
             (moment + timedelta(hours=12)).isoformat()),
        )
        return token

    return run_write(path, operation)


def session_account(path: str | Path, token: str, now: datetime | None = None) -> str:
    moment = _time(now)
    with connect(path) as connection:
        row = connection.execute(
            "SELECT account_id, expires_at FROM login_sessions WHERE token_hash = ?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]) <= moment:
            raise IdentityError("会话无效或已过期")
        return row["account_id"]


@audit_failures("disable_account", "admin_id")
def disable_account(path: str | Path, admin_id: str, target_id: str,
                    cannot_fulfill_order_ids: tuple[str, ...] = (),
                    now: datetime | None = None) -> None:
    """停用账号；商家仅取消管理员确认无法履约的订单。"""
    moment = _time(now)

    def operation(connection):
        admin = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,),
        ).fetchone()
        target = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (target_id,),
        ).fetchone()
        if not admin or admin["role"] != "admin" or admin["status"] != "active":
            raise IdentityError("只有管理员可停用账号")
        if not target or target["role"] == "admin":
            raise IdentityError("目标账号无效")
        if target["role"] == "merchant":
            for order_id in cannot_fulfill_order_ids:
                order = connection.execute(
                    """SELECT o.* FROM orders AS o JOIN stores AS s ON s.id = o.store_id
                       WHERE o.id = ? AND s.merchant_id = ?""",
                    (order_id, target_id),
                ).fetchone()
                if not order or order["status"] in ("cancelled", "completed", "delivered"):
                    raise IdentityError("存在不属于该商家或不可取消的订单")
                connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
                if order["status"] in ("pending_payment", "awaiting_merchant"):
                    _restore_stock(connection, order_id)
                from app.after_sales import _refund_for_order
                _refund_for_order(connection, order, moment)
        elif target["role"] == "courier":
            active = connection.execute(
                """SELECT 1 FROM delivery_tasks AS t JOIN orders AS o ON o.id = t.order_id
                   WHERE t.courier_id = ? AND t.status IN ('picked_up', 'exception')
                     AND o.status NOT IN ('completed', 'cancelled')""",
                (target_id,),
            ).fetchone()
            if active:
                raise IdentityError("已取餐任务须先由管理员确认交接和改派")
            tasks = connection.execute(
                "SELECT order_id FROM delivery_tasks WHERE courier_id = ?", (target_id,),
            ).fetchall()
            for task in tasks:
                connection.execute(
                    "UPDATE delivery_tasks SET courier_id = NULL WHERE order_id = ?",
                    (task["order_id"],),
                )
                connection.execute(
                    """UPDATE orders SET status = 'awaiting_assignment'
                       WHERE id = ? AND status = 'awaiting_courier'""",
                    (task["order_id"],),
                )
        connection.execute("UPDATE accounts SET status = 'disabled' WHERE id = ?", (target_id,))
        write_audit_log(connection, admin_id, None, "disable_account", "success", target_id, moment)

    run_write(path, operation)


@audit_failures("disable_store", "admin_id")
def disable_store(path: str | Path, admin_id: str, store_id: str,
                  cannot_fulfill_order_ids: tuple[str, ...] = (),
                  now: datetime | None = None) -> None:
    """停用店铺后仅取消管理员确认无法继续履约的订单。"""
    moment = _time(now)

    def operation(connection):
        admin = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (admin_id,),
        ).fetchone()
        store = connection.execute("SELECT id FROM stores WHERE id = ?", (store_id,)).fetchone()
        if not admin or admin["role"] != "admin" or admin["status"] != "active" or not store:
            raise IdentityError("只有管理员可停用有效店铺")
        for order_id in cannot_fulfill_order_ids:
            order = connection.execute(
                "SELECT * FROM orders WHERE id = ? AND store_id = ?", (order_id, store_id),
            ).fetchone()
            if not order or order["status"] in ("cancelled", "completed", "delivered"):
                raise IdentityError("存在不属于该店铺或不可取消的订单")
            connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            if order["status"] in ("pending_payment", "awaiting_merchant"):
                _restore_stock(connection, order_id)
            from app.after_sales import _refund_for_order
            _refund_for_order(connection, order, moment)
        connection.execute("UPDATE stores SET status = 'disabled' WHERE id = ?", (store_id,))
        write_audit_log(connection, admin_id, None, "disable_store", "success",
                        store_id, moment, store_id=store_id)

    run_write(path, operation)


@audit_failures("save_address", "customer_id")
def save_address(path: str | Path, customer_id: str, recipient: str,
                 phone: str, address_text: str, area_code: str,
                 address_id: str | None = None, pickup_point_id: str | None = None) -> str:
    """顾客新增或修改本人常用地址。"""
    if not all(value.strip() for value in (recipient, phone, address_text)) or (not area_code and not pickup_point_id):
        raise IdentityError("收货信息不能为空")

    def operation(connection):
        customer = connection.execute(
            "SELECT role, status FROM accounts WHERE id = ?", (customer_id,),
        ).fetchone()
        if not customer or customer["role"] != "customer" or customer["status"] != "active":
            raise IdentityError("顾客账号不可修改地址")
        valid_area = connection.execute(
            "SELECT 1 FROM delivery_areas WHERE code = ? AND active = 1",
            (area_code,),
        ).fetchone()
        if not valid_area and not pickup_point_id:
            raise IdentityError("请选择管理员设置的配送区域")
        if pickup_point_id and not connection.execute(
            "SELECT 1 FROM map_points WHERE id = ? AND kind = 'pickup' AND active = 1",
            (pickup_point_id,),
        ).fetchone():
            raise IdentityError("请选择管理员开放的取件点")
        if address_id:
            updated = connection.execute(
                """UPDATE addresses SET recipient = ?, phone = ?, address_text = ?,
                   area_code = ?, pickup_point_id = ? WHERE id = ? AND customer_id = ?""",
                (recipient, phone, address_text, area_code, pickup_point_id, address_id, customer_id),
            )
            if updated.rowcount != 1:
                raise IdentityError("地址不存在或不属于该顾客")
            write_audit_log(connection, customer_id, None, "update_address",
                            "success", address_id)
            return address_id
        new_id = uuid4().hex
        connection.execute(
            """INSERT INTO addresses
               (id, customer_id, recipient, phone, address_text, area_code, pickup_point_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (new_id, customer_id, recipient, phone, address_text, area_code, pickup_point_id),
        )
        write_audit_log(connection, customer_id, None, "add_address", "success", new_id)
        return new_id

    return run_write(path, operation)


@audit_failures("update_phone", "customer_id")
def update_phone(path: str | Path, customer_id: str, phone: str) -> None:
    if not phone.strip():
        raise IdentityError("联系电话不能为空")

    def operation(connection):
        result = connection.execute(
            """UPDATE accounts SET phone = ? WHERE id = ?
               AND role = 'customer' AND status = 'active'""",
            (phone, customer_id),
        )
        if result.rowcount != 1:
            raise IdentityError("顾客账号不可修改资料")
        write_audit_log(connection, customer_id, None, "update_phone", "success")

    run_write(path, operation)
