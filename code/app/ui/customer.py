"""顾客页面：搜索、购物车、下单、付款、订单和地址。"""

from uuid import uuid4
from math import ceil, floor

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDoubleSpinBox, QGridLayout, QMessageBox, QWidget

from app.access import view_order
from app.after_sales import request_cancellation
from app.catalog import change_cart_quantity, create_cart, search_stores
from app.checkout import confirm_checkout, create_order
from app.delivery import confirm_completion
from app.categories import list_categories
from app.map_points import delivery_fee, list_points, nearby_points
from app.promotions import list_coupons, submit_report
from app.disputes import open_dispute
from app.identity import save_address, update_phone
from app.notifications import list_notifications
from app.payment import apply_payment_result, start_payment
from app.ui.timefmt import local_time
from app.ui.widgets import (
    Page, button, card, empty, form_field, label, line, row, select, spin, table,
)


def money(cents: int) -> str:
    return f"¥{cents / 100:.2f}"


class VirtualMap(QWidget):
    """固定测试点位的虚拟坐标示意图，不表示真实道路。"""

    def __init__(self, points: list[dict], x: float, y: float):
        super().__init__()
        self.points = points
        self.search_x = x
        self.search_y = y
        self.setMinimumHeight(320)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#fffdf9"))
        values_x = [point["x"] for point in self.points] + [self.search_x]
        values_y = [point["y"] for point in self.points] + [self.search_y]
        left, right = floor(min(values_x)) - 1, ceil(max(values_x)) + 1
        bottom, top = floor(min(values_y)) - 1, ceil(max(values_y)) + 1
        width, height = max(1, self.width() - 110), max(1, self.height() - 65)
        def position(x, y):
            return 55 + (x - left) * width / (right - left), 20 + (top - y) * height / (top - bottom)
        step = max(1, ceil(max(right - left, top - bottom) / 12))
        painter.setPen(QPen(QColor("#e6e0d7"), 1))
        for x in range(left, right + 1, step):
            px, _ = position(x, bottom)
            painter.drawLine(int(px), 20, int(px), 20 + height)
            painter.drawText(int(px) - 14, 40 + height, str(x))
        for y in range(bottom, top + 1, step):
            _, py = position(left, y)
            painter.drawLine(55, int(py), 55 + width, int(py))
            painter.drawText(15, int(py) + 5, str(y))
        for point in self.points:
            px, py = position(point["x"], point["y"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ff642f"))
            painter.drawEllipse(int(px) - 5, int(py) - 5, 10, 10)
            painter.setPen(QColor("#302923"))
            painter.drawText(int(px) + 8, int(py) - 8, point["name"])
        px, py = position(self.search_x, self.search_y)
        painter.setPen(QPen(QColor("#274f8a"), 2))
        painter.drawLine(int(px) - 7, int(py), int(px) + 7, int(py))
        painter.drawLine(int(px), int(py) - 7, int(px), int(py) + 7)
        painter.drawText(int(px) + 8, int(py) + 18, "输入坐标")
        painter.end()


STATUS = {
    "pending_payment": "待付款", "awaiting_merchant": "待商家接单",
    "preparing": "制作中", "awaiting_assignment": "待派单",
    "awaiting_courier": "待配送员接单", "awaiting_pickup": "待取餐",
    "delivering": "配送中", "delivery_exception": "配送异常",
    "delivered": "已送达", "completed": "已完成", "cancelled": "已取消",
}


def area_label(code: str) -> str:
    return {"campus": "校园区域", "area": "测试区域"}.get(code, code)


def build_customer_page(window, key: str, context=None) -> Page:
    pages = {
        "home": home_page, "search": search_page, "store_detail": store_page,
        "cart": cart_page, "orders": orders_page, "order_detail": order_detail_page,
        "cancel": cancellation_page, "profile": profile_page,
        "pickup_points": pickup_points_page, "report": report_page,
        "notifications": notifications_page,
    }
    return pages.get(key, home_page)(window, context)


def home_page(window, context=None) -> Page:
    page = Page("美食广场", "查看营业状态，按条件查找菜品",
                button("查看购物车", lambda: window.show_page("cart"), True))
    frame, layout = card("开始点餐")
    layout.addWidget(label("搜索菜品、店铺名称，或浏览所有营业中的菜品。", "muted"))
    layout.addWidget(button("搜索餐品", lambda: window.show_page("search"), True))
    layout.addWidget(button("举报店铺或菜品", lambda: window.show_page("report")))
    page.add(frame)
    matches = search_stores(window.path)
    if matches:
        grid = QGridLayout()
        grid.setSpacing(16)
        for index, item in enumerate(matches[:6]):
            grid.addWidget(_store_card(window, item), index // 3, index % 3)
        page.content.addLayout(grid)
    else:
        page.add(empty("目前还没有店铺。管理员审核商家后，商家可新增店铺。"))
    return page.finish()


def search_page(window, context=None) -> Page:
    page = Page("搜索结果" if context else "美食广场", "查看营业状态，按条件查找菜品",
                button("查看购物车", lambda: window.show_page("cart"), True))
    frame, layout = card("查找店铺与菜品")
    filters = context or {}
    keyword = line("关键词或店铺名", filters.get("keyword", ""))
    category = select([("全部分类", "")] + [(item["name"], item["name"])
                                         for item in list_categories(window.path)])
    selected_category = filters.get("category", "")
    category.setCurrentIndex(max(0, category.findData(selected_category)))
    low = spin(filters.get("low", 0), 0, 100000)
    high = spin(filters.get("high", 1000), 0, 100000)
    fields = row(keyword, category, low, high)
    layout.addLayout(fields)
    layout.addWidget(button("搜索", lambda: window.show_page("search", {
        "keyword": keyword.text().strip(), "category": category.currentData(),
        "low": low.value(), "high": high.value(),
    }), True))
    page.add(frame)
    try:
        matches = search_stores(window.path, keyword.text().strip(),
                                 category.currentData(), low.value() * 100,
                                 None if high.value() == 1000 else high.value() * 100)
    except ValueError as error:
        page.add(empty(str(error)))
        return page.finish()
    if not matches:
        page.add(empty("没有符合条件的店铺。"))
        return page.finish()
    grid = QGridLayout()
    grid.setSpacing(16)
    for index, item in enumerate(matches):
        grid.addWidget(_store_card(window, item), index // 3, index % 3)
    page.content.addLayout(grid)
    return page.finish()


def _store_card(window, item: dict):
    frame, layout = card(item["store_name"])
    sample = item["sample_item"]
    if sample:
        layout.addWidget(label(f"推荐菜品：{sample['name']}  {money(sample['price_cents'])}",
                               "cardtitle"))
        layout.addWidget(label(sample["category"], "muted"))
    else:
        layout.addWidget(label("暂无在售菜品", "muted"))
    state = "营业中" if item["store_status"] == "open" else "暂停营业"
    if sample and sample["sold_out"]:
        state += " · 售罄"
    layout.addWidget(label(state, "muted"))
    layout.addWidget(button("查看店铺", lambda: window.show_page(
        "store_detail", item["store_id"])))
    return frame


def store_page(window, store_id: str) -> Page:
    items = window.data.public_items(store_id)
    store = window.data.store_info(store_id)
    title = store["name"] if store else "店铺"
    page = Page(title, "单店购买，价格与库存以结算确认时为准",
                button("查看购物车", lambda: window.show_page("cart", store_id), True))
    if store:
        frame, layout = card("店铺与配送信息")
        layout.addWidget(label(f"取餐点：{store['location_text'] or '商家尚未申请'}"))
        if store["store_point_id"]:
            layout.addWidget(label("虚拟直线距离不超过2公里配送费3元，超过2公里至5公里5元；超过5公里不可下单。", "muted"))
        else:
            layout.addWidget(label("商家尚未设置审核通过的取餐点，暂不能下单。", "muted"))
        page.add(frame)
    if not items:
        addresses = window.data.addresses()
        if store and not store["store_point_id"]:
            page.add(empty("商家尚未设置审核通过的取餐点，暂不能下单。"))
        elif addresses and not any(address["pickup_point_id"] for address in addresses):
            page.add(empty("当前收货地址不在该店配送范围内，请调整收货地址。"))
        else:
            page.add(empty("这家店铺目前没有在售菜品。"))
    for item in items:
        frame, layout = card(item["name"])
        layout.addWidget(label(f"{item['category']} · {money(item['price_cents'])} · "
                               f"库存 {item['available_quantity']}", "muted"))
        quantity = spin(1, 1, 99)

        def add(item_id=item["id"], chooser=quantity):
            def action():
                cart_id = create_cart(window.path, window.account_id, store_id)
                existing = next((line_item for line_item in window.data.cart_items(cart_id)
                                 if line_item["item_id"] == item_id), None)
                current = existing["quantity"] if existing else 0
                change_cart_quantity(window.path, window.account_id, cart_id,
                                     item_id, current + chooser.value())
            if window.perform(action, "已加入购物车"):
                QMessageBox.information(window, "加入成功", "菜品已加入购物车。")

        layout.addLayout(row(quantity, button("加入购物车", add, True)))
        page.add(frame)
    return page.finish()


def cart_page(window, context=None) -> Page:
    cart = window.data.cart(context)
    page = Page("购物车与结算", "单店购买，提交前确认最新价格",
                button("返回选餐", lambda: window.show_page("search"), True))
    if not cart:
        page.add(empty("购物车为空，请先到搜索餐品页面选餐。"))
        return page.finish()
    carts = window.data.nonempty_carts()
    if len(carts) > 1:
        chooser_frame, chooser_layout = card("选择店铺购物车")
        chooser = select([(item["store_name"], item["store_id"]) for item in carts])
        chooser.setCurrentIndex(next((index for index, item in enumerate(carts)
                                      if item["store_id"] == cart["store_id"]), 0))
        chooser.currentIndexChanged.connect(
            lambda _index: window.show_page("cart", chooser.currentData()))
        chooser_layout.addWidget(chooser)
        page.add(chooser_frame)
    items = window.data.cart_items(cart["id"])
    frame, layout = card("购物车菜品")
    for item in items:
        quantity = spin(item["quantity"], 0, 999)

        def change(item_id=item["item_id"], chooser=quantity):
            window.perform(lambda: change_cart_quantity(
                window.path, window.account_id, cart["id"], item_id, chooser.value()),
                "数量已更新")

        layout.addLayout(row(label(f"{item['name']}  {money(item['price_cents'])}"),
                             quantity, button("更新", change)))
    page.add(frame)
    addresses = window.data.addresses()
    store = window.data.store_info(cart["store_id"])
    eligible = [item for item in addresses if item["pickup_point_id"]]
    origin = window.data.store_point(cart["store_id"])
    pickup_by_id = {point["id"]: point for point in list_points(window.path)}
    if origin:
        eligible = [item for item in eligible if item["pickup_point_id"] in pickup_by_id
                    and delivery_fee(origin, pickup_by_id[item["pickup_point_id"]])[1] is not None]
    else:
        eligible = []
    checkout, options = card("收货地址与金额")
    options.addWidget(label("配送规则：虚拟直线距离不超过2公里3元，超过2公里至5公里5元。" if origin
                            else "店铺取餐点尚未通过管理员审核。", "muted"))
    if not eligible:
        options.addWidget(label("店铺取餐点尚未通过管理员审核。" if not origin else
                                "没有位于该店5公里范围内的开放取件点地址。", "muted"))
        options.addWidget(button("调整收货地址", lambda: window.show_page(
            "profile", ("add", cart["store_id"])), True))
        page.add(checkout)
        return page.finish()
    address = select([(f"{item['recipient']} · {item['address_text']} · "
                       f"{pickup_by_id[item['pickup_point_id']]['name']}",
                       item["id"]) for item in eligible])
    form_field(options, "选择收货地址", address)
    options.addWidget(button("调整收货地址", lambda: window.show_page(
        "profile", ("add", cart["store_id"]))))
    note = form_field(options, "备注", line("例如：少辣"))
    food_cents = sum(item["price_cents"] * item["quantity"] for item in items)
    coupons = list_coupons(window.path, window.account_id)
    coupon = form_field(options, "优惠券（只抵餐费）", select([("不使用优惠券", "")] + [
        (f"{item['title']} · {money(item['amount_cents'])}", item["id"]) for item in coupons]))
    preview = label("")
    def update_preview():
        chosen = next(item for item in eligible if item["id"] == address.currentData())
        point = pickup_by_id[chosen["pickup_point_id"]]
        current_fee = delivery_fee(origin, point)[1]
        chosen_coupon = next((item for item in coupons if item["id"] == coupon.currentData()), None)
        discount = min(food_cents, chosen_coupon["amount_cents"]) if chosen_coupon else 0
        preview.setText(f"取件点：{point['name']} · 餐费：{money(food_cents)} · "
                        f"配送费：{money(current_fee)} · 抵扣：{money(discount)} · "
                        f"预计实付：{money(food_cents - discount + current_fee)}")
    address.currentIndexChanged.connect(lambda _index: update_preview())
    coupon.currentIndexChanged.connect(lambda _index: update_preview())
    update_preview()
    options.addWidget(preview)
    options.addWidget(label("提交时会重新核对价格、配送费、库存和配送区域。", "muted"))

    def checkout_action():
        order_id = []

        def action():
            quote = confirm_checkout(window.path, window.account_id,
                                     cart["id"], address.currentData(), coupon_id=coupon.currentData() or None)
            if quote.price_changed:
                answer = QMessageBox.question(
                    window, "价格变化", f"最新餐费 {money(quote.food_amount_cents)}，"
                    f"配送费 {money(quote.delivery_fee_cents)}，优惠券抵扣 {money(quote.coupon_discount_cents)}，实付 {money(quote.total_amount_cents)}。"
                    "是否接受并继续下单？")
                if answer != QMessageBox.StandardButton.Yes:
                    raise ValueError("已取消本次结算确认")
                quote = confirm_checkout(window.path, window.account_id, cart["id"],
                                         address.currentData(), True, quote.fingerprint,
                                         coupon_id=coupon.currentData() or None)
            else:
                answer = QMessageBox.question(
                    window, "核对订单金额",
                    f"餐费 {money(quote.food_amount_cents)}，"
                    f"配送费 {money(quote.delivery_fee_cents)}，"
                    f"优惠券抵扣 {money(quote.coupon_discount_cents)}，实付 {money(quote.total_amount_cents)}。确认下单？")
                if answer != QMessageBox.StandardButton.Yes:
                    raise ValueError("已取消本次下单")
            result = create_order(window.path, window.account_id,
                                  quote.confirmation_id, note.text().strip())
            order_id.append(result.order_id)

        if window.perform(action, "订单已创建") and order_id:
            window.show_page("order_detail", order_id[0])

    options.addWidget(button("确认下单", checkout_action, True))
    page.add(checkout)
    return page.finish()


def orders_page(window, context=None) -> Page:
    page = Page("我的订单", "查看付款、备餐、配送及退款进度",
                button("刷新订单", window.refresh, True))
    orders = window.data.orders()
    if not orders:
        page.add(empty("目前没有订单。"))
    for order in orders:
        frame, layout = card(f"{order['store_name_snapshot']}  ·  {money(order['total_amount_cents'])}")
        layout.addWidget(label(f"{STATUS.get(order['status'], order['status'])}  ·  "
                               f"{local_time(order['placed_at'])}  ·  {order['id'][:12]}", "muted"))
        layout.addWidget(button("查看详情", lambda checked=False, order_id=order["id"]:
                                window.show_page("order_detail", order_id)))
        page.add(frame)
    return page.finish()


def order_detail_page(window, order_id: str) -> Page:
    order = view_order(window.path, window.account_id, order_id)
    items = window.data.order_items(order_id)
    page = Page("订单详情", f"订单 {order_id}",
                button("返回订单", lambda: window.show_page("orders"), True))
    left, left_layout = card("付款状态与订单金额")
    left_layout.addWidget(label(STATUS.get(order["status"], order["status"]), "cardtitle"))
    left_layout.addWidget(label(money(order["total_amount_cents"]), "metric"))
    payment = window.data.payment_request(order_id)
    if payment:
        left_layout.addWidget(label(f"付款请求：{payment['result']}", "muted"))
    if order["status"] == "pending_payment":
        def pay():
            def action():
                request_id = (payment["request_id"] if payment and
                              payment["result"] == "pending" else uuid4().hex)
                if not payment or payment["result"] != "pending":
                    start_payment(window.path, window.account_id, order_id, request_id)
                apply_payment_result(window.path, request_id, True)
            window.perform(action, "模拟付款成功")
        left_layout.addWidget(button("模拟付款", pay, True))
    if order["status"] == "delivered":
        left_layout.addWidget(button("确认收货", lambda: window.perform(
            lambda: confirm_completion(window.path, window.account_id, order_id),
            "订单已完成"), True))

    right, right_layout = card("履约进度与订单快照")
    right_layout.addWidget(label(f"状态：{STATUS.get(order['status'], order['status'])}"))
    right_layout.addWidget(label("菜品：" + "、".join(
        f"{item['item_name_snapshot']} × {item['quantity']}" for item in items)))
    right_layout.addWidget(label(f"收货人：{order['recipient']}  {order['phone']}", "muted"))
    right_layout.addWidget(label(f"地址：{order['address']}", "muted"))
    if order["pickup_point"]:
        right_layout.addWidget(label(f"取件点：{order['pickup_point']} {order['pickup_coordinates']} · "
                                     f"店铺取餐点：{order['store_point']} {order['store_coordinates']} · "
                                     f"虚拟直线距离：{order['distance_km']:.2f} 公里", "muted"))
    right_layout.addWidget(label(f"优惠券抵扣：{money(order['coupon_discount_cents'])} · "
                                 f"配送费：{money(order['delivery_fee_cents'])}", "muted"))
    if order["delivery_exception"]:
        right_layout.addWidget(label(f"配送异常：{order['delivery_exception']['reason']}"))
    page.add_row(left, right)
    if order["status"] not in ("completed", "cancelled", "delivered"):
        page.add(button("取消与退款", lambda: window.show_page("cancel", order_id)))
    refund = window.data.refund(order_id)
    if refund:
        frame, layout = card("退款进度")
        layout.addWidget(label(f"{money(refund['amount_cents'])} · {refund['result']}"))
        page.add(frame)
    if order["status"] in ("completed", "cancelled", "delivery_exception", "delivered"):
        reason = line("说明争议原因")
        frame, layout = card("联系管理员 · 订单争议")
        layout.addWidget(reason)
        layout.addWidget(button("提交争议", lambda: window.perform(
            lambda: open_dispute(window.path, window.account_id,
                                 order_id, reason.text().strip()), "争议已提交")))
        page.add(frame)
    elif order["status"] != "pending_payment":
        reason = line("请说明订单问题")
        frame, layout = card("联系管理员")
        layout.addWidget(reason)
        layout.addWidget(button("提交给管理员", lambda: window.perform(
            lambda: open_dispute(window.path, window.account_id,
                                 order_id, reason.text().strip()), "已提交管理员处理")))
        page.add(frame)
    return page.finish()


def cancellation_page(window, order_id: str) -> Page:
    order = view_order(window.path, window.account_id, order_id)
    page = Page("取消与退款", "按当前履约阶段选择适用的处理方式",
                button("返回订单", lambda: window.show_page("order_detail", order_id)))
    status_frame, status_layout = card("订单当前阶段")
    status_layout.addWidget(label(STATUS.get(order["status"], order["status"]), "cardtitle"))
    if order["cancellation"]:
        status_layout.addWidget(label(f"取消申请：{order['cancellation']['status']}", "muted"))
    page.add(status_frame)
    frame, layout = card("取消申请")
    reason = form_field(layout, "取消原因", line("请说明原因"))
    layout.addWidget(button("提交申请", lambda: window.perform(
        lambda: request_cancellation(window.path, window.account_id,
                                     order_id, reason.text().strip()),
        "取消申请已提交"), True))
    page.add(frame)
    refund = window.data.refund(order_id)
    result, result_layout = card("退款进度")
    result_layout.addWidget(label(
        f"{money(refund['amount_cents'])} · {refund['result']}" if refund else "暂无退款记录",
        "muted"))
    page.add(result)
    return page.finish()


def pickup_points_page(window, context=None) -> Page:
    store_id = context[0] if isinstance(context, tuple) else None
    x_value, y_value, keyword_value = context[1:] if isinstance(context, tuple) else (0.0, 0.0, "")
    page = Page("选择取件点", "固定测试地图；坐标和距离均为实验用虚拟数据，不代表真实道路距离")
    frame, layout = card("输入虚拟坐标或搜索名称")
    x = QDoubleSpinBox()
    y = QDoubleSpinBox()
    for control, value in ((x, x_value), (y, y_value)):
        control.setRange(-1000, 1000)
        control.setDecimals(2)
        control.setValue(value)
    keyword = line("点位名称", value=keyword_value)
    layout.addLayout(row(label("横坐标"), x, label("纵坐标"), y, keyword))
    layout.addWidget(button("查找点位", lambda: window.show_page(
        "pickup_points", (store_id, x.value(), y.value(), keyword.text().strip())), True))
    page.add(frame)
    page.add(VirtualMap(list_points(window.path), x_value, y_value))
    origin = window.data.store_point(store_id) if store_id else None
    points = nearby_points(window.path, x_value, y_value, keyword=keyword_value)
    for point in points:
        card_frame, card_layout = card(point["name"])
        text = f"虚拟坐标 ({point['x']:.2f}, {point['y']:.2f}) · 距输入位置 {point['distance_km']:.2f} 公里"
        if origin:
            distance, fee = delivery_fee(origin, point)
            text += f" · 距店铺 {distance:.2f} 公里 · " + (f"配送费 {money(fee)}" if fee is not None else "超出配送范围")
        card_layout.addWidget(label(text, "muted"))
        card_layout.addWidget(button("选择此取件点", lambda checked=False, pid=point["id"]:
                                     window.show_page("profile", ("add", store_id, pid))))
        page.add(card_frame)
    return page.finish()


def report_page(window, context=None) -> Page:
    page = Page("举报店铺或菜品", "填写详细描述，只有管理员可查看举报内容")
    stores = [item for item in window.data.all_stores() if item["status"] != "disabled"]
    if not stores:
        page.add(empty("当前没有可举报的店铺。"))
        return page.finish()
    frame, layout = card("提交举报")
    store_box = form_field(layout, "店铺", select([(item["name"], item["id"]) for item in stores]))
    item_box = form_field(layout, "菜品（可不选）", select([("整个店铺", "")]))
    def refresh_items():
        item_box.clear()
        item_box.addItem("整个店铺", "")
        if store_box.currentData():
            for item in window.data.public_items(store_box.currentData()):
                item_box.addItem(item["name"], item["id"])
    store_box.currentIndexChanged.connect(lambda _index: refresh_items())
    refresh_items()
    description = form_field(layout, "详细描述", line("请说明菜品、价格或店铺问题"))
    layout.addWidget(button("提交举报", lambda: window.perform(
        lambda: submit_report(window.path, window.account_id, store_box.currentData(),
                              item_box.currentData() or None, description.text().strip()),
        "举报已提交给管理员", "home"), True))
    page.add(frame)
    return page.finish()


def profile_page(window, context=None) -> Page:
    account = window.data.account()
    store_id = context[1] if isinstance(context, tuple) else None
    selected_point = context[2] if isinstance(context, tuple) and len(context) > 2 else None
    page = Page("个人资料与收货地址", "管理联系方式与常用收货地址",
                button("新增地址", lambda: window.show_page("profile", ("add", store_id)), True))
    info, info_layout = card("基本资料")
    info_layout.addWidget(label(f"账号：{account['id']}", "muted"))
    phone = form_field(info_layout, "联系电话", line(value=account["phone"]))
    info_layout.addWidget(button("保存资料", lambda: window.perform(
        lambda: update_phone(window.path, window.account_id, phone.text().strip()),
        "资料已保存"), True))
    page.add(info)
    addresses = window.data.addresses()
    frame, layout = card("常用地址")
    point_names = {point["id"]: point["name"] for point in list_points(window.path, active_only=False)}
    for item in addresses:
        layout.addWidget(label(f"{item['recipient']} · {item['phone']}\n"
                               f"{item['address_text']}（取件点：{point_names.get(item['pickup_point_id'], '未选择')}）"))
        layout.addWidget(button("编辑地址", lambda checked=False, address=item:
                                window.show_page("profile", address)))
    if not addresses:
        layout.addWidget(label("暂无地址", "muted"))
    page.add(frame)
    if context:
        editing = context if isinstance(context, dict) else {}
        editor, editor_layout = card("编辑地址" if editing else "新增地址")
        recipient = form_field(editor_layout, "收货人", line(value=editing.get("recipient", "")))
        telephone = form_field(editor_layout, "联系电话", line(value=editing.get("phone", "")))
        pickup_id = selected_point or editing.get("pickup_point_id")
        points = {point["id"]: point for point in list_points(window.path)}
        address_text = form_field(editor_layout, "详细说明", line(value=editing.get("address_text", "")))
        editor_layout.addWidget(label("取件点：" + (points[pickup_id]["name"] if pickup_id in points else "未选择")))
        editor_layout.addWidget(button("按坐标或名称选择取件点", lambda: window.show_page(
            "pickup_points", (store_id, 0.0, 0.0, ""))))
        destination = ("cart", store_id) if store_id else "profile"
        editor_layout.addWidget(button("保存地址", lambda: window.perform(
            lambda: save_address(window.path, window.account_id,
                                 recipient.text().strip(), telephone.text().strip(),
                                 address_text.text().strip(), editing.get("area_code", ""),
                                 editing.get("id"), pickup_id), "地址已保存", destination), True))
        page.add(editor)
    return page.finish()


def notifications_page(window, context=None) -> Page:
    page = Page("通知中心", "订单状态通知",
                button("刷新通知", window.refresh, True))
    notices = list_notifications(window.path, window.account_id)
    page.add(table(["时间", "订单", "内容", "状态"], [
        [local_time(item["created_at"]), item["order_id"][:12], item["content"], item["status"]]
        for item in reversed(notices)]))
    return page.finish()
