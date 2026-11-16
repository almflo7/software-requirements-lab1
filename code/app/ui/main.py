"""桌面程序入口：共享侧边栏、顶栏、登录会话和页面导航。"""

import argparse
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.db import initialize_schema
from app.identity import login, register, session_account
from app.notifications import dispatch_pending
from app.ui.data import UiData
from app.ui.widgets import STYLE, Page, button, card, form_field, label, line


NAVIGATION = {
    "customer": [
        ("home", "首页"), ("search", "搜索餐品"), ("cart", "购物车"),
        ("orders", "我的订单"), ("profile", "个人资料"),
        ("pickup_points", "选择取件点"), ("report", "举报"), ("notifications", "通知"),
    ],
    "merchant": [
        ("merchant_home", "业务首页"), ("merchant_orders", "订单处理"),
        ("store", "店铺菜品"), ("statistics", "本店统计"),
        ("notifications", "通知"),
    ],
    "courier": [
        ("tasks", "我的配送任务"), ("history", "历史任务"),
        ("notifications", "通知"), ("profile", "个人资料"),
    ],
    "admin": [
        ("admin_home", "业务首页"), ("approvals", "资格审核"),
        ("assign", "人工派单"), ("exceptions", "异常处理"),
        ("platform_tools", "点位与分类"), ("reports", "举报处理"),
        ("statistics", "统计与日志"), ("notifications", "通知中心"),
    ],
}

HOME = {"customer": "home", "merchant": "merchant_home",
        "courier": "tasks", "admin": "admin_home"}


class MainWindow(QMainWindow):
    """按角色显示页面，并用业务层处理所有写操作。"""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.account_id = ""
        self.token = ""
        self.role = ""
        self.data: UiData | None = None
        self.page_key = "home"
        self.context = None
        self.setWindowTitle("外卖点餐与配送系统")
        self.resize(1420, 920)
        self.setMinimumSize(1020, 660)
        self.setStyleSheet(STYLE)

        shell = QWidget()
        shell.setObjectName("shell")
        horizontal = QHBoxLayout(shell)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(235)
        self.nav_layout = QVBoxLayout(self.sidebar)
        self.nav_layout.setContentsMargins(16, 35, 16, 20)
        self.nav_layout.setSpacing(8)
        horizontal.addWidget(self.sidebar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("topbar")
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(28, 13, 28, 13)
        top_layout.addWidget(label("外卖点餐与配送系统", "cardtitle"))
        top_layout.addStretch()
        self.identity_label = label("未登录", "muted")
        top_layout.addWidget(self.identity_label)
        self.logout_button = button("退出登录", self.logout)
        top_layout.addWidget(self.logout_button)
        right_layout.addWidget(topbar)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(self.content_layout, 1)
        horizontal.addWidget(right, 1)
        self.setCentralWidget(shell)
        self.show_page("home")

    def _rebuild_navigation(self) -> None:
        while self.nav_layout.count():
            item = self.nav_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        role_name = {"customer": "顾客端", "merchant": "商家端",
                     "courier": "配送人员端", "admin": "管理员端"}.get(self.role, "顾客端")
        heading = label(role_name)
        heading.setStyleSheet("color: #f4e5d8; font-size: 16px; padding: 12px;")
        self.nav_layout.addWidget(heading)
        self.nav_layout.addSpacing(26)
        for key, title in NAVIGATION.get(self.role, [("home", "首页")]):
            item = QPushButton(title)
            item.setObjectName("nav")
            item.setProperty("active", "true" if key == self.page_key else "false")
            item.clicked.connect(lambda checked=False, target=key: self.show_page(target))
            self.nav_layout.addWidget(item)
        self.nav_layout.addStretch()
        footer = label("课程实验 · 桌面界面")
        footer.setStyleSheet("color: #d8c4b6; padding: 12px;")
        self.nav_layout.addWidget(footer)
        self.identity_label.setText(
            f"{role_name} · {self.account_id}" if self.account_id else "未登录"
        )
        self.logout_button.setVisible(bool(self.account_id))

    def show_page(self, key: str, context=None) -> None:
        if self.account_id:
            try:
                valid = session_account(self.path, self.token) == self.account_id
                account = self.data.account()
                valid = valid and (account.get("status") == "active" or
                                   account.get("role") == "customer")
            except (ValueError, sqlite3.DatabaseError):
                valid = False
            if not valid:
                self.account_id = ""
                self.token = ""
                self.role = ""
                self.data = None
                key, context = "home", None
        self.page_key, self.context = key, context
        self._rebuild_navigation()
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self.account_id:
            page = self._welcome_page() if key != "register" else self._register_page()
        elif self.role == "customer":
            from app.ui.customer import build_customer_page
            page = build_customer_page(self, key, context)
        elif self.role == "merchant":
            from app.ui.merchant import build_merchant_page
            page = build_merchant_page(self, key, context)
        elif self.role == "courier":
            from app.ui.courier import build_courier_page
            page = build_courier_page(self, key, context)
        else:
            from app.ui.admin import build_admin_page
            page = build_admin_page(self, key, context)
        self.content_layout.addWidget(page)

    def refresh(self) -> None:
        self.show_page(self.page_key, self.context)

    def perform(self, action, success: str = "操作已完成", destination=None) -> bool:
        """业务错误在界面显示；成功后刷新当前页或切换页面。"""
        try:
            action()
            dispatch_pending(self.path)
        except (ValueError, OSError, sqlite3.DatabaseError) as error:
            QMessageBox.warning(self, "操作未完成", str(error))
            return False
        self.statusBar().showMessage(success, 6000)
        if destination:
            if isinstance(destination, tuple):
                self.show_page(*destination)
            else:
                self.show_page(destination)
        else:
            self.refresh()
        return True

    def logout(self) -> None:
        self.account_id = ""
        self.token = ""
        self.role = ""
        self.data = None
        self.show_page("home")

    def _welcome_page(self) -> Page:
        page = Page("欢迎使用外卖点餐与配送系统", "登录后查看订单与配送进度",
                    button("注册账号", lambda: self.show_page("register"), True))
        frame, layout = card("账号登录")
        account = form_field(layout, "账号", line("请输入账号"))
        password = form_field(layout, "密码", line("请输入密码", secret=True))

        def sign_in():
            def action():
                token = login(self.path, account.text().strip(), password.text())
                data = UiData(self.path, account.text().strip())
                role = data.account().get("role")
                if role not in HOME:
                    raise ValueError("账号角色无效")
                self.account_id = account.text().strip()
                self.token = token
                self.role = role
                self.data = data
            if self.perform(action, "登录成功"):
                self.show_page(HOME[self.role])

        layout.addWidget(button("登录", sign_in, True))
        layout.addWidget(button("还没有账号？注册", lambda: self.show_page("register")))
        page.add(frame)
        return page.finish()

    def _register_page(self) -> Page:
        page = Page("创建账号", "顾客立即生效；商家和配送员需等待管理员审核",
                    button("返回登录", lambda: self.show_page("home")))
        frame, layout = card("账号资料")
        account = form_field(layout, "账号", line("请输入账号"))
        phone = form_field(layout, "联系电话", line("请输入电话"))
        phone.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9-]*"), phone))
        password = form_field(layout, "密码（至少 8 位）", line("请输入密码", secret=True))
        from app.ui.widgets import select
        role = form_field(layout, "申请角色", select([
            ("顾客", "customer"), ("商家", "merchant"), ("配送员", "courier")
        ]))

        def submit():
            def action():
                register(self.path, account.text().strip(), password.text(),
                         phone.text().strip(), role.currentData())
            self.perform(action, "申请已提交，请返回登录", "home")

        layout.addWidget(button("提交注册", submit, True))
        page.add(frame)
        return page.finish()


def main() -> int:
    parser = argparse.ArgumentParser(description="外卖点餐与配送系统桌面界面")
    parser.add_argument("--db", type=Path, default=Path("lab3.sqlite3"))
    arguments = parser.parse_args()
    initialize_schema(arguments.db)
    application = QApplication(sys.argv[:1])
    window = MainWindow(arguments.db)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
