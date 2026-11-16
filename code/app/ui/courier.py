"""配送员页面：本人任务、接单、取餐、送达和联系异常。"""

from PySide6.QtWidgets import QInputDialog

from app.access import view_order
from app.delivery import (
    courier_step, record_arrival, record_contact, report_exception,
    respond_courier,
)
from app.reporting import list_history
from app.disputes import open_dispute
from app.ui.customer import STATUS, notifications_page as common_notifications
from app.ui.timefmt import local_time
from app.ui.widgets import Page, button, card, empty, label, row, table


def build_courier_page(window, key: str, context=None) -> Page:
    if key == "history":
        return history_page(window)
    if key == "notifications":
        return common_notifications(window)
    if key == "profile":
        return profile_page(window)
    return tasks_page(window)


def tasks_page(window) -> Page:
    page = Page("我的配送任务", "仅展示当前分配给我的任务",
                button("刷新任务", window.refresh, True))
    tasks = [task for task in window.data.tasks() if task["order_status"] not in
             ("completed", "cancelled")]
    if not tasks:
        page.add(empty("当前没有配送任务。"))
        return page.finish()
    for task in tasks:
        order_id = task["order_id"]
        detail = view_order(window.path, window.account_id, order_id, True)
        frame, layout = card(f"当前任务 {order_id[:12]}")
        layout.addWidget(label(STATUS.get(task["order_status"], task["order_status"]),
                               "cardtitle"))
        layout.addWidget(label(f"取餐：{detail['store_point'] or task['store_name_snapshot']} "
                               f"{detail['store_coordinates'] or ''}", "muted"))
        layout.addWidget(label(f"交付：{detail['pickup_point'] or detail['address']} "
                               f"{detail['pickup_coordinates'] or ''} · {detail['phone']}", "muted"))
        status = task["order_status"]
        if status == "awaiting_courier":
            layout.addLayout(row(
                button("接单", lambda checked=False, oid=order_id:
                       window.perform(lambda: respond_courier(
                           window.path, window.account_id, oid, True), "已接单"), True),
                button("拒接", lambda checked=False, oid=order_id:
                       _reject(window, oid)),
            ))
        elif status == "awaiting_pickup":
            layout.addWidget(button("确认取餐", lambda checked=False, oid=order_id:
                                    window.perform(lambda: courier_step(
                                        window.path, window.account_id, oid, "pickup"),
                                        "已确认取餐"), True))
        elif status == "delivering":
            if not task["arrived_at"]:
                layout.addWidget(button("记录到达", lambda checked=False, oid=order_id:
                                        window.perform(lambda: record_arrival(
                                            window.path, window.account_id, oid),
                                            "已记录到达")))
            layout.addLayout(row(
                button("联系顾客", lambda checked=False, oid=order_id:
                       _contact(window, oid, "customer")),
                button("联系商家", lambda checked=False, oid=order_id:
                       _contact(window, oid, "merchant")),
                button("联系管理员", lambda checked=False, oid=order_id:
                       _contact_admin(window, oid)),
            ))
            layout.addLayout(row(
                button("确认送达", lambda checked=False, oid=order_id:
                       window.perform(lambda: courier_step(
                           window.path, window.account_id, oid, "deliver"),
                           "已确认送达"), True),
                button("报告异常", lambda checked=False, oid=order_id:
                       _report(window, oid)),
            ))
        elif status == "delivery_exception":
            layout.addWidget(label("等待管理员处理配送异常。", "muted"))
        if status not in ("delivering", "delivery_exception"):
            layout.addWidget(button("联系管理员", lambda checked=False, oid=order_id:
                                    _contact_admin(window, oid)))
        page.add(frame)
    rules, rules_layout = card("任务规则")
    rules_layout.addWidget(label("接单后确认取餐，取餐后确认送达。"))
    rules_layout.addWidget(label("无法联系顾客时，先记录到达，再于 10 分钟内记录两次联系失败，"
                                 "并联系商家或管理员，然后报告异常。", "muted"))
    page.add(rules)
    return page.finish()


def _reject(window, order_id: str) -> None:
    reason, accepted = QInputDialog.getText(window, "拒绝派单", "拒接原因")
    if accepted:
        window.perform(lambda: respond_courier(window.path, window.account_id,
                                               order_id, False, reason.strip()), "已拒接")


def _contact(window, order_id: str, target: str) -> None:
    method, accepted = QInputDialog.getItem(
        window, "联系记录", "联系方式", ["电话", "短信", "其他"], 0, False)
    if not accepted:
        return
    result, accepted = QInputDialog.getItem(
        window, "联系记录", "联系结果", ["未联系上", "已联系上"], 0, False)
    if not accepted:
        return
    window.perform(lambda: record_contact(window.path, window.account_id,
                                          order_id, method, result == "已联系上",
                                          target=target), "联系记录已保存")


def _contact_admin(window, order_id: str) -> None:
    reason, accepted = QInputDialog.getText(window, "联系管理员", "请说明订单问题")
    if accepted and reason.strip():
        window.perform(lambda: open_dispute(window.path, window.account_id,
                                             order_id, reason.strip()), "已提交管理员处理")


def _report(window, order_id: str) -> None:
    reason, accepted = QInputDialog.getItem(
        window, "报告配送异常", "异常原因",
        ["无法联系顾客", "餐品损坏", "交通异常", "其他"], 0, True)
    if accepted:
        window.perform(lambda: report_exception(window.path, window.account_id,
                                                order_id, reason.strip()),
                       "异常已提交")


def history_page(window) -> Page:
    page = Page("历史任务", "查看本人已完成或已取消任务")
    orders = list_history(window.path, window.account_id)
    page.add(table(["订单", "状态", "下单时间"], [
        [item["id"][:12], STATUS.get(item["status"], item["status"]),
         local_time(item["placed_at"])] for item in orders
    ]))
    return page.finish()


def profile_page(window) -> Page:
    account = window.data.account()
    page = Page("个人资料", "配送员账号资料")
    frame, layout = card("账号信息")
    layout.addWidget(label(f"账号：{account['id']}"))
    layout.addWidget(label(f"电话：{account['phone']}"))
    layout.addWidget(label(f"状态：{account['status']}"))
    page.add(frame)
    return page.finish()
