"""主界面 —— 启航 / 靠岸 + 工具按钮 + 输出日志。

需求：登录后主界面只显示三个东西 —— 节点名称、启航、靠岸。
这里保留这三样作为主视觉，另外多了：

  · 一行状态（渡江中… / 已启航 / 已靠岸 / 风浪太大，请重试）
  · 一组可选设置（两排：分流/全局，系统代理/TUN）
  · 三个工具按钮：更新 / TCping / URL测试
  · 输出日志面板（内核输出 + 程序事件）
  · 底部账号名 + 离舟

**界面上永远不显示节点的地址、端口、协议、密码，也没有任何导出入口。**
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from canoe_core import BRAND_CN, VERSION, Text

from .. import sysproxy
from ..config import config
from ..kernel import kernel
from ..logbus import TAG_ERROR, TAG_KERNEL, TAG_SYSTEM, TAG_TEST, bus
from ..nettest import DEFAULT_URL, tcping, url_test
from ..options import (
    LABEL_SYSTEM_PROXY,
    LABEL_TUN,
    PROFILE_GLOBAL,
    PROFILE_LABELS,
    PROFILE_SPLIT,
    RunOptions,
)
from ..session import STATE_DOCKED, STATE_SAILED, STATE_STORM, session
from ..testnodes import build_proxy_outbound, node_display_name, node_endpoint
from ..tun import check_tun_ready, relaunch_as_admin
from ..update import check as check_update
from ..worker import Worker

LOG_POLL_MS = 300

# 日志里不同来源用不同颜色
TAG_COLORS = {
    TAG_SYSTEM: "#7A8DA0",
    TAG_KERNEL: "#5F7F9A",
    TAG_TEST: "#2A7F8F",
    TAG_ERROR: "#C2603C",
}


class MainView(QWidget):
    logged_out = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(BRAND_CN)
        self.setFixedSize(440, 720)

        self._opts = RunOptions.from_dict(config["options"])
        self._log_seq = 0

        self._build()
        self._load_options_into_ui()
        self.refresh()
        self._set_state(STATE_DOCKED)

        # 日志面板定时拉增量
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_log)
        self._log_timer.start(LOG_POLL_MS)

        bus.system(Text.LOG_READY)
        self._drain_log()

    # ==================================================================
    # 界面
    # ==================================================================
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 14)
        root.setSpacing(0)

        brand = QLabel(BRAND_CN)
        brand.setObjectName("Slogan")
        brand.setAlignment(Qt.AlignCenter)
        root.addWidget(brand)
        root.addSpacing(10)

        # --- 1) 节点名称 ---
        self.node_label = QLabel("—")
        self.node_label.setObjectName("NodeName")
        self.node_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.node_label)

        self.status_label = QLabel(Text.ST_DISCONNECTED)
        self.status_label.setObjectName("Status")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addSpacing(4)
        root.addWidget(self.status_label)

        root.addSpacing(14)

        # --- 2) 启航  3) 靠岸 ---
        buttons = QHBoxLayout()
        buttons.setSpacing(10)

        self.launch_btn = QPushButton(Text.BTN_LAUNCH)
        self.launch_btn.setObjectName("Launch")
        self.launch_btn.setCursor(Qt.PointingHandCursor)
        self.launch_btn.clicked.connect(self._launch)

        self.dock_btn = QPushButton(Text.BTN_DOCK)
        self.dock_btn.setObjectName("Dock")
        self.dock_btn.setCursor(Qt.PointingHandCursor)
        self.dock_btn.clicked.connect(self._dock)

        buttons.addWidget(self.launch_btn, 1)
        buttons.addWidget(self.dock_btn, 1)
        root.addLayout(buttons)

        self.error_label = QLabel("")
        self.error_label.setObjectName("Error")
        self.error_label.setWordWrap(True)
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setMinimumHeight(30)
        root.addSpacing(4)
        root.addWidget(self.error_label)

        root.addSpacing(4)

        # --- 可选设置 ---
        root.addWidget(self._options_card())
        root.addSpacing(10)

        # --- 工具按钮 ---
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.update_btn = self._tool_button(Text.BTN_UPDATE, "ToolUpdate", self._do_update)
        self.tcping_btn = self._tool_button(Text.BTN_TCPING, "ToolPing", self._do_tcping)
        self.urltest_btn = self._tool_button(Text.BTN_URLTEST, "ToolUrl", self._do_urltest)
        tools.addWidget(self.update_btn)
        tools.addWidget(self.tcping_btn)
        tools.addWidget(self.urltest_btn)
        root.addLayout(tools)

        root.addSpacing(10)

        # --- 输出日志 ---
        root.addWidget(self._log_card(), 1)

        root.addSpacing(8)

        # --- 账号 ---
        bottom = QHBoxLayout()
        self.account_label = QLabel("")
        self.account_label.setObjectName("Hint")
        bottom.addWidget(self.account_label)
        bottom.addStretch(1)
        logout_btn = QPushButton(Text.BTN_LOGOUT)
        logout_btn.setObjectName("Ghost")
        logout_btn.setCursor(Qt.PointingHandCursor)
        logout_btn.clicked.connect(self._logout)
        bottom.addWidget(logout_btn)
        root.addLayout(bottom)

    def _options_card(self) -> QFrame:
        """两排，整体居中，不带行标签。

            第一排（二选一）  ○ 分流        ○ 全局
            第二排（可并存）  ☑ 系统代理    ☐ TUN 模式

        两排的互斥性不同：分流/全局 是同一件事的两种模式，必须二选一；
        系统代理 / TUN 是两种可以叠加的接管方式，用独立复选框。
        """
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(10)

        self._group_profile = QButtonGroup(self)
        self.rb_split = QRadioButton(PROFILE_LABELS[PROFILE_SPLIT])
        self.rb_global = QRadioButton(PROFILE_LABELS[PROFILE_GLOBAL])
        self._group_profile.addButton(self.rb_split)
        self._group_profile.addButton(self.rb_global)
        self.rb_split.toggled.connect(self._on_options_changed)
        self.rb_split.setToolTip("绕过局域网和大陆，其余流量走代理")
        self.rb_global.setToolTip("所有流量都走代理")
        lay.addLayout(self._centered_row([self.rb_split, self.rb_global]))

        self.cb_system = QCheckBox(LABEL_SYSTEM_PROXY)
        self.cb_tun = QCheckBox(LABEL_TUN)
        self.cb_system.toggled.connect(self._on_options_changed)
        self.cb_tun.toggled.connect(self._on_options_changed)
        self.cb_system.setToolTip("把 Windows 系统代理指向本机端口，不需要管理员权限")
        self.cb_tun.setToolTip("接管全部流量（IPv4 + IPv6），需要管理员权限和 wintun.dll")
        lay.addLayout(self._centered_row([self.cb_system, self.cb_tun]))

        return card

    @staticmethod
    def _centered_row(widgets: list) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch(1)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def _tool_button(self, text: str, object_name: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(object_name)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(56)
        btn.clicked.connect(slot)
        return btn

    def _log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 9, 12, 11)
        lay.setSpacing(7)

        title = QLabel(Text.LABEL_LOG)
        title.setObjectName("LogTitle")
        lay.addWidget(title)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(800)
        # 长行折行、不要横向滚动条 —— 否则底部会多出一条灰条，很碍眼
        self.log_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.log_view, 1)
        return card

    # ==================================================================
    # 日志
    # ==================================================================
    def _drain_log(self) -> None:
        """把日志总线上新增的行追加到面板。"""
        new = bus.since(self._log_seq)
        if not new:
            return
        self._log_seq = new[-1].seq + 1

        for line in new:
            color = TAG_COLORS.get(line.tag, TAG_SYSTEM)
            self.log_view.appendHtml(
                f'<span style="color:#4A5A6B">{line.time_text}</span> '
                f'<span style="color:{color}">[{line.tag}]</span> '
                f'<span style="color:#C9CDD3">{_escape(line.message)}</span>'
            )
        self.log_view.moveCursor(QTextCursor.End)

    # ==================================================================
    # 选项读写
    # ==================================================================
    def _load_options_into_ui(self) -> None:
        self._loading = True
        self.rb_global.setChecked(self._opts.profile == PROFILE_GLOBAL)
        self.rb_split.setChecked(self._opts.profile != PROFILE_GLOBAL)
        self.cb_system.setChecked(self._opts.use_system_proxy)
        self.cb_tun.setChecked(self._opts.use_tun)
        self._loading = False
        self._update_tun_tooltip()

    def _on_options_changed(self) -> None:
        if getattr(self, "_loading", False):
            return
        if session.sailing:
            self._dock()          # 运行中改选项：先靠岸，避免半途换配置

        self._opts.profile = PROFILE_GLOBAL if self.rb_global.isChecked() else PROFILE_SPLIT
        self._opts.use_system_proxy = self.cb_system.isChecked()
        self._opts.use_tun = self.cb_tun.isChecked()
        config.set_and_save(options=self._opts.to_dict())
        self._update_tun_tooltip()

    def _update_tun_tooltip(self) -> None:
        if not self._opts.use_tun:
            self.cb_tun.setToolTip("接管全部流量（IPv4 + IPv6），需要管理员权限和 wintun.dll")
            return
        exe = config.find_singbox()
        ready, reason = check_tun_ready(exe.parent if exe else None)
        self.cb_tun.setToolTip(
            "接管全部流量（IPv4 + IPv6）" if ready else f"暂时不可用：{reason}"
        )

    # ==================================================================
    # 状态
    # ==================================================================
    def refresh(self) -> None:
        self.node_label.setText(session.node_name or "—")
        self.account_label.setText(session.username or "")
        self._apply_buttons()

    def _set_state(self, state: str, error: str = "") -> None:
        session.state = state
        session.error = error if state == STATE_STORM else ""
        self.status_label.setText(Text.ST_ERROR if state == STATE_STORM else session.status_text)
        self.error_label.setText(session.error)
        self._apply_buttons()

    def _apply_buttons(self) -> None:
        sailing = session.sailing
        self.launch_btn.setEnabled(not sailing)
        self.dock_btn.setEnabled(sailing)
        self.launch_btn.setText(Text.BTN_LAUNCH)
        self.dock_btn.setText(Text.BTN_DOCK)

    # ==================================================================
    # 启航 / 靠岸
    # ==================================================================
    def _launch(self) -> None:
        self.error_label.setText("")
        self.launch_btn.setEnabled(False)
        self.launch_btn.setText(Text.BTN_LAUNCH_BUSY)
        self.status_label.setText(Text.ST_CONNECTING)
        bus.system("正在启航…")

        if self._opts.use_tun:
            exe = config.find_singbox()
            ready, reason = check_tun_ready(exe.parent if exe else None)
            if not ready:
                bus.error(reason)
                self._apply_buttons()
                self._handle_tun_blocked(reason)
                return

        def on_ok(_none) -> None:
            self.refresh()
            self._set_state(STATE_SAILED)
            bus.system("已启航")

            if self._opts.use_system_proxy:
                try:
                    sysproxy.set_proxy("127.0.0.1", int(self._opts.mixed_port))
                    bus.system(f"系统代理已指向 127.0.0.1:{self._opts.mixed_port}")
                except OSError as exc:
                    kernel.stop()
                    self._set_state(STATE_STORM, f"设置系统代理失败：{exc}")
                    bus.error(f"设置系统代理失败：{exc}")

        def on_err(code: str, message: str) -> None:
            self._set_state(STATE_STORM, message)
            bus.error(message)

        Worker(self._do_start_kernel).run_with(on_ok, on_err)

    def _do_start_kernel(self) -> None:
        kernel.start(build_proxy_outbound(), self._opts)

    def _handle_tun_blocked(self, reason: str) -> None:
        if "管理员" not in reason:
            self._set_state(STATE_STORM, reason)
            return
        answer = QMessageBox.question(
            self,
            "需要管理员权限",
            f"{reason}\n\n是否现在以管理员身份重启？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            if relaunch_as_admin():
                Qt.callLater(self.close)
            else:
                self._set_state(STATE_STORM, "提权失败，请手动以管理员身份运行")
        else:
            self._set_state(STATE_DOCKED)

    def _dock(self) -> None:
        self.dock_btn.setEnabled(False)
        self.dock_btn.setText(Text.BTN_DOCK_BUSY)
        bus.system("正在靠岸…")

        try:
            kernel.stop()
        except Exception as exc:  # noqa: BLE001
            self.error_label.setText(f"关闭内核时出错：{exc}")
            bus.error(f"关闭内核时出错：{exc}")

        if self._opts.use_system_proxy:
            try:
                sysproxy.clear_proxy()
                bus.system("系统代理已还原")
            except OSError as exc:
                self.error_label.setText(f"还原系统代理失败：{exc}")
                bus.error(f"还原系统代理失败：{exc}")

        self._set_state(STATE_DOCKED)
        bus.system("已靠岸")

    # ==================================================================
    # 工具按钮
    # ==================================================================
    def _do_update(self) -> None:
        self.update_btn.setEnabled(False)
        bus.test("检查客户端更新…")
        url = str(config["update_url"])

        def on_ok(info) -> None:
            self.update_btn.setEnabled(True)
            if info.is_newer:
                bus.test(f"发现新版本 {info.latest}（当前 {info.current}）")
                if info.notes:
                    bus.test(f"更新说明：{info.notes}")
                detail = f"当前版本：{info.current}\n最新版本：{info.latest}"
                if info.notes:
                    detail += f"\n\n更新说明：\n{info.notes}"
                if info.url:
                    detail += f"\n\n下载地址：\n{info.url}"
                QMessageBox.information(self, "有新版本", detail)
            else:
                bus.test(f"已是最新版本（{info.current}）")

        def on_err(code: str, message: str) -> None:
            self.update_btn.setEnabled(True)
            bus.error(f"更新检查失败：{message}")

        Worker(check_update, url, VERSION).run_with(on_ok, on_err)

    def _do_tcping(self) -> None:
        self.tcping_btn.setEnabled(False)
        host, port = node_endpoint()
        bus.test(f"TCping {host}:{port} …")

        def on_ok(result) -> None:
            self.tcping_btn.setEnabled(True)
            (bus.test if result.ok else bus.error)(result.summary())

        def on_err(code: str, message: str) -> None:
            self.tcping_btn.setEnabled(True)
            bus.error(f"TCping 失败：{message}")

        Worker(tcping, host, port).run_with(on_ok, on_err)

    def _do_urltest(self) -> None:
        if not session.sailing:
            bus.error("URL 测试需要先启航（它要走本地代理）")
            return

        self.urltest_btn.setEnabled(False)
        bus.test(f"URL 测试 {DEFAULT_URL} …")

        def on_ok(result) -> None:
            self.urltest_btn.setEnabled(True)
            (bus.test if result.ok else bus.error)(result.summary())

        def on_err(code: str, message: str) -> None:
            self.urltest_btn.setEnabled(True)
            bus.error(f"URL 测试失败：{message}")

        Worker(url_test, int(self._opts.mixed_port)).run_with(on_ok, on_err)

    # ==================================================================
    # 离舟 / 关窗
    # ==================================================================
    def _logout(self) -> None:
        answer = QMessageBox.question(
            self, Text.BTN_LOGOUT, "确定要离舟吗？", QMessageBox.Yes | QMessageBox.No
        )
        if answer == QMessageBox.Yes:
            self._do_logout()

    def _do_logout(self) -> None:
        if session.sailing:
            self._dock()
        session.logout()
        self.logged_out.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关窗必须停内核并还原系统代理，否则用户会断网。"""
        self._log_timer.stop()
        if session.sailing:
            self._dock()
        super().closeEvent(event)

    def start_with_test_node(self, username: str) -> None:
        """阶段1：节点名来自写死的测试节点。阶段3 换成服务端下发的 node_name。"""
        session.login(username, node_display_name())


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
