"""主界面（美化稿）—— 启航 / 靠岸 + 工具按钮 + 输出结果。

需求：登录后主界面只显示三个东西 —— 节点名称、启航、靠岸。
这里保留这三样作为主视觉，另外多了：

  · 一行状态（渡江中… / 已启航 / 已靠岸 / 风浪太大，请重试）
  · 一组可选设置（两排：分流/全局，系统代理/TUN）
  · 三个工具按钮：更新 / TCping / URL测试
  · 输出结果（只显示最新一条，绝不显示内核日志）
  · 底部账号名 + 离舟
  · 底部一条夜色水面，和登录页呼应

**界面上永远不显示节点的地址、端口、协议、密码，也没有任何导出入口。**
"""
from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from canoe_core import BRAND_CN, VERSION, Palette as P, Text

from .. import sysproxy, update
from ..api import CanoeApiError, api
from ..config import config
from ..entry import EntryError, build_entry_outbound
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
from ..tun import check_tun_ready, relaunch_as_admin
from ..worker import Worker
from . import artwork as A
from .controls import CheckBox, RadioButton
from .window_base import FramelessWindow

LOG_POLL_MS = 300

WINDOW_W = 420
SIDE_PAD = 20            # 正文左右留白
SCENE_BAND = 74          # 底部留给水面的高度
TOOL_H = 66
#: 三个工具按钮等宽：(窗口宽 - 两侧留白 - 两个间隔) / 3
TOOL_W = (WINDOW_W - SIDE_PAD * 2 - 20) // 3

#: 结果默认绿色，失败用橙色
RESULT_COLOR = P.GREEN
TAG_COLORS = {TAG_RESULT: P.GREEN, TAG_ERROR: P.AMBER}

#: 把结果里可能出现的域名打码，万一某条错误信息带了域名也不会漏出去
_DOMAIN_RE = re.compile(
    r"(https?://|\b)((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})"
)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mask_secrets(text: str) -> str:
    """兜底遮罩：结果文本里出现的任何域名都打码。

    正常结果（TCP 延迟 / URL 耗时）本来不含域名；这一层是防着某条错误
    信息里带了入口域名或 IP —— 界面上不出现节点/入口信息是硬要求。
    """
    return _DOMAIN_RE.sub(lambda m: m.group(1) + "＊＊＊", text)


def _result_line(result) -> str:
    """把测试结果压成一行。"""
    if hasattr(result, "times"):        # PingResult
        if not result.times:
            return f"TCP 延迟：{result.error or '超时'}"
        avg = sum(result.times) / len(result.times)
        text = f"TCP 延迟：{avg:.0f}ms"
        if result.lost:
            text += f"（丢包 {result.lost}/{result.total}）"
        return text
    if hasattr(result, "elapsed_ms"):   # UrlResult
        if not result.ok:
            return f"URL 耗时：{result.error or '失败'}"
        return f"URL 耗时：{result.elapsed_ms:.0f}ms"
    return str(result)


class MainView(FramelessWindow):
    logged_out = Signal()

    def __init__(self) -> None:
        super().__init__(WINDOW_W, 560)

        self._opts = RunOptions.from_dict(config["options"])
        self._result_seq = 0

        # --- 与服务端会话相关的状态 ---
        #: 本次会话 id。启航时服务端发下来的，靠岸时要用它结束会话。
        self._session_id = ""
        #: 服务端下发的配置版本 / 订阅指纹，用来判断"配置变了没有"
        self._config_version = 0
        self._revision = ""
        self._heartbeat_seconds = 30

        self._build()
        self._load_options_into_ui()
        self.refresh()
        self._set_state(STATE_DOCKED)

        # 布局定型后再把窗口收到内容高度 —— 底下不留空白带
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setFixedSize(WINDOW_W, self.sizeHint().height())

        # 结果框定时拉增量
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_log)
        self._log_timer.start(LOG_POLL_MS)

        # 心跳：航行期间每 N 秒报一次，顺便让服务端把会话续期。
        # 被管理员封禁/踢下线时，服务端会在心跳响应里带 revoked —— 那时立即靠岸。
        self._hb_timer = QTimer(self)
        self._hb_timer.timeout.connect(self._on_heartbeat)

        bus.system("界面就绪")
        self._drain_log()

    # ==================================================================
    # 背景
    # ==================================================================
    def paint_background(self, painter: QPainter, width: float, height: float) -> None:
        """夜色水面：地平线压到最底下那条留白里（约 94.5% 处），

        远山最高也就冒到账号行下沿附近，不会顶到内容上；
        主界面不放月亮和小舟 —— 地方太窄，放上去只会和账号行打架。
        """
        A.paint_night(painter, width, height, horizon=0.945, mountain=0.62,
                      boat=False, moon=False)

    # ==================================================================
    # 界面
    # ==================================================================
    def _rule(self) -> QFrame:
        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFixedSize(46, 1)
        return rule

    def _kicker(self) -> QWidget:
        """「轻舟」小标题，两侧各一条横线。"""
        row = QWidget()
        row.setAttribute(Qt.WA_TranslucentBackground, True)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addStretch(1)
        lay.addWidget(self._rule())
        label = QLabel(BRAND_CN)
        label.setObjectName("Kicker")
        lay.addWidget(label)
        lay.addWidget(self._rule())
        lay.addStretch(1)
        return row

    @staticmethod
    def _centered_row(widgets: list) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(26)
        row.addStretch(1)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def _build(self) -> None:
        root = self.body_layout
        root.setContentsMargins(SIDE_PAD, 10, SIDE_PAD, 0)
        root.setSpacing(0)

        root.addWidget(self._kicker())
        root.addSpacing(10)

        # --- 1) 节点名称 ---
        self.node_label = QLabel("—")
        self.node_label.setObjectName("NodeName")
        self.node_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.node_label)

        self.status_label = QLabel(Text.ST_DISCONNECTED)
        self.status_label.setObjectName("Status")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addSpacing(2)
        root.addWidget(self.status_label)

        root.addSpacing(14)

        # --- 2) 启航  3) 靠岸 ---
        buttons = QHBoxLayout()
        buttons.setSpacing(12)

        self.launch_btn = QPushButton(Text.BTN_LAUNCH)
        self.launch_btn.setObjectName("Primary")
        self.launch_btn.setMinimumHeight(56)
        self.launch_btn.setCursor(Qt.PointingHandCursor)
        self.launch_btn.clicked.connect(self._launch)

        self.dock_btn = QPushButton(Text.BTN_DOCK)
        self.dock_btn.setObjectName("Dock")
        self.dock_btn.setMinimumHeight(56)
        self.dock_btn.setCursor(Qt.PointingHandCursor)
        self.dock_btn.clicked.connect(self._dock)

        buttons.addWidget(self.launch_btn, 1)
        buttons.addWidget(self.dock_btn, 1)
        root.addLayout(buttons)

        self.error_label = QLabel("")
        self.error_label.setObjectName("Error")
        self.error_label.setWordWrap(True)
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setMinimumHeight(24)
        root.addSpacing(4)
        root.addWidget(self.error_label)

        root.addSpacing(6)

        # --- 可选设置 ---
        root.addWidget(self._options_card())
        root.addSpacing(10)

        # --- 工具按钮 ---
        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.update_btn = self._tool_button(Text.BTN_UPDATE, "ToolUpdate", "refresh",
                                            P.TOOL_UPDATE, self._do_update)
        self.tcping_btn = self._tool_button(Text.BTN_TCPING, "ToolPing", "terminal",
                                            P.TOOL_PING, self._do_tcping)
        self.urltest_btn = self._tool_button(Text.BTN_URLTEST, "ToolUrl", "link",
                                             P.TOOL_URL, self._do_urltest)
        tools.addWidget(self.update_btn)
        tools.addWidget(self.tcping_btn)
        tools.addWidget(self.urltest_btn)
        root.addLayout(tools)

        root.addSpacing(10)

        # --- 输出结果 ---
        root.addWidget(self._result_card())
        root.addSpacing(8)

        # --- 账号 ---
        bottom = QHBoxLayout()
        bottom.setContentsMargins(2, 0, 2, 0)
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

        # --- 底部水面 ---
        root.addSpacing(SCENE_BAND)

    def _options_card(self) -> QFrame:
        """两排，整体居中，不带行标签。

            第一排（二选一）  ● 分流        ○ 全局
            第二排（可并存）  ☑ 系统代理    ☐ TUN 模式

        两排的互斥性不同：分流/全局 是同一件事的两种模式，必须二选一；
        系统代理 / TUN 是两种可以叠加的接管方式，用独立复选框。
        """
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 13, 14, 13)
        lay.setSpacing(12)

        self._group_profile = QButtonGroup(self)
        self.rb_split = RadioButton(PROFILE_LABELS[PROFILE_SPLIT])
        self.rb_global = RadioButton(PROFILE_LABELS[PROFILE_GLOBAL])
        self._group_profile.addButton(self.rb_split)
        self._group_profile.addButton(self.rb_global)
        self.rb_split.toggled.connect(self._on_options_changed)
        self.rb_split.setToolTip("绕过局域网和大陆，其余流量走代理")
        self.rb_global.setToolTip("所有流量都走代理")
        lay.addLayout(self._centered_row([self.rb_split, self.rb_global]))

        self.cb_system = CheckBox(LABEL_SYSTEM_PROXY)
        self.cb_tun = CheckBox(LABEL_TUN)
        self.cb_system.toggled.connect(self._on_options_changed)
        self.cb_tun.toggled.connect(self._on_options_changed)
        self.cb_system.setToolTip("把 Windows 系统代理指向本机端口，不需要管理员权限")
        self.cb_tun.setToolTip("接管全部流量（IPv4 + IPv6），需要管理员权限和 wintun.dll")
        lay.addLayout(self._centered_row([self.cb_system, self.cb_tun]))

        return card

    def _tool_button(self, text: str, object_name: str, icon_name: str,
                     color: str, slot) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName(object_name)
        btn.setText(text)
        btn.setIcon(QIcon(A.icon(icon_name, 22, color)))
        btn.setIconSize(QSize(22, 22))
        btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        btn.setMinimumHeight(TOOL_H)
        # 三个等宽 —— 不设死的话 Qt 会按 sizeHint 分，"TCping" 就比"更新"宽一截
        btn.setFixedWidth(TOOL_W)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _result_card(self) -> QFrame:
        """输出结果：标题 + 一行（绿点 + 文本）。

        **只有一行**，新的结果顶掉旧的，底下不留空白块。

        **绝不显示内核日志** —— 那里面带节点域名，显示出来就是泄漏。
        详见 logbus.py 顶部的说明。
        """
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        mark = QLabel()
        mark.setPixmap(A.icon("doc", 17, P.TEXT))
        mark.setFixedSize(17, 17)
        head.addWidget(mark)
        title = QLabel(Text.LABEL_RESULT)
        title.setObjectName("ResultTitle")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        row_frame = QFrame()
        row_frame.setObjectName("ResultRow")
        row_frame.setFixedHeight(40)
        row = QHBoxLayout(row_frame)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(10)

        dot = QLabel()
        dot.setObjectName("Dot")
        dot.setFixedSize(9, 9)
        row.addWidget(dot)

        self.result_view = QLabel("—")
        self.result_view.setObjectName("ResultText")
        # 定死一行高 —— 不定的话未 show() 时是控件默认的 480，布局也不可控
        self.result_view.setFixedHeight(22)
        row.addWidget(self.result_view, 1)

        lay.addWidget(row_frame)
        return card

    # ==================================================================
    # 日志
    # ==================================================================
    def _drain_log(self) -> None:
        """把总线上新增的**可显示**结果刷到输出行。

        用的是 visible_since() 而不是 since() —— 内核日志会被挡在外面，
        界面拿不到它，也就不可能显示出来。
        """
        new = bus.visible_since(self._result_seq)
        if not new:
            return
        self._result_seq = new[-1].seq + 1

        line = new[-1]
        # 结果行本身不含域名；错误行可能含，再过一道遮罩
        self.result_view.setText(_escape(_mask_secrets(line.message)))
        color = TAG_COLORS.get(line.tag, RESULT_COLOR)
        # 只改颜色，字号/字体仍由 app 级 QSS 的 #ResultText 决定
        self.result_view.setStyleSheet(f"color: {color};")

    # ==================================================================
    # 选项
    # ==================================================================
    def _load_options_into_ui(self) -> None:
        self._loading = True
        self.rb_split.setChecked(self._opts.profile == PROFILE_SPLIT)
        self.rb_global.setChecked(self._opts.profile == PROFILE_GLOBAL)
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
        self._update_tun_tooltip()
        config.set_and_save(options=self._opts.to_dict())

    def _update_tun_tooltip(self) -> None:
        if self.cb_tun.isChecked():
            self.cb_tun.setToolTip("已开启：全部流量走 TUN，需要管理员权限")
        else:
            self.cb_tun.setToolTip("接管全部流量（IPv4 + IPv6），需要管理员权限和 wintun.dll")

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
    # 服务端推送（SSE）
    # ==================================================================
    def on_push_event(self, payload) -> None:
        """服务端推来的事件。跑在主线程（信号是排队投递的）。"""
        if not isinstance(payload, dict):
            return
        kind = payload.get("type")

        if kind == "config_changed":
            # 广播里**不带节点名**（免得把别人的节点泄露给所有人），
            # 所以只说"变了"，具体是什么等用户重新启航时自然会拿到
            version = payload.get("config_version")
            if version:
                self._config_version = 0   # 强制下次启航重新对齐
            bus.result("配置已更新，请重新启航")

        elif kind == "release":
            version = payload.get("version") or ""
            bus.result(f"有新版本：{version}" if version else "有新版本")

        elif kind == "kick":
            reason = payload.get("reason") or "已被管理员下线"
            bus.error(f"{reason}，自动靠岸")
            if session.sailing:
                self._dock()
            # permanent=True 才是连令牌一起废了（封禁）；单纯踢一次会话的话
            # 令牌还有效，靠岸就完了，不必把人踹回登录页
            if payload.get("permanent"):
                self.logged_out.emit()

        # hello / ping 不需要做什么

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

        def on_ok(cfg) -> None:
            # 服务端可能和登录时给的节点不一样（比如刚被管理员调过），以启航拿到的为准
            if getattr(cfg, "node_name", ""):
                session.node_name = cfg.node_name
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
                    return
            self._hb_timer.start(self._heartbeat_seconds * 1000)

        def on_err(code: str, message: str) -> None:
            self._set_state(STATE_STORM, message)
            bus.error(message)
            if code in ("unauthorized", "banned", "expired", "bad_credentials"):
                self.logged_out.emit()   # 令牌废了，回登录页

        Worker(self._do_start_kernel).run_with(on_ok, on_err)

    def _do_start_kernel(self):
        """启航：先问服务端要入口，再用它拉起内核。

        ★ 客户端自始至终只拿到「中转层入口」——
          真实节点的地址/端口/协议/密钥在服务端那一侧，客户端拿不到。
        """
        mode = "tun" if self._opts.use_tun else "system_proxy"
        cfg = api.fetch_config(mode)          # 服务端做完全套校验才发

        try:
            outbound = build_entry_outbound(cfg.entry)
        except EntryError as exc:
            raise exc

        self._session_id = cfg.session_id
        self._config_version = cfg.config_version
        self._heartbeat_seconds = max(10, int(cfg.heartbeat_interval or 30))
        kernel.start(outbound, self._opts)
        return cfg

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------
    def _on_heartbeat(self) -> None:
        if not session.sailing or not self._session_id:
            self._hb_timer.stop()
            return

        sid = self._session_id

        def on_ok(data) -> None:
            if not isinstance(data, dict):
                return
            if data.get("revoked"):
                bus.error("已被管理员下线，自动靠岸")
                self._dock()
                return
            name = data.get("node_name")
            if name and name != session.node_name:
                session.node_name = name
                self.refresh()
            version = data.get("config_version")
            if version and self._config_version and version != self._config_version:
                bus.result("配置已更新，请重新启航")

        def on_err(code: str, message: str) -> None:
            # 令牌废了 / 被封 / 到期 —— 服务端已经把会话吊销了，本地跟着靠岸
            if code in ("unauthorized", "banned", "expired"):
                bus.error(message)
                self._dock()

        Worker(api.heartbeat, sid).run_with(on_ok, on_err)

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
        self._hb_timer.stop()
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

        # 告诉服务端这次会话结束了。
        # 用 /api/session/stop 而不是 /api/logout —— 后者会吊销登录令牌，
        # 用户每次靠岸都得重新登舟。丢到工作线程，别卡住界面。
        if self._session_id:
            sid, self._session_id = self._session_id, ""
            Worker(api.stop_session, sid).run_with()

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
        """「更新」一次查两条 —— **客户端更新 + 订阅更新**。

            更新：1.0.0 最新 · 订阅：香港-01

        结果框只留一行，所以两条拼起来显示；有新版本时再弹窗给详情。
        """
        self.update_btn.setEnabled(False)

        def work():
            parts: list[str] = []
            newer = None
            revision = ""
            try:
                rel = api.latest_release()
                if update.compare_versions(rel.version, VERSION) > 0:
                    parts.append(f"更新：{VERSION} → {rel.version}")
                    newer = rel
                else:
                    parts.append(f"更新：{VERSION} 最新")
            except CanoeApiError as exc:
                parts.append(f"更新：{exc.message}")

            if api.token:
                try:
                    sub = api.subscription()
                    revision = sub.revision
                    if revision and revision == self._revision:
                        parts.append("订阅：最新")
                    elif sub.node_name:
                        parts.append(f"订阅：{sub.node_name}")
                    else:
                        parts.append("订阅：有更新")
                except CanoeApiError as exc:
                    parts.append(f"订阅：{exc.message}")
            return " · ".join(parts), newer, revision

        def on_ok(payload) -> None:
            self.update_btn.setEnabled(True)
            text, newer, revision = payload
            if revision:
                self._revision = revision
            bus.result(text)
            if newer is not None:
                # 详细内容放弹窗，不塞进结果框（结果框只留一行）
                detail = f"当前版本：{VERSION}\n最新版本：{newer.version}"
                if newer.notes:
                    detail += f"\n\n更新说明：\n{newer.notes}"
                if newer.url:
                    detail += f"\n\n下载地址：\n{newer.url}"
                QMessageBox.information(self, "有新版本", detail)

        def on_err(code: str, message: str) -> None:
            self.update_btn.setEnabled(True)
            bus.error(f"更新：{message}")

        Worker(work).run_with(on_ok, on_err)

    def _do_tcping(self) -> None:
        """测本机到**中转层入口**的 TCP 握手延迟。

        测的是入口，不是真实节点 —— 真实节点在服务端那一侧，
        客户端拿不到（也不该拿到），所以只能测"我到入口这条路通不通"。
        """
        self.tcping_btn.setEnabled(False)

        def work():
            sub = api.subscription()
            if sub.entry is None:
                raise EntryError("当前没有可用节点，请联系管理员")
            return tcping(sub.entry.host, sub.entry.port, 4)

        def on_ok(result) -> None:
            self.tcping_btn.setEnabled(True)
            (bus.result if result.ok else bus.error)(_result_line(result))

        def on_err(code: str, message: str) -> None:
            self.tcping_btn.setEnabled(True)
            bus.error(f"TCP 延迟：{message}")

        Worker(work).run_with(on_ok, on_err)

    def _do_urltest(self) -> None:
        if not session.sailing:
            bus.error("URL测试  需要先启航")
            return

        self.urltest_btn.setEnabled(False)
        port = int(self._opts.mixed_port)

        def on_ok(result) -> None:
            self.urltest_btn.setEnabled(True)
            (bus.result if result.ok else bus.error)(_result_line(result))

        def on_err(code: str, message: str) -> None:
            self.urltest_btn.setEnabled(True)
            bus.error(f"URL 耗时：{message}")

        Worker(url_test, port).run_with(on_ok, on_err)

    # ==================================================================
    # 退出
    # ==================================================================
    def _logout(self) -> None:
        self._do_logout()

    def _do_logout(self) -> None:
        self._hb_timer.stop()
        sid = self._session_id
        if session.sailing or self._session_id:
            self._dock()
        session.logout()
        self.logged_out.emit()
        # 吊销服务端令牌（幂等：没令牌 / 已失效都返回 ok）
        Worker(api.logout, sid or None).run_with()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关窗必须停内核并还原系统代理，否则用户会断网。

        注意也要看 `kernel.running`：刚点完启航、内核正在起的那一两秒里
        `session.sailing` 还是 False，只看它会漏掉这时候关窗的情况。
        """
        self._log_timer.stop()
        self._hb_timer.stop()
        if session.sailing or kernel.running or sysproxy.has_backup():
            self._dock()
        super().closeEvent(event)

    def start_with_node(self, username: str, node_name: str = "") -> None:
        """登录成功后进入主界面。

        节点名是**登录时服务端一起给的** —— 主界面的「节点名称」要在点启航
        之前就能显示，不能等启航才知道连哪个。
        """
        session.login(username, node_name)
        self._session_id = ""
        self._config_version = 0
        self._revision = ""
        self.refresh()
