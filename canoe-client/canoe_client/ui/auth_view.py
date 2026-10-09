"""造舟 / 登舟 —— 注册页与登录页（美化稿）。

阶段1 的注册/登录走本地（localauth.py）；阶段3 换成服务端调用时，
只需把 _do_login / _do_register 里的两个函数换成 api 调用，界面不用动。

视觉：夜色山水打底；卡片浮在中间；卡片下方**特意留出一条山水带**，
让远山、水波和那叶小舟露出来，而不是被卡片整个盖住。
输入框内嵌线性图标，密码框右侧有个眼睛可以切换明文。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from canoe_core import BRAND_CN, SLOGAN_CN, SLOGAN_EN, Palette as P, Text

from .. import localauth
from ..worker import Worker
from . import artwork as A
from .window_base import FramelessWindow

WINDOW_W = 380
PAD = 22                 # 正文左右留白
SCENE_BAND = 150         # 卡片下方留给山水的高度
FIELD_H = 40
BTN_H = 44
BTN_H2 = 40


class AuthView(FramelessWindow):
    """登录成功后发 logged_in(username) 信号。"""

    logged_in = Signal(str)

    def __init__(self) -> None:
        super().__init__(WINDOW_W, 560)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_login())
        self.stack.addWidget(self._build_register())

        lay = self.body_layout
        lay.setContentsMargins(PAD, 8, PAD, 0)
        lay.setSpacing(0)

        logo = QLabel()
        logo.setPixmap(A.sailboat_logo(56))
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)

        lay.addSpacing(3)

        brand = QLabel(BRAND_CN)
        brand.setObjectName("Brand")
        brand.setAlignment(Qt.AlignCenter)
        lay.addWidget(brand)

        slogan = QLabel(SLOGAN_EN)
        slogan.setObjectName("Slogan")
        slogan.setAlignment(Qt.AlignCenter)
        lay.addWidget(slogan)

        lay.addSpacing(16)
        lay.addWidget(self.stack)
        lay.addSpacing(SCENE_BAND)

        self.setToolTip(SLOGAN_CN)
        self._fit()

    # ------------------------------------------------------------------
    def paint_background(self, painter: QPainter, width: float, height: float) -> None:
        """夜色山水。地平线压到卡片下沿以下 —— 这样远山、月亮、水波和小舟
        都落在特意留出来的那条带子里，不会整条被卡片盖住。"""
        A.paint_night(painter, width, height, horizon=0.885, mountain=1.0)

    def _fit(self) -> None:
        """窗口高度跟着**当前这一页**走，登录页比注册页矮一截。

        量之前必须先松开 min/max —— 上一次 setFixedSize 会把尺寸钉死，
        sizeHint() 被钳住，换页时高度就不会变了。
        """
        page = self.stack.currentWidget()
        if page is not None:
            self.stack.setFixedHeight(page.sizeHint().height())
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setFixedSize(WINDOW_W, self.sizeHint().height())

    # ------------------------------------------------------------------
    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 17, 18, 18)
        lay.setSpacing(0)
        return card, lay

    def _field(self, placeholder: str, icon_name: str) -> QLineEdit:
        """带前置图标的输入框。"""
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumHeight(FIELD_H)
        edit.addAction(QIcon(A.icon(icon_name, 18, P.TEXT_DIM)), QLineEdit.LeadingPosition)
        return edit

    def _password_field(self, placeholder: str) -> QLineEdit:
        """密码框：前置锁图标 + 尾部眼睛（切换明文）。"""
        edit = self._field(placeholder, "lock")
        edit.setEchoMode(QLineEdit.Password)

        action = edit.addAction(QIcon(A.icon("eye", 18, P.TEXT_DIM)), QLineEdit.TrailingPosition)
        action.setToolTip("显示密码")

        def toggle() -> None:
            shown = edit.echoMode() == QLineEdit.Normal
            edit.setEchoMode(QLineEdit.Password if shown else QLineEdit.Normal)
            action.setIcon(QIcon(A.icon("eye" if shown else "eye-off", 18, P.TEXT_DIM)))
            action.setToolTip("显示密码" if shown else "隐藏密码")

        action.triggered.connect(toggle)
        return edit

    def _switch_row(self, hint_text: str, link_text: str, slot) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        hint = QLabel(hint_text)
        hint.setObjectName("Hint")
        row.addWidget(hint)
        link = QPushButton(link_text)
        link.setObjectName("Link")
        link.setCursor(Qt.PointingHandCursor)
        link.setIcon(QIcon(A.icon("arrow-right", 14, P.CYAN)))
        link.setLayoutDirection(Qt.RightToLeft)
        link.clicked.connect(slot)
        row.addWidget(link)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------------
    def _build_login(self) -> QWidget:
        page = QWidget()
        page.setAttribute(Qt.WA_TranslucentBackground, True)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card, lay = self._card()

        title = QLabel(Text.TITLE_LOGIN)
        title.setObjectName("Title")
        lay.addWidget(title)

        sub = QLabel("报上名号，登陆渡江")
        sub.setObjectName("Subtitle")
        lay.addSpacing(3)
        lay.addWidget(sub)
        lay.addSpacing(14)

        self.login_user = self._field(Text.PLACEHOLDER_USERNAME, "user")
        lay.addWidget(self.login_user)
        lay.addSpacing(9)

        self.login_pass = self._password_field(Text.PLACEHOLDER_PASSWORD)
        self.login_pass.returnPressed.connect(self._do_login)
        lay.addWidget(self.login_pass)

        self.login_err = QLabel("")
        self.login_err.setObjectName("Error")
        self.login_err.setWordWrap(True)
        self.login_err.setMinimumHeight(22)
        lay.addSpacing(5)
        lay.addWidget(self.login_err)
        lay.addSpacing(6)

        self.login_btn = QPushButton(Text.BTN_LOGIN)
        self.login_btn.setObjectName("Primary")
        self.login_btn.setMinimumHeight(BTN_H)
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.setIcon(QIcon(A.icon("arrow-right", 18, "#FFFFFF")))
        self.login_btn.clicked.connect(self._do_login)
        lay.addWidget(self.login_btn)
        lay.addSpacing(9)

        # 阶段1 测试便利：不想注册就直接进
        self.guest_btn = QPushButton(Text.BTN_GUEST)
        self.guest_btn.setObjectName("Outline")
        self.guest_btn.setMinimumHeight(BTN_H2)
        self.guest_btn.setCursor(Qt.PointingHandCursor)
        self.guest_btn.setIcon(QIcon(A.icon("user-plus", 18, P.CYAN)))
        self.guest_btn.setToolTip("跳过注册，用「访客」身份直接进主界面")
        self.guest_btn.clicked.connect(self._do_guest)
        lay.addWidget(self.guest_btn)
        lay.addSpacing(12)

        lay.addLayout(self._switch_row(Text.HINT_NO_ACCOUNT, Text.LINK_TO_REGISTER,
                                       lambda: self._switch(1)))

        outer.addWidget(card)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _build_register(self) -> QWidget:
        page = QWidget()
        page.setAttribute(Qt.WA_TranslucentBackground, True)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card, lay = self._card()

        title = QLabel(Text.TITLE_REGISTER)
        title.setObjectName("Title")
        lay.addWidget(title)

        sub = QLabel(Text.SUBTITLE_REGISTER)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        lay.addSpacing(3)
        lay.addWidget(sub)
        lay.addSpacing(14)

        self.reg_user = self._field(Text.PLACEHOLDER_USERNAME, "user")
        lay.addWidget(self.reg_user)
        lay.addSpacing(9)

        self.reg_pass = self._password_field(Text.PLACEHOLDER_PASSWORD)
        lay.addWidget(self.reg_pass)
        lay.addSpacing(9)

        self.reg_pass2 = self._password_field(Text.PLACEHOLDER_PASSWORD2)
        self.reg_pass2.returnPressed.connect(self._do_register)
        lay.addWidget(self.reg_pass2)

        self.reg_err = QLabel("")
        self.reg_err.setObjectName("Error")
        self.reg_err.setWordWrap(True)
        self.reg_err.setMinimumHeight(22)
        lay.addSpacing(5)
        lay.addWidget(self.reg_err)
        lay.addSpacing(6)

        self.reg_btn = QPushButton(Text.BTN_REGISTER)
        self.reg_btn.setObjectName("Primary")
        self.reg_btn.setMinimumHeight(BTN_H)
        self.reg_btn.setCursor(Qt.PointingHandCursor)
        self.reg_btn.setIcon(QIcon(A.icon("arrow-right", 18, "#FFFFFF")))
        self.reg_btn.clicked.connect(self._do_register)
        lay.addWidget(self.reg_btn)
        lay.addSpacing(12)

        lay.addLayout(self._switch_row(Text.HINT_HAS_ACCOUNT, Text.LINK_TO_LOGIN,
                                       lambda: self._switch(0)))

        outer.addWidget(card)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.login_err.setText("")
        self.reg_err.setText("")
        self._fit()

    def _set_busy(self, btn: QPushButton, idle: str, busy: bool) -> None:
        btn.setEnabled(not busy)
        btn.setText(Text.progress(idle.replace(" ", "")) if busy else idle)

    # ------------------------------------------------------------------
    def _do_login(self) -> None:
        username = self.login_user.text().strip()
        password = self.login_pass.text()

        self.login_err.setText("")
        self._set_busy(self.login_btn, Text.BTN_LOGIN, True)

        def on_ok(account) -> None:
            self._set_busy(self.login_btn, Text.BTN_LOGIN, False)
            self.login_pass.clear()
            self.logged_in.emit(account.username)

        def on_err(code: str, message: str) -> None:
            self._set_busy(self.login_btn, Text.BTN_LOGIN, False)
            self.login_err.setText(message)

        Worker(localauth.login, username, password).run_with(on_ok, on_err)

    def _do_guest(self) -> None:
        """跳过注册，直接用访客身份进主界面。阶段1 测试便利。"""
        self.login_err.setText("")
        self.guest_btn.setEnabled(False)

        def on_ok(account) -> None:
            self.guest_btn.setEnabled(True)
            self.logged_in.emit(account.username)

        def on_err(code: str, message: str) -> None:
            self.guest_btn.setEnabled(True)
            self.login_err.setText(message)

        Worker(localauth.login_as_guest).run_with(on_ok, on_err)

    def _do_register(self) -> None:
        username = self.reg_user.text().strip()
        password = self.reg_pass.text()
        confirm = self.reg_pass2.text()

        self.reg_err.setText("")
        try:
            localauth.validate(username, password, confirm)
        except localauth.LocalAuthError as exc:
            self.reg_err.setText(exc.message)
            return

        self._set_busy(self.reg_btn, Text.BTN_REGISTER, True)

        def on_ok(_account) -> None:
            # 造舟成功后直接登舟，少一步
            self._set_busy(self.reg_btn, Text.BTN_REGISTER, False)
            self.reg_user.clear()
            self.reg_pass.clear()
            self.reg_pass2.clear()
            self.logged_in.emit(username)

        def on_err(code: str, message: str) -> None:
            self._set_busy(self.reg_btn, Text.BTN_REGISTER, False)
            self.reg_err.setText(message)

        Worker(localauth.register, username, password).run_with(on_ok, on_err)
