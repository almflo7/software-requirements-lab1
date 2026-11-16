"""四类角色共用的桌面控件和样式。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


STYLE = """
QWidget { font-family: 'Microsoft YaHei', 'Segoe UI'; font-size: 14px; color: #302923; }
QWidget#shell { background: #faf7f1; }
QFrame#sidebar { background: #603e31; border: none; }
QFrame#topbar, QFrame#card { background: white; border-radius: 15px; }
QFrame#topbar { border-radius: 0; }
QLabel#title { font-size: 30px; font-weight: bold; }
QLabel#subtitle, QLabel#muted { color: #81786e; }
QLabel#cardtitle { font-size: 19px; font-weight: bold; }
QLabel#metric { font-size: 27px; font-weight: bold; color: #f7662f; }
QPushButton { border: none; border-radius: 9px; padding: 10px 15px; background: #f3e9d9; font-weight: 600; }
QPushButton:hover { background: #ead9c1; }
QPushButton:disabled { color: #a7a19b; background: #f2f1ef; }
QPushButton#primary { background: #ff642f; color: white; }
QPushButton#primary:hover { background: #e95423; }
QPushButton#tableAction { background: #ff642f; color: white; border-radius: 6px; padding: 3px 8px; font-size: 12px; }
QPushButton#tableAction:hover { background: #e95423; }
QPushButton#nav { text-align: left; color: white; background: transparent; padding: 12px 16px; font-size: 15px; }
QPushButton#nav:hover, QPushButton#nav[active='true'] { background: #795241; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #d4d8da; border-radius: 8px; padding: 8px; min-height: 23px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #ff642f; }
QTableWidget { background: white; border: none; gridline-color: #f0f0f0; selection-background-color: #fff1e7; }
QHeaderView::section { background: #f5f6f7; color: #776e65; border: none; padding: 10px; font-weight: bold; }
QScrollArea { border: none; background: transparent; }
"""


def label(text: str, kind: str = "") -> QLabel:
    item = QLabel(str(text))
    if kind:
        item.setObjectName(kind)
    item.setWordWrap(True)
    return item


def button(text: str, action=None, primary: bool = False) -> QPushButton:
    item = QPushButton(text)
    if primary:
        item.setObjectName("primary")
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    if action:
        item.clicked.connect(lambda _checked=False: action())
    return item


def line(placeholder: str = "", value: str = "", secret: bool = False) -> QLineEdit:
    item = QLineEdit(value)
    item.setPlaceholderText(placeholder)
    if secret:
        item.setEchoMode(QLineEdit.EchoMode.Password)
    return item


def select(options: list[tuple[str, str]] | list[str]) -> QComboBox:
    item = QComboBox()
    for option in options:
        if isinstance(option, tuple):
            item.addItem(option[0], option[1])
        else:
            item.addItem(option, option)
    return item


def spin(value: int = 1, minimum: int = 0, maximum: int = 9999) -> QSpinBox:
    item = QSpinBox()
    item.setRange(minimum, maximum)
    item.setValue(value)
    return item


def amount(cents: int = 0) -> QDoubleSpinBox:
    item = QDoubleSpinBox()
    item.setRange(0, 100000)
    item.setDecimals(2)
    item.setValue(cents / 100)
    return item


def row(*widgets, stretch: bool = False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(12)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    return layout


def card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 22, 22, 22)
    layout.setSpacing(16)
    if title:
        layout.addWidget(label(title, "cardtitle"))
    return frame, layout


def form_field(layout: QVBoxLayout, title: str, widget: QWidget) -> QWidget:
    layout.addWidget(label(title, "muted"))
    layout.addWidget(widget)
    return widget


def table(headers: list[str], rows: list[list]) -> QTableWidget:
    widget = QTableWidget(len(rows), len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().setVisible(False)
    widget.setAlternatingRowColors(True)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setMinimumHeight(max(160, min(500, 46 * (len(rows) + 1))))
    widget.horizontalHeader().setStretchLastSection(True)
    for index in range(len(headers) - 1):
        widget.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
    for row_index, values in enumerate(rows):
        widget.setRowHeight(row_index, 43)
        for column_index, value in enumerate(values):
            widget.setItem(row_index, column_index, QTableWidgetItem(str(value)))
    return widget


class Page(QScrollArea):
    """有标题、操作按钮和纵向内容区域的滚动页面。"""

    def __init__(self, title: str, subtitle: str = "", action: QWidget | None = None):
        super().__init__()
        self.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("shell")
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(32, 30, 32, 30)
        self.content.setSpacing(20)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label(title, "title"))
        if subtitle:
            titles.addWidget(label(subtitle, "subtitle"))
        heading.addLayout(titles)
        heading.addStretch()
        if action:
            heading.addWidget(action)
        self.content.addLayout(heading)
        self.setWidget(body)

    def add(self, widget: QWidget) -> QWidget:
        self.content.addWidget(widget)
        return widget

    def add_row(self, *widgets: QWidget) -> None:
        self.content.addLayout(row(*widgets))

    def finish(self) -> "Page":
        self.content.addStretch()
        return self


def metric_card(caption: str, value: str) -> QFrame:
    frame, layout = card(caption)
    layout.addWidget(label(value, "metric"))
    return frame


def empty(text: str) -> QFrame:
    frame, layout = card()
    layout.addWidget(label(text, "muted"))
    return frame
