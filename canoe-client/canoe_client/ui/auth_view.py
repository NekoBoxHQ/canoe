"""造舟 / 登舟 —— 注册页与登录页。

账号在**服务端**（阶段3 起）。这里只做两件事：收输入、调 /api/login 与
/api/register。服务端地址是写死的（见 config.SERVER_BASE），界面上没有
任何"服务器地址"入口。

注册时先在本地做一遍和服务端同样的校验，省一次往返、也让错误提示更快。

视觉：夜色山水打底；卡片浮在中间；卡片下方**特意留出一条山水带**，
让远山、水波和那叶小舟露出来，而不是被卡片整个盖住。
输入框内嵌线性图标，密码框右侧有个眼睛可以切换明文。
"""
from __future__ import annotations

import re

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

from ..api import api
from ..config import config
from ..worker import Worker
from . import artwork as A
from .controls import CheckBox
from .window_base import WINDOW_H, WINDOW_W, FramelessWindow

#: 与服务端 RegisterRequest 保持一致
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
PASSWORD_MIN = 8


def validate_credentials(username: str, password: str, confirm: str = "") -> str:
    """本地先校验一遍。返回错误信息，空串表示通过。"""
    if not USERNAME_RE.match(username):
        return "用户名要 3-32 位字母、数字、下划线或短横线"
    if len(password) < PASSWORD_MIN:
        return f"密码至少 {PASSWORD_MIN} 位"
    if confirm and password != confirm:
        return "两次输入的密码不一致"
    return ""

# 窗口宽高见 window_base：跟主界面**一样大**（那里有说明）
PAD = 22                 # 正文左右留白
SCENE_BAND = 150         # 卡片下方留给山水的高度
FIELD_H = 40
BTN_H = 44
BTN_H2 = 40


class AuthView(FramelessWindow):
    """登录成功后发 logged_in(username) 信号。"""

    logged_in = Signal(str)

    def __init__(self) -> None:
        super().__init__(WINDOW_W, WINDOW_H)

        #: 登录成功时服务端告诉我们的节点显示名（主界面要用）
        self.last_node_name = ""

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_login())
        self.stack.addWidget(self._build_register())

        # 上次勾了「记住账号密码」的话，这里把用户名密码填回去
        self.restore_remembered()

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
        都落在特意留出来的那条带子里，不会整条被卡片盖住。

        有 assets/ocean_bg.png 的话这条分支就不走了（两个页面同一张底图）。
        """
        if A.paint_background_image(painter, width, height):
            return
        A.paint_night(painter, width, height, horizon=0.885, mountain=1.0)

    def _fit(self) -> None:
        """窗口尺寸**固定**，跟主界面一样大（见 window_base 里的 WINDOW_W/H）。

        以前是"窗口跟着当前页走"，登录页比注册页矮一截 —— 换个页面窗口就跳
        一下，像换了个程序（用户要求统一）。现在窗口不动，只让里面那摞页面
        按当前页的高度显示，多出来的高度归底下的山水。
        """
        page = self.stack.currentWidget()
        if page is not None:
            self.stack.setFixedHeight(page.sizeHint().height())
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setFixedSize(WINDOW_W, WINDOW_H)

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

        self.remember_box = CheckBox(Text.LABEL_REMEMBER)
        self.remember_box.setObjectName("Remember")
        self.remember_box.setToolTip(Text.HINT_REMEMBER)
        self.remember_box.toggled.connect(self._sync_remembered)
        lay.addSpacing(10)
        lay.addWidget(self.remember_box)

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
    def restore_remembered(self) -> None:
        """按本地记着的凭据回填登录框。没有记过就什么都不做。

        退出登录（离舟）之后也会调它 —— 记着的话，回到登录页时账号密码
        还在，不用再敲一遍。
        """
        username, password = config.remembered_credentials()
        if not username:
            # 只在状态真的不一样时才动它 —— setChecked 会触发 toggled，
            # 而 toggled 又回去写一次配置文件。没记过的人每次开客户端
            # 都白写一遍盘，没必要。
            if self.remember_box.isChecked():
                self.remember_box.setChecked(False)
            return
        self.login_user.setText(username)
        self.login_pass.setText(password)
        self.remember_box.setChecked(True)

    def _sync_remembered(self) -> None:
        """勾选框被点掉的那一刻就把本地那份清掉。

        只在登录成功时才处理的话，"取消勾选"要等到下次登录成功才生效 ——
        用户点了取消、关掉程序、密码还在盘上，跟他的预期不符。
        """
        if not self.remember_box.isChecked():
            config.forget_credentials()

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
        if not username or not password:
            self.login_err.setText("用户名和密码都要填")
            return

        self._set_busy(self.login_btn, Text.BTN_LOGIN, True)

        def on_ok(result) -> None:
            self._set_busy(self.login_btn, Text.BTN_LOGIN, False)
            # 勾了就记住，没勾就把上次记的清掉 —— 取消勾选也算一种明确的
            # "别记了"，不能只在新勾上时写、不勾时不管。
            if self.remember_box.isChecked():
                config.remember_credentials(username, password)
            else:
                config.forget_credentials()
            self.login_pass.clear()
            # node_name 在登录时就回来了 —— 主界面的「节点名称」要在
            # 点启航之前就能显示，不能等启航才知道连哪个。
            self.last_node_name = result.user.node_name or ""
            self.logged_in.emit(username)

        def on_err(code: str, message: str) -> None:
            self._set_busy(self.login_btn, Text.BTN_LOGIN, False)
            self.login_err.setText(message)

        Worker(api.login, username, password).run_with(on_ok, on_err)

    def _do_register(self) -> None:
        username = self.reg_user.text().strip()
        password = self.reg_pass.text()
        confirm = self.reg_pass2.text()

        self.reg_err.setText("")
        problem = validate_credentials(username, password, confirm)
        if problem:
            self.reg_err.setText(problem)
            return

        self._set_busy(self.reg_btn, Text.BTN_REGISTER, True)

        def on_ok(_result) -> None:
            # 造舟成功后直接登舟，少一步
            self.reg_user.clear()
            self.reg_pass.clear()
            self.reg_pass2.clear()
            self._auto_login(username, password)

        def on_err(code: str, message: str) -> None:
            self._set_busy(self.reg_btn, Text.BTN_REGISTER, False)
            self.reg_err.setText(message)

        Worker(api.register, username, password).run_with(on_ok, on_err)

    def _auto_login(self, username: str, password: str) -> None:
        """注册成功后自动登录（服务端注册接口不返回令牌）。"""

        def on_ok(result) -> None:
            self._set_busy(self.reg_btn, Text.BTN_REGISTER, False)
            self.last_node_name = result.user.node_name or ""
            self.logged_in.emit(username)

        def on_err(code: str, message: str) -> None:
            self._set_busy(self.reg_btn, Text.BTN_REGISTER, False)
            # 注册成功了但自动登录失败：把人送回登录页，别让他卡在造舟页
            self._switch(0)
            self.login_user.setText(username)
            self.login_err.setText(f"账号已创建，请手动登录（{message}）")

        Worker(api.login, username, password).run_with(on_ok, on_err)
