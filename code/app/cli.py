"""外卖点餐与配送系统命令行入口。"""

import argparse
import json
import os
import sqlite3
import time
from getpass import getpass
from dataclasses import asdict
from pathlib import Path

from app.checkout import CheckoutError, confirm_checkout, create_order
from app.after_sales import (CancellationError, apply_refund_result,
                             decide_cancellation, list_failed_refunds,
                             request_cancellation,
                             retry_failed_refund)
from app.access import AccessError, view_order
from app.catalog import (CatalogError, add_menu_item, add_store, create_cart,
                         change_cart_quantity,
                         review_account, search_catalog, update_menu_item, update_store)
from app.categories import list_categories, manage_category
from app.map_points import (nearby_points, save_point, request_store_point,
                            review_store_point)
from app.promotions import issue_coupon, list_coupons, submit_report, list_reports, resolve_report
from app.audit import AuditError, query_audit_logs, write_audit_log
from app.db import connect, initialize_schema, run_write
from app.disputes import DisputeError, open_dispute, resolve_dispute, view_dispute
from app.delivery import (DeliveryError, assign_courier, confirm_completion,
                          courier_step, record_arrival, record_contact,
                          report_exception, resolve_exception, respond_courier)
from app.fulfillment import FulfillmentError, finish_preparation, respond_merchant
from app.identity import (IdentityError, _password_hash, disable_account,
                          disable_store, login, register, save_address,
                          session_account, update_phone)
from app.payment import PaymentError, apply_payment_result, start_payment
from app.notifications import list_admin_exceptions, list_notifications
from app.reporting import ReportingError, list_history, query_statistics
from app.support import run_due_jobs

DEFAULT_DB = Path("lab3.sqlite3")


def _log_denied_command(path: Path, account_id: str, command: str,
                        reason: str, order_id: str | None = None) -> None:
    """命令在进入业务函数前被会话检查拒绝时留痕。"""
    def operation(connection):
        existing_order = order_id if order_id and connection.execute(
            "SELECT 1 FROM orders WHERE id = ?", (order_id,)
        ).fetchone() else None
        write_audit_log(connection, account_id, existing_order,
                        command, "failed", reason)

    try:
        run_write(path, operation)
    except (OSError, sqlite3.DatabaseError):
        pass


def seed_demo(path: Path) -> None:
    """仅在空库中写入一组可重复演示的数据。"""
    initialize_schema(path)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT id FROM accounts WHERE id NOT IN (?, ?, ?, ?) LIMIT 1",
                ("customer-demo", "merchant-demo", "admin-demo", "courier-demo"),
            ).fetchone()
            if existing:
                raise IdentityError("已有业务账号，不能再注入演示账号")
            connection.executemany(
                "INSERT OR IGNORE INTO accounts VALUES (?, ?, ?, ?)",
                [("customer-demo", "customer", "active", "13800000001"),
                 ("merchant-demo", "merchant", "active", "13800000002"),
                 ("admin-demo", "admin", "active", "13800000003"),
                 ("courier-demo", "courier", "active", "13800000004")],
            )
            demo_salt = "00112233445566778899aabbccddeeff"
            connection.executemany(
                "INSERT OR IGNORE INTO credentials VALUES (?, ?, ?)",
                [(account_id, demo_salt, _password_hash("demo-pass-123", demo_salt))
                 for account_id in ("customer-demo", "merchant-demo", "admin-demo", "courier-demo")],
            )
            connection.execute(
                "INSERT OR IGNORE INTO addresses (id, customer_id, recipient, phone, address_text, area_code) VALUES (?, ?, ?, ?, ?, ?)",
                ("address-demo", "customer-demo", "小肖", "13800000001", "校园东门一号", "campus"),
            )
            connection.execute("UPDATE addresses SET pickup_point_id = 'pickup-xianlin' WHERE id = 'address-demo'")
            connection.execute("INSERT OR IGNORE INTO map_points VALUES ('store-demo-point', '四季小馆取餐点', 'store', 3, 0, 1)")
            connection.execute(
                """INSERT OR IGNORE INTO stores
                   (id, merchant_id, name, status, delivery_areas, delivery_fee_cents)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("store-demo", "merchant-demo", "四季小馆", "open", '["campus"]', 500),
            )
            connection.execute("UPDATE stores SET store_point_id = 'store-demo-point', location_text = '四季小馆取餐点' WHERE id = 'store-demo'")
            connection.execute(
                "INSERT OR IGNORE INTO menu_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("item-demo", "store-demo", "招牌饭", "主食", 2000, 5, "listed"),
            )
            connection.execute(
                "INSERT OR IGNORE INTO carts VALUES (?, ?, ?)",
                ("cart-demo", "customer-demo", "store-demo"),
            )
            connection.execute(
                "INSERT OR IGNORE INTO cart_items VALUES (?, ?, ?, ?)",
                ("cart-demo", "item-demo", 2, 2000),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="外卖点餐与配送系统")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init-db", help="建立数据库")
    subcommands.add_parser("seed-demo", help="写入演示数据")
    subcommands.add_parser("demo", help="执行结算和建单演示")
    registration = subcommands.add_parser("register", help="注册顾客或提交资格申请")
    registration.add_argument("account_id")
    registration.add_argument("phone")
    registration.add_argument("role", choices=("customer", "merchant", "courier"))
    address = subcommands.add_parser("save-address", help="新增或修改本人收货地址")
    for field in ("customer_id", "recipient", "phone", "address_text", "area_code"):
        address.add_argument(field)
    address.add_argument("--address-id")
    address.add_argument("--pickup-point-id")
    phone = subcommands.add_parser("update-phone", help="修改本人联系电话")
    phone.add_argument("customer_id")
    phone.add_argument("phone")
    sign_in = subcommands.add_parser("login", help="登录并获取会话令牌")
    sign_in.add_argument("account_id")
    quote = subcommands.add_parser("confirm-checkout", help="确认购物车报价")
    quote.add_argument("customer_id")
    quote.add_argument("cart_id")
    quote.add_argument("address_id")
    quote.add_argument("--accept-price-change", action="store_true")
    quote.add_argument("--expected-fingerprint")
    quote.add_argument("--coupon-id")
    order = subcommands.add_parser("create-order", help="使用确认标识创建订单")
    order.add_argument("customer_id")
    order.add_argument("confirmation_id")
    order.add_argument("--note", default="")
    payment = subcommands.add_parser("start-payment", help="为待付款订单发起模拟付款")
    payment.add_argument("customer_id")
    payment.add_argument("order_id")
    payment.add_argument("request_id")
    result = subcommands.add_parser("payment-result", help="注入模拟付款结果")
    result.add_argument("request_id")
    result.add_argument("result", choices=("success", "failure"))
    result.add_argument("--reason", default="模拟付款失败")
    merchant = subcommands.add_parser("merchant-response", help="商家接单或拒单")
    merchant.add_argument("merchant_id")
    merchant.add_argument("order_id")
    merchant.add_argument("decision", choices=("accept", "reject"))
    merchant.add_argument("--reason", default="")
    preparation = subcommands.add_parser("finish-preparation", help="商家确认备餐完成")
    preparation.add_argument("merchant_id")
    preparation.add_argument("order_id")
    refund = subcommands.add_parser("refund-result", help="注入全额模拟退款结果")
    refund.add_argument("order_id")
    refund.add_argument("result", choices=("success", "failure"))
    refund.add_argument("--reason", default="")
    retry_refund = subcommands.add_parser("retry-refund", help="管理员重试失败退款")
    retry_refund.add_argument("admin_id")
    retry_refund.add_argument("order_id")
    failed_refunds = subcommands.add_parser("failed-refunds", help="管理员查询失败退款")
    failed_refunds.add_argument("admin_id")
    search = subcommands.add_parser("search", help="查询店铺和菜品")
    search.add_argument("--keyword", default="")
    search.add_argument("--category", default="")
    search.add_argument("--min-price", type=int, default=0)
    search.add_argument("--max-price", type=int)
    subcommands.add_parser("list-categories", help="查看启用菜品分类")
    category = subcommands.add_parser("manage-category", help="管理员维护菜品分类")
    category.add_argument("admin_id")
    category.add_argument("action", choices=("add", "rename", "disable", "enable"))
    category.add_argument("name")
    category.add_argument("--new-name", default="")
    points = subcommands.add_parser("nearby-points", help="查询虚拟地图取件点")
    points.add_argument("x", type=float)
    points.add_argument("y", type=float)
    points.add_argument("--keyword", default="")
    point = subcommands.add_parser("save-point", help="管理员维护取件点")
    point.add_argument("admin_id")
    point.add_argument("name")
    point.add_argument("x", type=float)
    point.add_argument("y", type=float)
    point.add_argument("--point-id")
    store_point = subcommands.add_parser("request-store-point", help="商家申请取餐点")
    store_point.add_argument("merchant_id")
    store_point.add_argument("store_id")
    store_point.add_argument("name")
    store_point.add_argument("x", type=float)
    store_point.add_argument("y", type=float)
    review_point = subcommands.add_parser("review-store-point", help="管理员审核取餐点")
    review_point.add_argument("admin_id")
    review_point.add_argument("request_id")
    review_point.add_argument("decision", choices=("approve", "reject"))
    coupons = subcommands.add_parser("list-coupons", help="顾客查看全员优惠券")
    coupons.add_argument("customer_id")
    coupon = subcommands.add_parser("issue-coupon", help="管理员发放全员券")
    coupon.add_argument("admin_id")
    coupon.add_argument("title")
    coupon.add_argument("amount_cents", type=int)
    report = subcommands.add_parser("submit-report", help="顾客举报店铺或菜品")
    report.add_argument("customer_id")
    report.add_argument("store_id")
    report.add_argument("description")
    report.add_argument("--item-id")
    reports = subcommands.add_parser("list-reports", help="管理员查看举报")
    reports.add_argument("admin_id")
    resolve = subcommands.add_parser("resolve-report", help="管理员处理举报")
    resolve.add_argument("admin_id")
    resolve.add_argument("report_id")
    resolve.add_argument("decision")
    view = subcommands.add_parser("view-order", help="按角色查看订单")
    view.add_argument("account_id")
    view.add_argument("order_id")
    view.add_argument("--detail", action="store_true")
    assign = subcommands.add_parser("assign-courier", help="管理员人工派单或改派")
    assign.add_argument("admin_id")
    assign.add_argument("order_id")
    assign.add_argument("courier_id")
    assign.add_argument("--handover", action="store_true")
    response = subcommands.add_parser("courier-response", help="配送员响应派单")
    response.add_argument("courier_id")
    response.add_argument("order_id")
    response.add_argument("decision", choices=("accept", "reject"))
    response.add_argument("--reason", default="")
    step = subcommands.add_parser("courier-step", help="配送员确认取餐或送达")
    step.add_argument("courier_id")
    step.add_argument("order_id")
    step.add_argument("step", choices=("pickup", "deliver"))
    cancel = subcommands.add_parser("request-cancel", help="顾客直接取消或申请取消")
    cancel.add_argument("customer_id")
    cancel.add_argument("order_id")
    cancel.add_argument("reason")
    decide = subcommands.add_parser("decide-cancel", help="商家或管理员处理取消申请")
    decide.add_argument("actor_id")
    decide.add_argument("request_id")
    decide.add_argument("decision", choices=("approve", "reject"))
    statistics = subcommands.add_parser("statistics", help="查询完成订单与退款统计")
    statistics.add_argument("account_id")
    statistics.add_argument("date_from")
    statistics.add_argument("date_to")
    history = subcommands.add_parser("history", help="按角色查看订单或配送历史")
    history.add_argument("account_id")
    review = subcommands.add_parser("review-account", help="管理员审核申请")
    review.add_argument("admin_id")
    review.add_argument("request_id")
    review.add_argument("decision", choices=("approve", "reject"))
    disabled = subcommands.add_parser("disable-account", help="管理员停用账号")
    disabled.add_argument("admin_id")
    disabled.add_argument("target_id")
    disabled.add_argument("--cancel-order", action="append", default=[])
    store = subcommands.add_parser("disable-store", help="管理员停用店铺")
    store.add_argument("admin_id")
    store.add_argument("store_id")
    store.add_argument("--cancel-order", action="append", default=[])
    completion = subcommands.add_parser("confirm-completion", help="顾客确认完成")
    completion.add_argument("customer_id")
    completion.add_argument("order_id")
    menu = subcommands.add_parser("add-menu-item", help="本店新增菜品")
    for field in ("merchant_id", "store_id", "name", "category"):
        menu.add_argument(field)
    menu.add_argument("price_cents", type=int)
    menu.add_argument("quantity", type=int)
    new_store = subcommands.add_parser("add-store", help="商家创建店铺")
    new_store.add_argument("merchant_id")
    new_store.add_argument("name")
    new_store.add_argument("delivery_fee_cents", type=int)
    new_store.add_argument("--area", action="append", required=True)
    new_store.add_argument("--description", default="")
    new_store.add_argument("--hours", default="")
    new_cart = subcommands.add_parser("create-cart", help="顾客创建单店购物车")
    new_cart.add_argument("customer_id")
    new_cart.add_argument("store_id")
    edit_store = subcommands.add_parser("update-store", help="修改本店营业状态和配送费")
    edit_store.add_argument("merchant_id")
    edit_store.add_argument("store_id")
    edit_store.add_argument("name")
    edit_store.add_argument("status", choices=("open", "paused"))
    edit_store.add_argument("delivery_fee_cents", type=int)
    edit_store.add_argument("--description")
    edit_store.add_argument("--hours")
    edit_store.add_argument("--area", action="append", default=None)
    edit_menu = subcommands.add_parser("update-menu-item", help="修改价格库存或上下架")
    edit_menu.add_argument("merchant_id")
    edit_menu.add_argument("item_id")
    edit_menu.add_argument("price_cents", type=int)
    edit_menu.add_argument("quantity", type=int)
    edit_menu.add_argument("status", choices=("listed", "unlisted"))
    edit_menu.add_argument("--name")
    edit_menu.add_argument("--category")
    cart = subcommands.add_parser("change-cart", help="修改购物车数量")
    for field in ("customer_id", "cart_id", "item_id"):
        cart.add_argument(field)
    cart.add_argument("quantity", type=int)
    arrival = subcommands.add_parser("record-arrival", help="配送员记录到达")
    arrival.add_argument("courier_id")
    arrival.add_argument("order_id")
    contact = subcommands.add_parser("record-contact", help="记录联系顾客")
    contact.add_argument("courier_id")
    contact.add_argument("order_id")
    contact.add_argument("method")
    contact.add_argument("result", choices=("reached", "unreachable"))
    contact.add_argument("--target", choices=("customer", "merchant", "admin"),
                         default="customer")
    exception = subcommands.add_parser("report-exception", help="报告配送异常")
    exception.add_argument("courier_id")
    exception.add_argument("order_id")
    exception.add_argument("reason")
    resolve = subcommands.add_parser("resolve-exception", help="管理员处置配送异常")
    resolve.add_argument("admin_id")
    resolve.add_argument("exception_id")
    resolve.add_argument("decision", choices=("continue", "handover", "terminate"))
    resolve.add_argument("--courier-id", help="交接后的配送员")
    resolve.add_argument("--handover-confirmed", action="store_true")
    messages = subcommands.add_parser("notifications", help="查看本人通知")
    messages.add_argument("account_id")
    failures = subcommands.add_parser("notification-errors", help="管理员查看通知异常")
    failures.add_argument("admin_id")
    logs = subcommands.add_parser("audit-logs", help="授权查询操作日志")
    logs.add_argument("account_id")
    logs.add_argument("--order-id")
    logs.add_argument("--operator-id")
    logs.add_argument("--date-from")
    logs.add_argument("--date-to")
    dispute = subcommands.add_parser("open-dispute", help="提交订单争议")
    dispute.add_argument("actor_id")
    dispute.add_argument("order_id")
    dispute.add_argument("reason")
    dispute_view = subcommands.add_parser("view-dispute", help="管理员查看争议并留痕")
    dispute_view.add_argument("admin_id")
    dispute_view.add_argument("dispute_id")
    dispute_resolve = subcommands.add_parser("resolve-dispute", help="管理员处理争议")
    dispute_resolve.add_argument("admin_id")
    dispute_resolve.add_argument("dispute_id")
    dispute_resolve.add_argument("decision")
    dispute_resolve.add_argument("--refund", action="store_true",
                                 help="为已完成或已取消的已付款订单发起全额退款")
    jobs = subcommands.add_parser("run-jobs", help="处理已到期的付款、履约与退款任务")
    jobs.add_argument("--watch", action="store_true", help="持续处理到期任务")
    jobs.add_argument("--interval", type=float, default=30,
                      help="持续模式下扫描间隔，单位秒，默认 30")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        public = {"init-db", "seed-demo", "register", "login", "search", "run-jobs",
                  "list-categories", "nearby-points"}
        if args.command not in public:
            token = os.environ.get("LAB3_SESSION", "")
            signed_in = session_account(args.db, token)
            actor = getattr(args, "customer_id", None) or getattr(args, "merchant_id", None)
            actor = actor or getattr(args, "courier_id", None) or getattr(args, "actor_id", None)
            actor = actor or getattr(args, "account_id", None)
            if args.command == "demo":
                actor = "customer-demo"
            if args.command == "assign-courier":
                actor = args.admin_id
            if args.command in ("review-account", "disable-account", "disable-store",
                                "retry-refund", "failed-refunds",
                                "resolve-exception", "notification-errors",
                                "view-dispute", "resolve-dispute", "manage-category",
                                "save-point", "review-store-point", "issue-coupon",
                                "list-reports", "resolve-report"):
                actor = args.admin_id
            if actor and actor != signed_in:
                _log_denied_command(args.db, signed_in, args.command,
                                    "会话账号与操作者不一致",
                                    getattr(args, "order_id", None))
                raise IdentityError("会话账号与操作者不一致")
            if args.command in ("payment-result", "refund-result"):
                with connect(args.db) as connection:
                    role = connection.execute(
                        "SELECT role FROM accounts WHERE id = ?", (signed_in,),
                    ).fetchone()
                    if not role or role["role"] != "admin":
                        _log_denied_command(args.db, signed_in, args.command,
                                            "只有管理员可注入模拟结果",
                                            getattr(args, "order_id", None))
                        raise IdentityError("只有管理员可注入模拟结果")
        if args.command == "init-db":
            initialize_schema(args.db)
            print(f"数据库已初始化：{args.db}")
        elif args.command == "seed-demo":
            seed_demo(args.db)
            print("演示数据已准备")
        elif args.command == "register":
            print(register(args.db, args.account_id, getpass("密码："), args.phone, args.role))
        elif args.command == "login":
            print(login(args.db, args.account_id, getpass("密码：")))
        elif args.command == "save-address":
            print(save_address(args.db, args.customer_id, args.recipient,
                               args.phone, args.address_text, args.area_code,
                               args.address_id, args.pickup_point_id))
        elif args.command == "update-phone":
            update_phone(args.db, args.customer_id, args.phone)
            print("联系电话已更新")
        elif args.command in ("confirm-checkout", "demo"):
            customer = "customer-demo" if args.command == "demo" else args.customer_id
            cart = "cart-demo" if args.command == "demo" else args.cart_id
            address = "address-demo" if args.command == "demo" else args.address_id
            result = confirm_checkout(
                args.db, customer, cart, address,
                False if args.command == "demo" else args.accept_price_change,
                None if args.command == "demo" else args.expected_fingerprint,
                None if args.command == "demo" else args.coupon_id,
            )
            print(json.dumps(asdict(result), ensure_ascii=False))
            if args.command == "demo" and result.confirmation_id:
                order = create_order(args.db, customer, result.confirmation_id)
                print(json.dumps(asdict(order), ensure_ascii=False))
        elif args.command == "create-order":
            result = create_order(args.db, args.customer_id, args.confirmation_id, args.note)
            print(json.dumps(asdict(result), ensure_ascii=False))
        elif args.command == "start-payment":
            result = start_payment(args.db, args.customer_id, args.order_id, args.request_id)
            print(json.dumps(asdict(result), ensure_ascii=False))
        elif args.command == "payment-result":
            result = apply_payment_result(
                args.db, args.request_id, args.result == "success", args.reason
            )
            print(json.dumps(asdict(result), ensure_ascii=False))
        elif args.command == "run-jobs":
            if args.watch:
                if not 0 < args.interval <= 60:
                    raise CancellationError("扫描间隔须在 0 到 60 秒之间")
                try:
                    while True:
                        run_due_jobs(args.db)
                        time.sleep(args.interval)
                except KeyboardInterrupt:
                    print("定时任务处理已停止")
            else:
                print(f"已处理到期工作：{run_due_jobs(args.db)}")
        elif args.command == "merchant-response":
            result = respond_merchant(
                args.db, args.merchant_id, args.order_id,
                args.decision == "accept", args.reason,
            )
            print(json.dumps(asdict(result), ensure_ascii=False))
        elif args.command == "finish-preparation":
            result = finish_preparation(args.db, args.merchant_id, args.order_id)
            print(json.dumps(asdict(result), ensure_ascii=False))
        elif args.command == "refund-result":
            result = apply_refund_result(
                args.db, args.order_id, args.result == "success", args.reason,
            )
            print(json.dumps(asdict(result), ensure_ascii=False))
        elif args.command == "retry-refund":
            print(retry_failed_refund(args.db, args.admin_id, args.order_id))
        elif args.command == "failed-refunds":
            print(json.dumps(list_failed_refunds(args.db, args.admin_id), ensure_ascii=False))
        elif args.command == "search":
            print(json.dumps(search_catalog(args.db, args.keyword, args.category,
                                            args.min_price, args.max_price), ensure_ascii=False))
        elif args.command == "list-categories":
            print(json.dumps(list_categories(args.db), ensure_ascii=False))
        elif args.command == "manage-category":
            manage_category(args.db, args.admin_id, args.action, args.name, args.new_name)
            print("分类已更新")
        elif args.command == "nearby-points":
            print(json.dumps(nearby_points(args.db, args.x, args.y, keyword=args.keyword), ensure_ascii=False))
        elif args.command == "save-point":
            print(save_point(args.db, args.admin_id, args.name, args.x, args.y, args.point_id))
        elif args.command == "request-store-point":
            print(request_store_point(args.db, args.merchant_id, args.store_id,
                                      args.name, args.x, args.y))
        elif args.command == "review-store-point":
            review_store_point(args.db, args.admin_id, args.request_id, args.decision == "approve")
            print("申请已处理")
        elif args.command == "list-coupons":
            print(json.dumps(list_coupons(args.db, args.customer_id), ensure_ascii=False))
        elif args.command == "issue-coupon":
            print(issue_coupon(args.db, args.admin_id, args.title, args.amount_cents))
        elif args.command == "submit-report":
            print(submit_report(args.db, args.customer_id, args.store_id,
                                args.item_id, args.description))
        elif args.command == "list-reports":
            print(json.dumps(list_reports(args.db, args.admin_id), ensure_ascii=False))
        elif args.command == "resolve-report":
            resolve_report(args.db, args.admin_id, args.report_id, args.decision)
            print("举报已处理")
        elif args.command == "view-order":
            print(json.dumps(view_order(args.db, args.account_id, args.order_id, args.detail), ensure_ascii=False))
        elif args.command == "assign-courier":
            print(assign_courier(args.db, args.admin_id, args.order_id,
                                 args.courier_id, args.handover))
        elif args.command == "courier-response":
            print(respond_courier(args.db, args.courier_id, args.order_id,
                                  args.decision == "accept", args.reason))
        elif args.command == "courier-step":
            print(courier_step(args.db, args.courier_id, args.order_id, args.step))
        elif args.command == "request-cancel":
            print(request_cancellation(args.db, args.customer_id, args.order_id, args.reason))
        elif args.command == "decide-cancel":
            print(decide_cancellation(args.db, args.actor_id, args.request_id,
                                      args.decision == "approve"))
        elif args.command == "statistics":
            print(json.dumps(query_statistics(args.db, args.account_id,
                                               args.date_from, args.date_to), ensure_ascii=False))
        elif args.command == "history":
            print(json.dumps(list_history(args.db, args.account_id), ensure_ascii=False))
        elif args.command == "review-account":
            print(review_account(args.db, args.admin_id, args.request_id,
                                 args.decision == "approve"))
        elif args.command == "disable-account":
            disable_account(args.db, args.admin_id, args.target_id, tuple(args.cancel_order))
            print("账号已停用")
        elif args.command == "disable-store":
            disable_store(args.db, args.admin_id, args.store_id, tuple(args.cancel_order))
            print("店铺已停用")
        elif args.command == "confirm-completion":
            print(confirm_completion(args.db, args.customer_id, args.order_id))
        elif args.command == "add-menu-item":
            print(add_menu_item(args.db, args.merchant_id, args.store_id, args.name,
                                args.category, args.price_cents, args.quantity))
        elif args.command == "add-store":
            print(add_store(args.db, args.merchant_id, args.name, tuple(args.area),
                            args.delivery_fee_cents, args.description, args.hours))
        elif args.command == "create-cart":
            print(create_cart(args.db, args.customer_id, args.store_id))
        elif args.command == "update-menu-item":
            update_menu_item(args.db, args.merchant_id, args.item_id,
                             args.price_cents, args.quantity, args.status,
                             args.name, args.category)
            print("菜品已更新")
        elif args.command == "update-store":
            update_store(args.db, args.merchant_id, args.store_id,
                         args.name, args.status, args.delivery_fee_cents,
                         args.description, args.hours, args.area)
            print("店铺已更新")
        elif args.command == "change-cart":
            change_cart_quantity(args.db, args.customer_id, args.cart_id,
                                 args.item_id, args.quantity)
            print("购物车已更新")
        elif args.command == "record-arrival":
            record_arrival(args.db, args.courier_id, args.order_id)
            print("已记录到达")
        elif args.command == "record-contact":
            print(record_contact(args.db, args.courier_id, args.order_id,
                                 args.method, args.result == "reached",
                                 target=args.target))
        elif args.command == "report-exception":
            print(report_exception(args.db, args.courier_id, args.order_id, args.reason))
        elif args.command == "resolve-exception":
            if args.decision == "handover" and not args.courier_id:
                raise DeliveryError("交接改派必须指定新配送员")
            if args.decision != "handover" and args.courier_id:
                raise DeliveryError("仅交接改派可指定新配送员")
            print(resolve_exception(args.db, args.admin_id, args.exception_id,
                                    args.decision == "terminate",
                                    new_courier_id=(args.courier_id
                                                    if args.decision == "handover" else None),
                                    handover_confirmed=args.handover_confirmed))
        elif args.command == "notifications":
            print(json.dumps(list_notifications(args.db, args.account_id), ensure_ascii=False))
        elif args.command == "notification-errors":
            print(json.dumps(list_admin_exceptions(args.db, args.admin_id), ensure_ascii=False))
        elif args.command == "audit-logs":
            print(json.dumps(query_audit_logs(args.db, args.account_id,
                                              args.order_id, args.operator_id,
                                              args.date_from, args.date_to), ensure_ascii=False))
        elif args.command == "open-dispute":
            print(open_dispute(args.db, args.actor_id, args.order_id, args.reason))
        elif args.command == "view-dispute":
            print(json.dumps(view_dispute(args.db, args.admin_id, args.dispute_id), ensure_ascii=False))
        elif args.command == "resolve-dispute":
            resolve_dispute(args.db, args.admin_id, args.dispute_id,
                            args.decision, args.refund)
            print("争议已处理")
    except (CheckoutError, PaymentError, FulfillmentError,
            DeliveryError, CancellationError, AccessError, ReportingError,
            IdentityError, CatalogError, AuditError, DisputeError) as error:
        print(f"操作失败：{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
