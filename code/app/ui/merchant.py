"""商家页面：店铺菜品、订单履约、取消申请与本店统计。"""

import re

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QDoubleSpinBox, QHeaderView, QInputDialog, QMessageBox, QTimeEdit

from app.areas import list_areas
from app.access import view_order
from app.after_sales import decide_cancellation
from app.catalog import add_menu_item, add_store, update_menu_item, update_store
from app.categories import list_categories
from app.map_points import request_store_point
from app.fulfillment import finish_preparation, respond_merchant
from app.disputes import open_dispute
from app.reporting import query_statistics
from app.ui.customer import STATUS, money
from app.ui.timefmt import local_time, local_today
from app.ui.widgets import (
    Page, amount, button, card, empty, form_field, label, line, metric_card, row,
    select, spin, table,
)


def build_merchant_page(window, key: str, context=None) -> Page:
    pages = {
        "merchant_home": home_page, "merchant_orders": orders_page,
        "store": store_page, "statistics": statistics_page,
        "notifications": notifications_page,
    }
    return pages.get(key, home_page)(window, context)


def home_page(window, context=None) -> Page:
    orders = window.data.orders()
    pending = sum(order["status"] == "awaiting_merchant" for order in orders)
    preparing = sum(order["status"] == "preparing" for order in orders)
    page = Page("商家业务首页", "处理订单并维护本店菜品",
                button("刷新订单", window.refresh, True))
    page.add_row(metric_card("待接单", str(pending)),
                 metric_card("制作中", str(preparing)),
                 metric_card("本店订单", str(len(orders))))
    page.add(button("进入订单处理", lambda: window.show_page("merchant_orders"), True))
    return page.finish()


def orders_page(window, context=None) -> Page:
    page = Page("待处理订单", "先处理即将到期的待接单订单",
                button("刷新订单", window.refresh, True))
    orders = window.data.orders()
    active = [order for order in orders if order["status"] in (
        "awaiting_merchant", "preparing", "awaiting_assignment",
        "awaiting_courier", "awaiting_pickup", "delivering")]
    if not active:
        page.add(empty("当前没有待处理订单。"))
    for order in active:
        frame, layout = card(f"订单 {order['id'][:12]} · {money(order['total_amount_cents'])}")
        layout.addWidget(label(f"{STATUS.get(order['status'], order['status'])} · "
                               f"下单 {local_time(order['placed_at'])}", "muted"))
        items = window.data.order_items(order["id"])
        layout.addWidget(label("、".join(
            f"{item['item_name_snapshot']} × {item['quantity']}" for item in items)))
        detail = view_order(window.path, window.account_id, order["id"], True)
        layout.addWidget(label(f"收货电话：{detail['phone']}", "muted"))
        if order["status"] == "awaiting_merchant":
            layout.addLayout(row(
                button("接单", lambda checked=False, order_id=order["id"]:
                       window.perform(lambda: respond_merchant(
                           window.path, window.account_id, order_id, True), "已接单"), True),
                button("拒单", lambda checked=False, order_id=order["id"]:
                       _reject_order(window, order_id)),
            ))
        elif order["status"] == "preparing":
            layout.addWidget(button("备餐完成", lambda checked=False, order_id=order["id"]:
                                    window.perform(lambda: finish_preparation(
                                        window.path, window.account_id, order_id),
                                        "备餐已完成"), True))
        layout.addWidget(button("联系管理员", lambda checked=False, oid=order["id"]:
                                _contact_admin(window, oid)))
        page.add(frame)
    requests = window.data.pending_cancellations()
    if requests:
        frame, layout = card("待处理取消申请")
        for request in requests:
            layout.addWidget(label(f"订单 {request['order_id'][:12]} · {request['reason']}"))
            layout.addLayout(row(
                button("批准", lambda checked=False, request_id=request["id"]:
                       window.perform(lambda: decide_cancellation(
                           window.path, window.account_id, request_id, True),
                           "已批准取消"), True),
                button("拒绝", lambda checked=False, request_id=request["id"]:
                       window.perform(lambda: decide_cancellation(
                           window.path, window.account_id, request_id, False),
                           "已拒绝取消")),
            ))
        page.add(frame)
    return page.finish()


def _reject_order(window, order_id: str) -> None:
    reason, accepted = QInputDialog.getText(window, "拒绝接单", "拒单原因")
    if accepted:
        window.perform(lambda: respond_merchant(window.path, window.account_id,
                                                order_id, False, reason.strip()), "已拒单")


def _contact_admin(window, order_id: str) -> None:
    reason, accepted = QInputDialog.getText(window, "联系管理员", "请说明订单问题")
    if accepted and reason.strip():
        window.perform(lambda: open_dispute(window.path, window.account_id,
                                             order_id, reason.strip()), "已提交管理员处理")


def store_page(window, context=None) -> Page:
    stores = window.data.stores()
    page = Page("店铺与菜品", "维护营业信息和可售菜品",
                button("新增店铺", lambda: window.show_page("store", ("new_store", None)), True))
    if not stores:
        page.add(empty("尚无店铺。审核通过后，可点击右上角新增店铺。"))
    for store in stores:
        status_frame, status_layout = card(f"{store['name']} · 店铺营业状态")
        status_layout.addWidget(label(
            f"{store['status']} · 营业时间 {store['business_hours'] or '未设置'} · "
            "按虚拟直线距离计配送费", "muted"))
        status_layout.addWidget(button("编辑店铺", lambda checked=False, entry=store:
                                       window.show_page("store", ("edit_store", entry))))
        page.add(status_frame)
        point_frame, point_layout = card(f"{store['name']} · 取餐点申请")
        point_layout.addWidget(label("当前取餐点：" + (store["location_text"] if store["store_point_id"] else "未审核设置")))
        point_name = form_field(point_layout, "取餐点名称", line(value=store["location_text"]))
        point_x = QDoubleSpinBox()
        point_y = QDoubleSpinBox()
        for coordinate in (point_x, point_y):
            coordinate.setRange(-1000, 1000)
            coordinate.setDecimals(2)
        point_layout.addLayout(row(label("虚拟横坐标"), point_x, label("虚拟纵坐标"), point_y))
        point_layout.addWidget(button("提交取餐点申请", lambda checked=False, sid=store["id"],
                                      name=point_name, x=point_x, y=point_y:
                                      window.perform(lambda: request_store_point(window.path,
                                          window.account_id, sid, name.text().strip(),
                                          x.value(), y.value()), "取餐点申请已提交")))
        page.add(point_frame)
        if isinstance(context, tuple) and context[0] == "edit_store" and context[1]["id"] == store["id"]:
            page.add(_store_editor(window, store))
        menu_frame, menu_layout = card(f"{store['name']} · 菜品管理")
        items = window.data.store_items(store["id"])
        menu_table = table(["菜品", "分类", "现价", "可售数量", "状态", "操作"], [
            [item["name"], item["category"], money(item["price_cents"]),
             item["available_quantity"],
             {"listed": "上架", "unlisted": "下架"}.get(item["status"], item["status"]),
             ""] for item in items
        ])
        menu_table.horizontalHeader().setStretchLastSection(False)
        menu_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Fixed)
        menu_table.setColumnWidth(5, 100)
        for index, item in enumerate(items):
            edit_button = button(
                "编辑", lambda entry=item:
                window.show_page("store", ("edit_item", entry)))
            edit_button.setObjectName("tableAction")
            menu_table.setCellWidget(index, 5, edit_button)
        menu_layout.addWidget(menu_table)
        menu_layout.addWidget(button("新增菜品", lambda checked=False, entry=store:
                                     window.show_page("store", ("new_item", entry)), True))
        page.add(menu_frame)
    if isinstance(context, tuple):
        kind, entry = context
        if kind == "new_store":
            page.add(_store_editor(window, entry))
        elif kind == "new_item" or kind == "edit_item":
            page.add(_item_editor(window, kind, entry, stores))
    return page.finish()


def _store_editor(window, entry: dict | None):
    current = entry or {}
    frame, layout = card("编辑店铺" if entry else "新增店铺")
    name = form_field(layout, "店铺名称", line(value=current.get("name", "")))
    values = re.findall(r"\d{1,2}:\d{2}", current.get("business_hours", ""))
    opening = QTimeEdit(QTime.fromString(values[0], "H:mm") if values else QTime(9, 0))
    closing = QTimeEdit(QTime.fromString(values[1], "H:mm") if len(values) > 1 else QTime(21, 0))
    for control in (opening, closing):
        control.setDisplayFormat("HH:mm")
    layout.addWidget(label("营业时间", "muted"))
    layout.addLayout(row(opening, label("至"), closing))
    description = form_field(layout, "店铺简介", line(value=current.get("description", "")))
    layout.addWidget(label("取餐点由上方申请表提交，管理员审核后生效。", "muted"))
    layout.addWidget(label("配送费由审核通过的取餐点与顾客取件点的虚拟距离统一计算。", "muted"))
    status = form_field(layout, "营业状态", select([("营业中", "open"), ("暂停", "paused")]))
    if current.get("status") == "paused":
        status.setCurrentIndex(1)

    def save():
        area_codes = tuple(area["code"] for area in list_areas(window.path)[:1])
        if opening.time() >= closing.time():
            QMessageBox.warning(window, "营业时间无效", "结束时间须晚于开始时间")
            return
        hours = f"{opening.time().toString('HH:mm')}-{closing.time().toString('HH:mm')}"
        if entry:
            action = lambda: update_store(
                window.path, window.account_id, entry["id"], name.text().strip(),
                status.currentData(), entry["delivery_fee_cents"], description.text().strip(),
                hours, None, None)
        else:
            action = lambda: add_store(
                window.path, window.account_id, name.text().strip(), area_codes,
                0, description.text().strip(),
                hours, "")
        window.perform(action, "店铺已保存", "store")

    layout.addWidget(button("保存店铺", save, True))
    return frame


def _item_editor(window, kind: str, entry: dict, stores: list[dict]):
    editing = kind == "edit_item"
    frame, layout = card("编辑菜品" if editing else "新增菜品")
    name = form_field(layout, "菜品名称", line(value=entry.get("name", "") if editing else ""))
    categories = [item["name"] for item in list_categories(window.path)]
    if editing and entry["category"] not in categories:
        categories.insert(0, entry["category"])
    category = form_field(layout, "分类", select(categories))
    if editing:
        category.setCurrentIndex(categories.index(entry["category"]))
    price = form_field(layout, "价格（元）", amount(
        entry.get("price_cents", 0) if editing else 0))
    quantity = form_field(layout, "可售数量", spin(
        entry.get("available_quantity", 0) if editing else 0, 0, 100000))
    status = form_field(layout, "状态", select([("上架", "listed"), ("下架", "unlisted")]))
    if entry.get("status") == "unlisted":
        status.setCurrentIndex(1)

    def save():
        if editing:
            action = lambda: update_menu_item(
                window.path, window.account_id, entry["id"], round(price.value() * 100),
                quantity.value(), status.currentData(), name.text().strip(),
                category.currentData() if category.currentData() != entry["category"] else None)
        else:
            action = lambda: add_menu_item(
                window.path, window.account_id, entry["id"], name.text().strip(),
                category.currentData() or "", round(price.value() * 100), quantity.value())
        window.perform(action, "菜品已保存", "store")

    layout.addWidget(button("保存菜品", save, True))
    return frame


def statistics_page(window, context=None) -> Page:
    today = local_today()
    page = Page("本店统计", "仅查看本人店铺数据",
                button("刷新", window.refresh, True))
    frame, layout = card("选择完成日期与退款成功日期")
    start = line(value=today[:8] + "01")
    end = line(value=today)
    layout.addLayout(row(label("起始日期"), start, label("结束日期"), end))
    page.add(frame)

    def load():
        try:
            result = query_statistics(window.path, window.account_id,
                                      start.text().strip(), end.text().strip())
        except ValueError as error:
            QMessageBox.warning(window, "统计失败", str(error))
            return
        results, results_layout = card("数据概览")
        results_layout.addWidget(label(f"已完成订单：{result['completed_orders']}"))
        results_layout.addWidget(label(f"营业额：{money(result['revenue_cents'])}"))
        results_layout.addWidget(label(f"退款：{money(result['refund_cents'])}"))
        results_layout.addWidget(label(f"差额：{money(result['difference_cents'])}"))
        results_layout.addWidget(table(["热销菜品", "销量"], result["popular_items"]))
        page.add(results)

    layout.addWidget(button("查询统计", load, True))
    return page.finish()


def notifications_page(window, context=None) -> Page:
    from app.ui.customer import notifications_page as common_notifications
    return common_notifications(window, context)
