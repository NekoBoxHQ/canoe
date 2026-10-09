"""造舟 / 登舟 —— 注册页与登录页。

阶段1 的注册/登录走本地（localauth.py）；阶段3 换成服务端调用时，
只需把 _do_login / _do_register 里的两个函数换成 api 调用，界面不用动。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from canoe_core import BRAND_CN, SLOGAN_CN, SLOGAN_EN, Text

from .. import localauth
from ..worker import Worker


class AuthView(QWidget):
    """登录成功后发 logged_in(username) 信号。"""

    logged_in = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(BRAND_CN)
        self.setFixedSize(390, 520)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_login())
        self.stack.addWidget(self._build_register())

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 22)
        root.setSpacing(0)

        brand = QLabel(BRAND_CN)
        brand.setObjectName("Brand")
        brand.setAlignment(Qt.AlignCenter)
        root.addWidget(brand)

        slogan = QLabel(SLOGAN_EN)
        slogan.setObjectName("Slogan")
        slogan.setAlignment(Qt.AlignCenter)
        root.addSpacing(2)
        root.addWidget(slogan)

        root.addSpacing(20)
        root.addWidget(self.stack)

        self.setToolTip(SLOGAN_CN)

    # ------------------------------------------------------------------
    def _build_login(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        title = QLabel(Text.TITLE_LOGIN)
        title.setObjectName("Title")
        lay.addWidget(title)

        sub = QLabel(Text.SUBTITLE_LOGIN)
        sub.setObjectName("Subtitle")
        lay.addWidget(sub)
        lay.addSpacing(14)

        self.login_user = QLineEdit()
        self.login_user.setPlaceholderText(Text.PLACEHOLDER_USERNAME)
        lay.addWidget(self.login_user)

        self.login_pass = QLineEdit()
        self.login_pass.setPlaceholderText(Text.PLACEHOLDER_PASSWORD)
        self.login_pass.setEchoMode(QLineEdit.Password)
        self.login_pass.returnPressed.connect(self._do_login)
        lay.addWidget(self.login_pass)

        self.login_err = QLabel("")
        self.login_err.setObjectName("Error")
        self.login_err.setWordWrap(True)
        self.login_err.setMinimumHeight(32)
        lay.addWidget(self.login_err)

        self.login_btn = QPushButton(Text.BTN_LOGIN)
        self.login_btn.setMinimumHeight(42)
        self.login_btn.clicked.connect(self._do_login)
        lay.addWidget(self.login_btn)

        # 阶段1 测试便利：不想注册就直接进
        self.guest_btn = QPushButton(Text.BTN_GUEST)
        self.guest_btn.setObjectName("Guest")
        self.guest_btn.setMinimumHeight(34)
        self.guest_btn.setCursor(Qt.PointingHandCursor)
        self.guest_btn.setToolTip("跳过注册，用「访客」身份直接进主界面")
        self.guest_btn.clicked.connect(self._do_guest)
        lay.addWidget(self.guest_btn)

        lay.addStretch(1)

        bottom = QHBoxLayout()
        hint = QLabel(Text.HINT_NO_ACCOUNT)
        hint.setObjectName("Hint")
        bottom.addWidget(hint)
        to_reg = QPushButton(Text.LINK_TO_REGISTER)
        to_reg.setObjectName("Ghost")
        to_reg.setCursor(Qt.PointingHandCursor)
        to_reg.clicked.connect(lambda: self._switch(1))
        bottom.addWidget(to_reg)
        bottom.addStretch(1)
        lay.addLayout(bottom)
        return page

    # ------------------------------------------------------------------
    def _build_register(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        title = QLabel(Text.TITLE_REGISTER)
        title.setObjectName("Title")
        lay.addWidget(title)

        sub = QLabel(Text.SUBTITLE_REGISTER)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addSpacing(14)

        self.reg_user = QLineEdit()
        self.reg_user.setPlaceholderText(Text.PLACEHOLDER_USERNAME)
        lay.addWidget(self.reg_user)

        self.reg_pass = QLineEdit()
        self.reg_pass.setPlaceholderText(Text.PLACEHOLDER_PASSWORD)
        self.reg_pass.setEchoMode(QLineEdit.Password)
        lay.addWidget(self.reg_pass)

        self.reg_pass2 = QLineEdit()
        self.reg_pass2.setPlaceholderText(Text.PLACEHOLDER_PASSWORD2)
        self.reg_pass2.setEchoMode(QLineEdit.Password)
        self.reg_pass2.returnPressed.connect(self._do_register)
        lay.addWidget(self.reg_pass2)

        self.reg_err = QLabel("")
        self.reg_err.setObjectName("Error")
        self.reg_err.setWordWrap(True)
        self.reg_err.setMinimumHeight(32)
        lay.addWidget(self.reg_err)

        self.reg_btn = QPushButton(Text.BTN_REGISTER)
        self.reg_btn.setMinimumHeight(42)
        self.reg_btn.clicked.connect(self._do_register)
        lay.addWidget(self.reg_btn)

        lay.addStretch(1)

        bottom = QHBoxLayout()
        hint = QLabel(Text.HINT_HAS_ACCOUNT)
        hint.setObjectName("Hint")
        bottom.addWidget(hint)
        to_login = QPushButton(Text.LINK_TO_LOGIN)
        to_login.setObjectName("Ghost")
        to_login.setCursor(Qt.PointingHandCursor)
        to_login.clicked.connect(lambda: self._switch(0))
        bottom.addWidget(to_login)
        bottom.addStretch(1)
        lay.addLayout(bottom)
        return page

    # ------------------------------------------------------------------
    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.login_err.setText("")
        self.reg_err.setText("")

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
