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

import re

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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from canoe_core import BRAND_CN, VERSION, Text

from .. import sysproxy
from ..config import config
from ..kernel import kernel
from ..logbus import TAG_ERROR, TAG_RESULT, bus
from ..nettest import tcping, url_test
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

#: 主界面宽度。高度不写死 —— 用内容高度（见 MainView.__init__）。
WINDOW_WIDTH = 440

#: 结果框高度（px）：**只留一行**。大字行高 32px，加上边框/内边距取 46，
#: 正好一行，底下不再拖一大块空白。
RESULT_BOX_HEIGHT = 46

#: 结果框最多保留几条 —— 只留最新一条。
RESULT_MAX_LINES = 1

#: 结果默认绿色大字，失败用橙色
RESULT_COLOR = "#3FD07A"
TAG_COLORS = {
    TAG_RESULT: RESULT_COLOR,
    TAG_ERROR: "#E0803C",
}


def _result_line(result) -> str:
    """把测试结果压成一行，格式固定：

        TCP 延迟：38ms
        URL 耗时：344ms

    刻意不显示 target（那是 host:port，含节点域名）。
    """
    if isinstance(result, str):
        return result

    # PingResult
    if hasattr(result, "times"):
        if not result.times:
            return f"TCP 延迟：{result.error or '超时'}"
        avg = sum(result.times) / len(result.times)
        text = f"TCP 延迟：{avg:.0f}ms"
        if result.lost:
            text += f"（丢包 {result.lost}/{result.total}）"
        return text

    # UrlResult
    if hasattr(result, "elapsed_ms"):
        if not result.ok:
            return f"URL 耗时：{result.error or '失败'}"
        return f"URL 耗时：{result.elapsed_ms:.0f}ms"

    return str(result)


def _mask_secrets(text: str) -> str:
    """把可能出现的节点域名/地址遮掉。

    结果行本身不含这些，但**错误信息**可能带（比如 requests 的报错里
    会有完整 URL 和主机名）。宁可遮得狠一点，也不能让域名溜到界面上。
    """
    try:
        host, _ = node_endpoint()
    except Exception:  # noqa: BLE001
        host = ""
    if host and host in text:
        text = text.replace(host, "节点")

    # 再兜一层：任何 形如 xxx.yyy 的域名片段都打码
    return _DOMAIN_RE.sub(lambda m: m.group(1) + "＊＊＊", text)


#: 匹配 http(s)://host 或裸域名，保留前缀便于理解，主机部分打码
_DOMAIN_RE = re.compile(
    r"(https?://|\b)((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})"
)


class MainView(QWidget):
    logged_out = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(BRAND_CN)

        self._opts = RunOptions.from_dict(config["options"])
        self._result_seq = 0

        self._build()
        self._load_options_into_ui()
        self.refresh()
        self._set_state(STATE_DOCKED)

        # 窗口高度 = 内容高度。布局里**没有任何 addStretch**，
        # 所以不会有"兜底被推到底、中间空一条"的情况；
        # 反过来这里也不能写死高度 —— 比内容矮就会挤压控件。
        self.setFixedSize(WINDOW_WIDTH, self.sizeHint().height())

        # 日志面板定时拉增量
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_log)
        self._log_timer.start(LOG_POLL_MS)

        bus.system("界面就绪")
        self._drain_log()

    # ==================================================================
    # 界面
    # ==================================================================
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 14, 22, 12)
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

        root.addSpacing(12)

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
        self.error_label.setMinimumHeight(26)
        root.addSpacing(4)
        root.addWidget(self.error_label)

        root.addSpacing(4)

        # --- 可选设置 ---
        root.addWidget(self._options_card())
        root.addSpacing(8)

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

        root.addSpacing(8)

        # --- 结果框 ---
        # 固定高度，**不给 stretch**：给 stretch 它会自己膨胀去填满，
        # 结果框就变成一大块空白（实测会撑到 480px）。不拉伸、也不用
        # 尾部 addStretch(1) 收尾 —— 那样会在它和账号行之间留一条空白。
        # 窗口高度收到内容高度（见 __init__），自然就没有留白了。
        root.addWidget(self._result_card())

        root.addSpacing(6)

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

    def _result_card(self) -> QFrame:
        """结果框：只显示 更新版本号 / TCping 毫秒 / URL 毫秒。

        **只有一行高** —— 新的结果顶掉旧的，底下不留空白块。

        **绝不显示内核日志** —— 那里面带节点域名，显示出来就是泄漏。
        详见 logbus.py 顶部的说明。
        """
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 7, 12, 9)
        lay.setSpacing(5)

        title = QLabel(Text.LABEL_RESULT)
        title.setObjectName("LogTitle")
        lay.addWidget(title)

        self.result_view = QPlainTextEdit()
        self.result_view.setObjectName("ResultView")
        self.result_view.setReadOnly(True)
        # 只留最新一条 —— 一行高，新的顶掉旧的
        self.result_view.setMaximumBlockCount(RESULT_MAX_LINES)
        self.result_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.result_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 本来就没几行，不要右边的拖动条
        self.result_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 固定高度：正好一行大字。不固定的话 Qt 会按 sizePolicy 把它拉去
        # 填满剩余空间，结果框就成了一大块空白。
        self.result_view.setFixedHeight(RESULT_BOX_HEIGHT)
        self.result_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(self.result_view)
        return card

    # ==================================================================
    # 日志
    # ==================================================================
    def _drain_log(self) -> None:
        """把总线上新增的**可显示**结果追加到结果框。

        用的是 visible_since() 而不是 since() —— 内核日志会被挡在外面，
        界面拿不到它，也就不可能显示出来。
        """
        new = bus.visible_since(self._result_seq)
        if not new:
            return
        self._result_seq = new[-1].seq + 1

        for line in new:
            color = TAG_COLORS.get(line.tag, RESULT_COLOR)
            # 结果行本身不含域名；错误行可能含，再过一道遮罩
            text = _escape(_mask_secrets(line.message))
            self.result_view.appendHtml(f'<span style="color:{color}">{text}</span>')
        self.result_view.moveCursor(QTextCursor.End)

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

        # ★ 顺序不能反：**先还原系统代理，再停内核**。
        #   反过来的话，TUN 模式下 kernel.stop() 要阻塞约两秒收网卡，
        #   这两秒里注册表仍然指着 127.0.0.1:20818 而内核已经要没了 ——
        #   用户此时要是觉得卡死了强关程序，就永久断网（脏备份黑洞）。
        #   先把代理还回去，最坏情况也只是"代理没生效"，绝不会"没网"。
        if sysproxy.has_backup():
            try:
                sysproxy.clear_proxy()
                bus.system("系统代理已还原")
            except OSError as exc:
                self.error_label.setText(f"还原系统代理失败：{exc}")
                bus.error(f"还原系统代理失败：{exc}")

        try:
            kernel.stop()
        except Exception as exc:  # noqa: BLE001
            self.error_label.setText(f"关闭内核时出错：{exc}")
            bus.error(f"关闭内核时出错：{exc}")

        self._set_state(STATE_DOCKED)
        bus.system("已靠岸")

    # ==================================================================
    # 工具按钮
    # ==================================================================
    def _do_update(self) -> None:
        self.update_btn.setEnabled(False)
        url = str(config["update_url"])

        def on_ok(info) -> None:
            self.update_btn.setEnabled(True)
            if info.is_newer:
                bus.result(f"更新：{info.current} → {info.latest}")
                # 详细内容放弹窗，不塞进结果框（结果框只留一行）
                detail = f"当前版本：{info.current}\n最新版本：{info.latest}"
                if info.notes:
                    detail += f"\n\n更新说明：\n{info.notes}"
                if info.url:
                    detail += f"\n\n下载地址：\n{info.url}"
                QMessageBox.information(self, "有新版本", detail)
            else:
                bus.result(f"更新：{info.current} 最新")

        def on_err(code: str, message: str) -> None:
            self.update_btn.setEnabled(True)
            bus.error(f"更新：{message}")

        Worker(check_update, url, VERSION).run_with(on_ok, on_err)

    def _do_tcping(self) -> None:
        self.tcping_btn.setEnabled(False)
        host, port = node_endpoint()

        def on_ok(result) -> None:
            self.tcping_btn.setEnabled(True)
            (bus.result if result.ok else bus.error)(_result_line(result))

        def on_err(code: str, message: str) -> None:
            self.tcping_btn.setEnabled(True)
            bus.error(f"TCP 延迟：{message}")

        Worker(tcping, host, port).run_with(on_ok, on_err)

    def _do_urltest(self) -> None:
        if not session.sailing:
            bus.error("URL 耗时：需要先启航")
            return

        self.urltest_btn.setEnabled(False)
        pass

        def on_ok(result) -> None:
            self.urltest_btn.setEnabled(True)
            (bus.result if result.ok else bus.error)(_result_line(result))

        def on_err(code: str, message: str) -> None:
            self.urltest_btn.setEnabled(True)
            bus.error(f"URL 耗时：{message}")

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
        """关窗必须停内核并还原系统代理，否则用户会断网。

        注意也要看 `kernel.running`：刚点完启航、内核正在起的那一两秒里
        `session.sailing` 还是 False，只看它会漏掉这时候关窗的情况。
        """
        self._log_timer.stop()
        if session.sailing or kernel.running or sysproxy.has_backup():
            self._dock()
        super().closeEvent(event)

    def start_with_test_node(self, username: str) -> None:
        """阶段1：节点名来自写死的测试节点。阶段3 换成服务端下发的 node_name。"""
        session.login(username, node_display_name())


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
