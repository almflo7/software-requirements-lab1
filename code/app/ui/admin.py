"""管理员页面：审核、派单、异常、统计和通知。"""

import csv

from PySide6.QtWidgets import QDoubleSpinBox, QFileDialog, QInputDialog, QMessageBox

from app.after_sales import decide_cancellation, list_failed_refunds, retry_failed_refund
from app.audit import query_audit_logs
from app.catalog import review_account
from app.categories import list_categories, manage_category
from app.map_points import (import_points, list_points, list_store_point_requests,
                            review_store_point, save_point)
from app.promotions import (issue_coupon, list_issued_coupons, list_reports,
                            resolve_report, set_coupon_active)
from app.delivery import assign_courier, resolve_exception
from app.disputes import resolve_dispute, view_dispute
from app.identity import disable_account, disable_store
from app.notifications import dispatch_pending, list_admin_exceptions, list_notifications
from app.reporting import query_statistics
from app.support import run_due_jobs
from app.ui.customer import STATUS, money
from app.ui.timefmt import local_time, local_today
from app.ui.widgets import (
    Page, amount, button, card, empty, form_field, label, line, metric_card,
    row, select, table,
)


def build_admin_page(window, key: str, context=None) -> Page:
    pages = {
        "admin_home": home_page, "approvals": approvals_page,
        "assign": assignment_page, "exceptions": exceptions_page,
        "statistics": statistics_page, "notifications": notifications_page,
        "platform_tools": platform_tools_page, "reports": reports_page,
    }
    return pages.get(key, home_page)(window, context)


def home_page(window, context=None) -> Page:
    orders = window.data.orders()
    page = Page("管理员业务首页", "查看待处理申请、订单和异常",
                button("运行到期任务", lambda: window.perform(
                    lambda: run_due_jobs(window.path), "到期任务已处理"), True))
    page.add_row(
        metric_card("待审核申请", str(len(window.data.approvals()))),
        metric_card("待派单", str(sum(order["status"] == "awaiting_assignment"
                                 for order in orders))),
        metric_card("待处理异常", str(sum(item["status"] == "pending"
                                  for item in window.data.exceptions()))),
    )
    frame, layout = card("常用操作")
    layout.addLayout(row(
        button("资格审核", lambda: window.show_page("approvals")),
        button("人工派单", lambda: window.show_page("assign")),
        button("异常处理", lambda: window.show_page("exceptions")),
    ))
    page.add(frame)
    return page.finish()


def approvals_page(window, context=None) -> Page:
    page = Page("资格审核与账号处理", "待办申请与进行中业务一并核对",
                button("刷新申请", window.refresh, True))
    requests = window.data.approvals()
    if not requests:
        page.add(empty("当前没有待审核申请。"))
    for request in requests:
        frame, layout = card(f"{request['account_id']} · {request['role']}")
        layout.addWidget(label(f"电话：{request['phone']} · 提交：{local_time(request['submitted_at'])}",
                               "muted"))
        layout.addLayout(row(
            button("通过", lambda checked=False, rid=request["id"]:
                   window.perform(lambda: review_account(
                       window.path, window.account_id, rid, True), "审核已通过"), True),
            button("拒绝", lambda checked=False, rid=request["id"]:
                   window.perform(lambda: review_account(
                       window.path, window.account_id, rid, False), "申请已拒绝")),
        ))
        page.add(frame)
    accounts, accounts_layout = card("账号管理")
    accounts_layout.addWidget(table(["账号", "角色", "状态", "电话"], [
        [item["id"], {"customer": "顾客", "merchant": "商家", "courier": "配送员", "admin": "管理员"}.get(item["role"], item["role"]),
         {"active": "正常", "pending": "待审核", "disabled": "已停用"}.get(item["status"], item["status"]), item["phone"]]
        for item in window.data.accounts()
    ]))
    account_choices = [(f"{item['id']} · {item['role']} · {item['status']}", item["id"])
                       for item in window.data.accounts() if item["role"] != "admin"
                       and item["status"] != "disabled"]
    if account_choices:
        target = select(account_choices)
        accounts_layout.addLayout(row(target, button("停用账号", lambda: _disable_account(
            window, target.currentData()))))
    page.add(accounts)
    stores, store_layout = card("店铺管理")
    store_choices = [(f"{item['name']} · {item['status']}", item["id"])
                     for item in window.data.all_stores() if item["status"] != "disabled"]
    if store_choices:
        target_store = select(store_choices)
        store_layout.addLayout(row(target_store, button("停用店铺", lambda: _disable_store(
            window, target_store.currentData()))))
    else:
        store_layout.addWidget(label("暂无可停用店铺", "muted"))
    page.add(stores)
    return page.finish()


def _unfulfillable_orders(window, target: str, store: bool) -> tuple[str, ...] | None:
    prompt = ("填写已确认无法履约的订单编号，多个编号用逗号分隔；"
              "可以留空，表示现有订单继续履约。")
    value, accepted = QInputDialog.getText(window, "确认停用范围", prompt)
    if not accepted:
        return None
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _disable_account(window, target_id: str) -> None:
    affected = _unfulfillable_orders(window, target_id, False)
    if affected is not None:
        window.perform(lambda: disable_account(window.path, window.account_id,
                                               target_id, affected), "账号已停用")


def _disable_store(window, store_id: str) -> None:
    affected = _unfulfillable_orders(window, store_id, True)
    if affected is not None:
        window.perform(lambda: disable_store(window.path, window.account_id,
                                             store_id, affected), "店铺已停用")


def platform_tools_page(window, context=None) -> Page:
    page = Page("点位、分类与优惠券", "管理员维护虚拟地图、统一分类和全员优惠券")
    point_frame, point_layout = card("开放取件点（坐标单位：虚拟公里）")
    points = list_points(window.path, active_only=False)
    point_layout.addWidget(table(["名称", "横坐标", "纵坐标", "状态"], [
        [item["name"], item["x"], item["y"], "开放" if item["active"] else "停用"]
        for item in points]))
    point_name = form_field(point_layout, "名称", line("取件点名称"))
    x = QDoubleSpinBox()
    y = QDoubleSpinBox()
    for control in (x, y):
        control.setRange(-1000, 1000)
        control.setDecimals(2)
    point_layout.addLayout(row(label("横坐标"), x, label("纵坐标"), y))
    point_layout.addWidget(button("新增取件点", lambda: window.perform(
        lambda: save_point(window.path, window.account_id, point_name.text(),
                           x.value(), y.value()), "取件点已新增"), True))
    if points:
        point_box = select([(item["name"], item["id"]) for item in points])
        def load_point():
            selected_point = next(item for item in points if item["id"] == point_box.currentData())
            point_name.setText(selected_point["name"])
            x.setValue(selected_point["x"])
            y.setValue(selected_point["y"])
        point_box.currentIndexChanged.connect(lambda _index: load_point())
        point_layout.addWidget(label("选择下方点位后，可修改名称和坐标并重新启用。", "muted"))
        point_layout.addLayout(row(point_box, button("停用所选点位", lambda: window.perform(
            lambda: save_point(window.path, window.account_id,
                next(item["name"] for item in points if item["id"] == point_box.currentData()),
                next(item["x"] for item in points if item["id"] == point_box.currentData()),
                next(item["y"] for item in points if item["id"] == point_box.currentData()),
                point_box.currentData(), False), "点位已停用")),
            button("保存修改并启用", lambda: window.perform(
                lambda: save_point(window.path, window.account_id, point_name.text(),
                                   x.value(), y.value(), point_box.currentData(), True),
                "点位已更新"))))

    def import_csv():
        filename, _ = QFileDialog.getOpenFileName(window, "导入取件点", "", "CSV (*.csv)")
        if not filename:
            return
        try:
            with open(filename, encoding="utf-8-sig", newline="") as source:
                rows = [(row["name"], float(row["x"]), float(row["y"]))
                        for row in csv.DictReader(source)]
            prepared = import_points(window.path, window.account_id, rows, True)
        except (ValueError, KeyError, OSError) as error:
            QMessageBox.warning(window, "导入失败", str(error))
            return
        preview = "\n".join(f"{item['name']} ({item['x']}, {item['y']})" for item in prepared[:10])
        if QMessageBox.question(window, "导入预览", f"共 {len(prepared)} 个点位：\n{preview}\n确认整体导入？") == QMessageBox.StandardButton.Yes:
            window.perform(lambda: import_points(window.path, window.account_id, rows, False), "取件点已导入")
    point_layout.addWidget(button("从 CSV 导入并预览", import_csv))
    page.add(point_frame)

    requests = [item for item in list_store_point_requests(window.path, window.account_id)
                if item["status"] == "pending"]
    if requests:
        frame, layout = card("商家取餐点申请")
        for item in requests:
            layout.addWidget(label(f"店铺 {item['store_id'][:12]} · {item['name']} ({item['x']}, {item['y']})"))
            layout.addLayout(row(
                button("通过", lambda checked=False, rid=item["id"]: window.perform(
                    lambda: review_store_point(window.path, window.account_id, rid, True), "申请已通过")),
                button("拒绝", lambda checked=False, rid=item["id"]: window.perform(
                    lambda: review_store_point(window.path, window.account_id, rid, False), "申请已拒绝"))))
        page.add(frame)

    category_frame, category_layout = card("统一菜品分类")
    categories = list_categories(window.path, False)
    category_layout.addWidget(label("、".join(
        f"{item['name']}（{'启用' if item['active'] else '停用'}）" for item in categories)))
    category_name = form_field(category_layout, "新分类", line("分类名称"))
    category_layout.addWidget(button("新增分类", lambda: window.perform(
        lambda: manage_category(window.path, window.account_id, "add", category_name.text()),
        "分类已新增")))
    if categories:
        selected = select([(item["name"], item["name"]) for item in categories])
        rename = line("新名称")
        category_layout.addLayout(row(selected, rename))
        category_layout.addLayout(row(
            button("改名", lambda: window.perform(lambda: manage_category(
                window.path, window.account_id, "rename", selected.currentData(), rename.text()), "分类已改名")),
            button("停用", lambda: window.perform(lambda: manage_category(
                window.path, window.account_id, "disable", selected.currentData()), "分类已停用")),
            button("启用", lambda: window.perform(lambda: manage_category(
                window.path, window.account_id, "enable", selected.currentData()), "分类已启用"))))
    page.add(category_frame)
    coupon_frame, coupon_layout = card("发放全员优惠券")
    issued = list_issued_coupons(window.path, window.account_id)
    coupon_layout.addWidget(table(["名称", "金额", "状态"], [
        [item["title"], money(item["amount_cents"]), "启用" if item["active"] else "停用"]
        for item in issued]))
    title = form_field(coupon_layout, "名称", line("优惠券名称"))
    value = form_field(coupon_layout, "固定抵扣金额（元，仅抵餐费）", amount())
    coupon_layout.addWidget(button("发放给所有顾客", lambda: window.perform(
        lambda: issue_coupon(window.path, window.account_id, title.text(),
                             round(value.value() * 100)), "全员优惠券已发放"), True))
    if issued:
        coupon_box = select([(item["title"], item["id"]) for item in issued])
        coupon_layout.addLayout(row(coupon_box,
            button("停用", lambda: window.perform(lambda: set_coupon_active(
                window.path, window.account_id, coupon_box.currentData(), False), "优惠券已停用")),
            button("启用", lambda: window.perform(lambda: set_coupon_active(
                window.path, window.account_id, coupon_box.currentData(), True), "优惠券已启用"))))
    page.add(coupon_frame)
    return page.finish()


def reports_page(window, context=None) -> Page:
    page = Page("顾客举报处理", "仅管理员可查看举报详情")
    reports = list_reports(window.path, window.account_id)
    if not reports:
        page.add(empty("暂无举报。"))
    for item in reports:
        frame, layout = card(f"店铺 {item['store_id'][:12]} · {item['status']}")
        layout.addWidget(label(f"举报人：{item['customer_id']} · 提交：{local_time(item['created_at'])}"))
        layout.addWidget(label(f"菜品：{item['item_name_snapshot'] or '整个店铺'} · "
                               f"提交时价格：{money(item['item_price_snapshot_cents']) if item['item_price_snapshot_cents'] is not None else '无'}"))
        layout.addWidget(label(item["description"]))
        if item["status"] == "pending":
            decision = line("填写处理结果")
            layout.addLayout(row(decision, button("记录处理结果", lambda checked=False,
                rid=item["id"], field=decision: window.perform(
                    lambda: resolve_report(window.path, window.account_id, rid,
                                           field.text().strip()), "举报已处理"))))
        else:
            layout.addWidget(label("处理结果：" + (item["decision"] or "")))
        page.add(frame)
    return page.finish()


def assignment_page(window, context=None) -> Page:
    page = Page("人工派单与异常", "核对配送人员资格、任务及交接记录",
                button("刷新订单", window.refresh, True))
    candidates = [order for order in window.data.orders() if order["status"] in
                  ("awaiting_assignment", "awaiting_courier", "awaiting_pickup", "delivering")]
    left, left_layout = card("待分配与可改派订单")
    left_layout.addWidget(table(["订单", "店铺", "状态", "时间"], [
        [order["id"][:12], order["store_name_snapshot"],
         STATUS.get(order["status"], order["status"]), local_time(order["placed_at"])]
        for order in candidates
    ]))
    page.add(left)
    right, right_layout = card("选择配送人员")
    couriers = window.data.couriers()
    if candidates and couriers:
        order_box = select([(f"{order['id'][:12]} · {order['store_name_snapshot']} · "
                             f"{STATUS.get(order['status'], order['status'])}", order["id"])
                            for order in candidates])
        courier_box = select([(item["id"], item["id"]) for item in couriers])
        form_field(right_layout, "订单", order_box)
        form_field(right_layout, "配送员", courier_box)

        def assign():
            order = next(item for item in candidates if item["id"] == order_box.currentData())
            handover = order["status"] == "delivering"
            if handover:
                reply = QMessageBox.question(window, "确认餐品交接", "已确认餐品交接完成？")
                if reply != QMessageBox.StandardButton.Yes:
                    return
            reassigned = order["status"] != "awaiting_assignment"
            if window.perform(lambda: assign_courier(
                window.path, window.account_id, order_box.currentData(),
                courier_box.currentData(), handover), "订单已改派" if reassigned else "派单已保存") and reassigned:
                QMessageBox.information(window, "改派成功", "订单已分配给新配送员，原配送员权限已收回。")

        right_layout.addWidget(button("确认分配", assign, True))
    else:
        right_layout.addWidget(label("需要可派订单和审核通过的配送员。", "muted"))
    page.add(right)
    return page.finish()


def exceptions_page(window, context=None) -> Page:
    page = Page("异常处理", "处理配送异常、退款失败、取消申请和争议",
                button("刷新异常", window.refresh, True))
    for exception in window.data.exceptions():
        if exception["status"] != "pending":
            continue
        frame, layout = card(f"配送异常 · 订单 {exception['order_id'][:12]}")
        layout.addWidget(label(f"{exception['reason']} · {local_time(exception['reported_at'])}"))
        layout.addLayout(row(
            button("继续配送", lambda checked=False, eid=exception["id"]:
                   window.perform(lambda: resolve_exception(
                       window.path, window.account_id, eid, False), "异常已处理"), True),
            button("终止并退款", lambda checked=False, eid=exception["id"]:
                   window.perform(lambda: resolve_exception(
                       window.path, window.account_id, eid, True), "订单已终止")),
            button("改派", lambda checked=False, eid=exception["id"]:
                   _reassign_exception(window, eid)),
        ))
        page.add(frame)
    refunds = list_failed_refunds(window.path, window.account_id)
    if refunds:
        frame, layout = card("退款失败")
        for item in refunds:
            layout.addWidget(label(f"订单 {item['order_id'][:12]} · "
                                   f"{money(item['amount_cents'])} · "
                                   f"{item['failure_reason'] or '无失败原因'}"))
            layout.addWidget(button("重试退款", lambda checked=False, oid=item["order_id"]:
                                    window.perform(lambda: retry_failed_refund(
                                        window.path, window.account_id, oid),
                                        "退款已重新提交"), True))
        page.add(frame)
    requests = window.data.pending_cancellations()
    if requests:
        frame, layout = card("取消申请")
        for item in requests:
            layout.addWidget(label(f"订单 {item['order_id'][:12]} · {item['reason']}"))
            layout.addLayout(row(
                button("批准", lambda checked=False, rid=item["id"]:
                       window.perform(lambda: decide_cancellation(
                           window.path, window.account_id, rid, True), "取消已批准"), True),
                button("拒绝", lambda checked=False, rid=item["id"]:
                       window.perform(lambda: decide_cancellation(
                           window.path, window.account_id, rid, False), "取消已拒绝")),
            ))
        page.add(frame)
    disputes = [item for item in window.data.disputes() if item["status"] == "open"]
    if disputes:
        frame, layout = card("待处理争议")
        for item in disputes:
            layout.addWidget(label(f"订单 {item['order_id'][:12]} · {item['reason']}"))
            layout.addWidget(button("查看并处理", lambda checked=False, did=item["id"]:
                                    _handle_dispute(window, did)))
        page.add(frame)
    if not any([window.data.exceptions(), refunds, requests, disputes]):
        page.add(empty("当前没有异常记录。"))
    return page.finish()


def _reassign_exception(window, exception_id: str) -> None:
    couriers = window.data.couriers()
    if not couriers:
        QMessageBox.warning(window, "无法改派", "没有可用配送员")
        return
    courier, accepted = QInputDialog.getItem(
        window, "异常改派", "新配送员", [item["id"] for item in couriers], 0, False)
    if not accepted:
        return
    reply = QMessageBox.question(window, "确认餐品交接", "已确认餐品交接完成？")
    if reply == QMessageBox.StandardButton.Yes:
        if window.perform(lambda: resolve_exception(
            window.path, window.account_id, exception_id, False,
            new_courier_id=courier, handover_confirmed=True), "已改派"):
            QMessageBox.information(window, "改派成功", "配送异常订单已分配给新配送员。")


def _handle_dispute(window, dispute_id: str) -> None:
    detail = []
    if not window.perform(lambda: detail.append(view_dispute(
        window.path, window.account_id, dispute_id)), "已查看争议"):
        return
    if not detail:
        return
    decision, accepted = QInputDialog.getText(
        window, "处理争议", f"订单 {detail[0]['order_id'][:12]}\n原因：{detail[0]['reason']}\n处理决定")
    if not accepted:
        return
    refund = QMessageBox.question(window, "退款决定", "是否全额退款？")
    window.perform(lambda: resolve_dispute(
        window.path, window.account_id, dispute_id, decision.strip(),
        refund == QMessageBox.StandardButton.Yes), "争议已处理")


def statistics_page(window, context=None) -> Page:
    today = local_today()
    page = Page("统计与操作日志", "平台数据按完成日期与退款成功日期统计",
                button("刷新", window.refresh, True))
    filters, layout = card("筛选日期")
    start = line(value=today[:8] + "01")
    end = line(value=today)
    layout.addLayout(row(label("起始日期"), start, label("结束日期"), end))
    page.add(filters)

    def load():
        try:
            result = query_statistics(window.path, window.account_id,
                                      start.text().strip(), end.text().strip())
            logs = query_audit_logs(window.path, window.account_id,
                                    date_from=start.text().strip(), date_to=end.text().strip())
        except ValueError as error:
            QMessageBox.warning(window, "查询失败", str(error))
            return
        results, results_layout = card("数据概览")
        results_layout.addWidget(label(f"已完成订单：{result['completed_orders']}"))
        results_layout.addWidget(label(f"营业额：{money(result['revenue_cents'])}"))
        results_layout.addWidget(label(f"退款成功：{money(result['refund_cents'])}"))
        results_layout.addWidget(label(f"差额：{money(result['difference_cents'])}"))
        results_layout.addWidget(table(["热销菜品", "销量"], result["popular_items"]))
        page.add(results)
        audit_frame, audit_layout = card("关键操作日志")
        audit_layout.addWidget(table(["时间", "操作者", "操作内容", "结果"], [
            [local_time(item["occurred_at"]), item["actor_id"] or "系统",
             item["action"], item["result"]] for item in reversed(logs[-100:])
        ]))
        audit_layout.addWidget(button("导出筛选结果", lambda: _export(window, result, logs), True))
        page.add(audit_frame)

    layout.addWidget(button("查询", load, True))
    return page.finish()


def _export(window, result: dict, logs: list[dict]) -> None:
    path, _ = QFileDialog.getSaveFileName(window, "导出统计与日志", "统计结果.csv", "CSV (*.csv)")
    if not path:
        return
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["已完成订单", result["completed_orders"]])
            writer.writerow(["营业额（分）", result["revenue_cents"]])
            writer.writerow(["退款成功（分）", result["refund_cents"]])
            writer.writerow(["差额（分）", result["difference_cents"]])
            writer.writerow([])
            writer.writerow(["时间", "操作者", "订单", "操作", "结果", "原因"])
            for item in logs:
                writer.writerow([local_time(item["occurred_at"]), item["actor_id"], item["order_id"],
                                 item["action"], item["result"], item["reason"]])
    except OSError as error:
        QMessageBox.warning(window, "导出失败", str(error))
        return
    window.statusBar().showMessage("筛选结果已导出", 6000)


def notifications_page(window, context=None) -> Page:
    page = Page("通知中心", "订单状态通知与发送失败处理",
                button("处理待发通知", lambda: window.perform(
                    lambda: dispatch_pending(window.path), "通知已处理"), True))
    notices = list_notifications(window.path, window.account_id)
    frame, layout = card("通知列表")
    layout.addWidget(table(["时间", "订单", "事件", "发送结果"], [
        [local_time(item["created_at"]), item["order_id"][:12], item["content"], item["status"]]
        for item in reversed(notices)
    ]))
    page.add(frame)
    failures = list_admin_exceptions(window.path, window.account_id)
    failure_frame, failure_layout = card("发送异常")
    if failures:
        failure_layout.addWidget(table(["订单", "内容", "尝试次数", "状态"], [
            [item["order_id"][:12], item["content"], item["attempt_count"], item["status"]]
            for item in failures
        ]))
    else:
        failure_layout.addWidget(label("暂无发送异常。", "muted"))
    page.add(failure_frame)
    return page.finish()
