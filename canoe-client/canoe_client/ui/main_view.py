"""主界面 —— 启航 / 靠岸。

需求：登录后主界面只显示三个东西 —— 节点名称、启航、靠岸。
这里严格保留这三样作为主视觉，另外多了：

  · 一行状态（渡江中… / 已启航 / 已靠岸 / 风浪太大，请重试）
    —— 需求里明确规定了这四句文案，不显示用户就不知道当前状态
  · 一组可选设置（两排：分流/全局，系统代理/TUN）
    —— 需求里的"客户可选部分，默认系统代理"
  · 底部账号名 + 离舟

**界面上永远不显示节点的地址、端口、协议、密码，也没有任何导出入口。**
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from canoe_core import BRAND_CN, Text

from .. import sysproxy
from ..config import config
from ..kernel import kernel
from ..options import (
    LABEL_SYSTEM_PROXY,
    LABEL_TUN,
    PROFILE_GLOBAL,
    PROFILE_LABELS,
    PROFILE_SPLIT,
    RunOptions,
)
from ..session import STATE_DOCKED, STATE_SAILED, STATE_STORM, session
from ..testnodes import build_proxy_outbound, node_display_name
from ..tun import check_tun_ready, relaunch_as_admin
from ..worker import Worker


class MainView(QWidget):
    logged_out = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(BRAND_CN)
        self.setFixedSize(390, 470)

        self._opts = RunOptions.from_dict(config["options"])
        self._build()
        self._load_options_into_ui()
        self.refresh()
        self._set_state(STATE_DOCKED)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 18)
        root.setSpacing(0)

        brand = QLabel(BRAND_CN)
        brand.setObjectName("Slogan")
        brand.setAlignment(Qt.AlignCenter)
        root.addWidget(brand)
        root.addSpacing(14)

        # --- 1) 节点名称 ---
        self.node_label = QLabel("—")
        self.node_label.setObjectName("NodeName")
        self.node_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.node_label)

        self.status_label = QLabel(Text.ST_DISCONNECTED)
        self.status_label.setObjectName("Status")
        self.status_label.setAlignment(Qt.AlignCenter)
        root.addSpacing(6)
        root.addWidget(self.status_label)

        root.addSpacing(22)

        # --- 2) 启航  3) 靠岸 ---
        buttons = QHBoxLayout()
        buttons.setSpacing(12)

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
        self.error_label.setMinimumHeight(34)
        root.addSpacing(6)
        root.addWidget(self.error_label)

        root.addSpacing(8)

        # --- 可选设置 ---
        root.addWidget(self._options_card())

        root.addStretch(1)

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
        lay.setContentsMargins(14, 11, 14, 13)
        lay.setSpacing(11)

        # --- 第一排：分流模式 ---
        # 这两个互斥，用 QButtonGroup 明确成组，不依赖 Qt 的"同父自动互斥"
        self._group_profile = QButtonGroup(self)
        self.rb_split = QRadioButton(PROFILE_LABELS[PROFILE_SPLIT])
        self.rb_global = QRadioButton(PROFILE_LABELS[PROFILE_GLOBAL])
        self._group_profile.addButton(self.rb_split)
        self._group_profile.addButton(self.rb_global)
        self.rb_split.toggled.connect(self._on_options_changed)
        self.rb_split.setToolTip("绕过局域网和大陆，其余流量走代理")
        self.rb_global.setToolTip("所有流量都走代理")
        lay.addLayout(self._centered_row([self.rb_split, self.rb_global]))

        # --- 第二排：接管方式（可同时勾选）---
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
        """把一组选项作为整体居中。"""
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch(1)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        return row

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Hint")
        return label

    # ------------------------------------------------------------------
    # 选项读写
    # ------------------------------------------------------------------
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
            # 运行中改选项：先靠岸，让用户重新启航，避免半途换配置
            self._dock()

        self._opts.profile = PROFILE_GLOBAL if self.rb_global.isChecked() else PROFILE_SPLIT
        self._opts.use_system_proxy = self.cb_system.isChecked()
        self._opts.use_tun = self.cb_tun.isChecked()
        config.set_and_save(options=self._opts.to_dict())
        self._update_tun_tooltip()

    def _update_tun_tooltip(self) -> None:
        """TUN 勾上但环境不满足时，把原因挂在提示上。"""
        if not self._opts.use_tun:
            self.cb_tun.setToolTip("接管全部流量（IPv4 + IPv6），需要管理员权限和 wintun.dll")
            return
        exe = config.find_singbox()
        ready, reason = check_tun_ready(exe.parent if exe else None)
        self.cb_tun.setToolTip(
            "接管全部流量（IPv4 + IPv6）" if ready else f"暂时不可用：{reason}"
        )

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 启航
    # ------------------------------------------------------------------
    def _launch(self) -> None:
        self.error_label.setText("")
        self.launch_btn.setEnabled(False)
        self.launch_btn.setText(Text.progress(Text.BTN_LAUNCH))
        self.status_label.setText(Text.ST_CONNECTING)

        # 勾了 TUN 就先查权限，不合格别白忙
        if self._opts.use_tun:
            exe = config.find_singbox()
            ready, reason = check_tun_ready(exe.parent if exe else None)
            if not ready:
                self._apply_buttons()
                self._handle_tun_blocked(reason)
                return

        def on_ok(_none) -> None:
            self.refresh()
            self._set_state(STATE_SAILED)

            # 勾了系统代理就把 Windows 代理指向本地 mixed 入站。
            # 注意：这和 TUN 不冲突，两个都勾时两件事都做。
            if self._opts.use_system_proxy:
                try:
                    sysproxy.set_proxy("127.0.0.1", int(self._opts.mixed_port))
                except OSError as exc:
                    kernel.stop()
                    self._set_state(STATE_STORM, f"设置系统代理失败：{exc}")

        def on_err(code: str, message: str) -> None:
            self._set_state(STATE_STORM, message)

        Worker(self._do_start_kernel).run_with(on_ok, on_err)

    def _do_start_kernel(self) -> None:
        """在线程里跑，避免启动内核时界面卡住。"""
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

    # ------------------------------------------------------------------
    # 靠岸
    # ------------------------------------------------------------------
    def _dock(self) -> None:
        self.dock_btn.setEnabled(False)

        try:
            kernel.stop()
        except Exception as exc:  # noqa: BLE001
            self.error_label.setText(f"关闭内核时出错：{exc}")

        # 只要启航时设过系统代理，靠岸就要还原 —— 不管 TUN 有没有同时开
        if self._opts.use_system_proxy:
            try:
                sysproxy.clear_proxy()
            except OSError as exc:
                self.error_label.setText(f"还原系统代理失败：{exc}")

        self._set_state(STATE_DOCKED)

    # ------------------------------------------------------------------
    # 离舟 / 关窗
    # ------------------------------------------------------------------
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
        if session.sailing:
            self._dock()
        super().closeEvent(event)

    def start_with_test_node(self, username: str) -> None:
        """阶段1：节点名来自写死的测试节点。阶段3 换成服务端下发的 node_name。"""
        session.login(username, node_display_name())
