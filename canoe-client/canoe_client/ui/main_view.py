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
import threading

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
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

from canoe_core import BRAND_CN, VERSION, Palette as P, Route, Text

from .. import links, sysproxy, update
from ..api import CanoeApiError, api
from ..config import config
from ..kernel import kernel
from ..logbus import TAG_ERROR, TAG_RESULT, bus
from ..nettest import target_url, tcping, url_test
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
from .nodelights import NodeLights
from .update_dialog import UpdateDialog
from .window_base import WINDOW_H, WINDOW_W, FramelessWindow

LOG_POLL_MS = 300

SIDE_PAD = 20            # 正文左右留白
#: ★ 界面上**所有**层级之间都用这一个距离。用户原话：「界面每个层级保持
#:   距离搞一致啊，太难受了」—— 之前是 10/2/14/6/10/10/18/4/0/46 一堆
#:   不同的数，看着就乱。现在连底部那片水面留白也收进来了（"上面红框的
#:   距离抹掉，整个界面就变协调了，高度也短了"）。
#:   只剩两处例外，都在下面就近写了原因：节点名→状态 2px（一组）、
#:   按钮→报错行 4px（报错是按钮的反馈）。
GAP = 12
#: 页脚（按钮 → 账号行 → 版本号）比正文紧一点。用户看完正文那版说
#: 「启航 靠岸 底下还缩点」—— 这三样是收尾信息，挨紧些整幅才收得住。
FOOT_GAP = 8
#: 主界面在"还没有那行字"时的高度。窗高不许越过它 —— 为这行字用户来回
#: 改过三回（"越拉越长 / 是高度不是长度"），别再让它长回去。
#: 实测 674；留 4px 给字体度量的抖动（show() 前后能差 2px，别让测试偶发红）。
HEIGHT_BEFORE_ROUTE = 678
BOTTOM_PAD = 14          # 版本号离窗口下沿（页脚，见 FOOT_GAP）
#: ↑ 用户：「用户名 离舟 版本 都高一点点」—— 底下多留一点，这两行
#:   就一起抬起来（整窗跟着高这一点点）。
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


class SubscriptionError(Exception):
    """订阅不可用：服务端没给，或者里面没有能解析出来的节点。

    code 属性是给 Worker 用的 —— 它按 (code, message) 把异常转成信号。
    """

    code = "no_subscription"

    def __init__(self, message: str, code: str = "no_subscription") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


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
    #: 「TUN 是不是真的在跑」变了。托盘靠它决定要不要给图标点红点。
    #: 注意口径是**正在跑**，不是"勾选框被勾上了" —— 靠岸之后勾还在，
    #: 但那时没有接管任何流量，点红点就是骗人。
    tun_active_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__(WINDOW_W, 560)

        self._opts = RunOptions.from_dict(config["options"])
        self._result_seq = 0

        #: 关窗时收进托盘而不是退出。由 app.py 在确认托盘可用后打开；
        #: 没有托盘的环境保持 False，关窗就是关窗。
        self.close_to_tray = False
        self._force_close = False

        # --- 与服务端会话相关的状态 ---
        #: 本次会话 id。启航时服务端发下来的，靠岸时要用它结束会话。
        self._session_id = ""
        #: 服务端下发的订阅指纹，用来判断"订阅变了没有"
        self._revision = ""
        self._heartbeat_seconds = 30

        #: 订阅明文（节点链接）。**只在内存里** —— 不落盘、不导出。
        #: 进程一退就没了，下次登舟重新拉。这也正是"服务端随时能收回"的前提。
        self._sub_text = ""
        #: 订阅里解析出来的全部节点。留着整份是为了支持"在几个节点之间切"
        #: —— 以前是每次用的时候 links.pick() 现挑第一个，切不了。
        self._links: list = []
        #: 当前用第几个。底部那排灯就是照它点亮的。
        self._active = 0
        #: 当前节点的显示名。启航前也可能有（登舟后就从订阅里解出来了）。
        self._node_name = ""

        #: 「有新版本」那扇窗。同一时间只留一扇。
        self._update_dialog: UpdateDialog | None = None
        #: 下载的取消开关。**每次开下都换一个新的** —— 复用的话上次
        #: 按过取消，这次一进来就是置位状态，下载立刻自己掐掉。
        self._update_cancel = threading.Event()
        #: TUN 当前是不是真的在跑（给托盘那颗红点用的）
        self._tun_active = False

        self._build()
        self._load_options_into_ui()
        self.refresh()
        self._set_state(STATE_DOCKED)

        # 布局定型后再把窗口收到内容高度 —— 底下不留空白带
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        # ★ 用共享的 WINDOW_H，**不是** sizeHint().height()：登录页那个窗口
        #   也是这个数，两边写死成同一个值，切页面才不会忽高忽低（差几
        #   像素就会跳一下）。内容比它高的话由底部的间隔吸收；test_gui
        #   里有一条盯着「内容自然高度约等于 WINDOW_H」。
        self.setFixedSize(WINDOW_W, WINDOW_H)

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
        # 底部留白。最后一行是版本号，原来只留 6px —— 用户反馈"太贴底"，
        # 现在留 BOTTOM_PAD。整窗高度是靠 sizeHint 量的，这里每多一点窗口
        # 就长一点，所以这个数别随手加。
        root.setContentsMargins(SIDE_PAD, 10, SIDE_PAD, BOTTOM_PAD)
        root.setSpacing(0)

        root.addWidget(self._kicker())
        root.addSpacing(GAP)

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

        root.addSpacing(GAP)

        # --- 2) 节点灯 ---
        #   一个灯 = 订阅里一个能用的节点，亮着就是有。点一下切过去。
        #   不写字：上面那行大字就是当前节点的名字，底下再标一遍又挤又重复。
        #   ★ 位置：紧跟着节点名。用户嫌原来"头重脚轻"（那颗大蓝按钮压在
        #     顶上、底下反倒空着），把灯和下面那两个按钮对调了。
        self.lights = NodeLights()
        self.lights.node_selected.connect(self._switch_node)
        root.addWidget(self.lights)

        root.addSpacing(GAP)

        # --- 可选设置 ---
        root.addWidget(self._options_card())
        root.addSpacing(GAP)

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

        root.addSpacing(GAP)

        # --- 输出结果 ---
        root.addWidget(self._result_card())
        root.addSpacing(GAP)

        # --- 3) 启航  4) 靠岸 ---
        #   ★ 挪到最底下了（用户："把节点灯和启航靠岸对换一下位置，
        #     现在头重脚轻"）。底下压着水纹，整块看着才稳。
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
        # ★ 不设最小高度：空的时候它高度是 0，所以按钮和账号行之间的间距
        #   还是 GAP —— 跟别处一样齐。早先写死 24px，等于平时也留着一块
        #   看不见的空白，用户一眼就看出"这块比别处宽"。
        #   真出错时它顶出来的高度从下面的水面里借（窗是定高的）。
        root.addSpacing(4)
        root.addWidget(self.error_label)

        root.addSpacing(FOOT_GAP)

        # --- 账号 ---
        #   ★ 挪到按钮底下了（用户："用户名和离舟移到下面来"）——
        #     登出这种"退出类"的操作压在主动作上头，读起来是反的。
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
        #   ⚠ 这里**不要**再往界面上加"出国模式 / 回国模式"那行字。
        #     加过三版、用户改了四回，最后由他自己拍板："干脆不用显示，
        #     看着别闹"。方向仍然生效（服务端定、在航时被切会重连），
        #     只是不在界面上占地方 —— 想要的话看「分流」那个提示。
        #   底部这块留白也收成 FOOT_GAP（用户：「上面红框的距离抹掉，
        #   整个界面就变协调了，高度就变短了好看点」）。水纹是**画上去的
        #   背景**，不靠留白撑 —— 它就是窗底那一层，内容压上去也看得见。
        root.addSpacing(FOOT_GAP)

        # --- 版本号 ---
        #   压在底边正中。用户报问题时第一句常常是"我装的是哪个版本"，
        #   而更新弹窗只在有新版本时才出来 —— 得有个地方随时能看。
        #   写法就"版本:V1.0.30" —— 原来写的是"当前版本"，用户嫌啰嗦
        #   （原话："当前版本 改成 版本 这样正规点"）。
        self.version_label = QLabel(f"版本:V{VERSION}")
        self.version_label.setObjectName("Version")
        self.version_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.version_label)

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

        # ★ 还没出过任何结果时**整行是空的** —— 绿点和文字都先藏起来。
        #   以前这儿预置了一个 "—"，用户原话："首次使用 应该为空 现在有个 点 杠"。
        #   点要跟着一起藏：只清文字的话，那行还孤零零亮着个绿点。
        self.result_dot = QLabel()
        self.result_dot.setObjectName("Dot")
        self.result_dot.setFixedSize(9, 9)
        self.result_dot.hide()
        row.addWidget(self.result_dot)

        self.result_view = QLabel("")
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
        # 有结果了才把绿点放出来（没结果时整行是空的，见 _result_card）
        self.result_dot.show()
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
        self._sync_route_label()

    # ------------------------------------------------------------------
    # 线路方向（服务端给的，只显示）
    # ------------------------------------------------------------------
    def _sync_route_label(self) -> None:
        """把当前方向刷到提示里。

        界面上**不显示**方向（用户："干脆不用显示，看着别闹"），但那个
        「分流」的提示得跟着方向换 —— 「分流」是什么意思本来就跟方向走：
        出国是"大陆直连"，回国是"国外直连"。这是唯一还看得见方向的地方，
        鼠标停上去就能确认自己是哪种。
        """
        mode = Route.clean(self._opts.route_mode)
        self.rb_split.setToolTip(
            f"线路模式由服务端指定：{Route.HINTS[mode]}"
        )

    def _apply_route_mode(self, value: str | None, reconnect: bool = True) -> str:
        """收下服务端给的线路方向。变了就返回一句提示，没变返回空串。

        变了而且在航的话：靠岸 -> 按新方向重新启航。这不是可选项 ——
        方向挂在**分流规则**上，不重连就只是界面上换了个字，底下还按
        老规矩走。断一下（TUN 拆网卡要一两秒）比"显示的和实际的不一致"
        强得多。

        reconnect=False 给"调用方自己马上要重连一次"的场合用（拉订阅那条
        路本来就会因为节点变化重启内核）—— 免得连着重启两回。

        ⚠ 必须在主线程调用（要动内核和控件）。推送那条路是信号排队过来的，
          本来就在主线程上；启航那条路在工作线程，那边只改 _opts 里的数据，
          界面的字等回到主线程再刷（见 _do_start_kernel 的 on_ok）。
        """
        if value is None:
            return ""
        new = Route.clean(value)
        if new == Route.clean(self._opts.route_mode):
            return ""

        self._opts.route_mode = new
        config.set_and_save(options=self._opts.to_dict())
        self._sync_route_label()

        if reconnect and session.sailing:
            link = self._active_link()
            if link is not None:
                try:
                    self._dock()
                    kernel.start(link.outbound, self._opts)
                    self._set_state(STATE_SAILED)
                except Exception as exc:              # noqa: BLE001
                    self._set_state(STATE_STORM, f"切换线路方向失败：{exc}")
        return f"线路已切到{Route.SHORT[new]}"

    def _on_options_changed(self) -> None:
        if getattr(self, "_loading", False):
            return
        if session.sailing:
            self._dock()          # 运行中改选项：先靠岸，避免半途换配置

        self._opts.profile = PROFILE_GLOBAL if self.rb_global.isChecked() else PROFILE_SPLIT
        self._opts.use_system_proxy = self.cb_system.isChecked()
        self._opts.use_tun = self.cb_tun.isChecked()
        self._update_tun_tooltip()
        # 上面那句 _dock() 已经刷过一次状态，但那会儿 use_tun 还是旧值 ——
        # 改完得再同步一次，不然"在航时勾上 TUN"这条路上红点不会亮。
        self._sync_tun_indicator()
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
        self._sync_tun_indicator()

    def _sync_tun_indicator(self) -> None:
        """TUN 是不是真的在跑 —— 变了才发信号，免得托盘图标一直被重画。"""
        active = session.sailing and self._opts.use_tun
        if active != self._tun_active:
            self._tun_active = active
            self.tun_active_changed.emit(active)

    # ==================================================================
    # 服务端推送（SSE）
    # ==================================================================
    def on_push_event(self, payload) -> None:
        """服务端推来的事件。跑在主线程（信号是排队投递的）。"""
        if not isinstance(payload, dict):
            return
        kind = payload.get("type")

        if kind == "config_changed":
            # 订阅被改了（可能是别的账号，也可能就是自己）。
            # 广播里不带任何订阅内容，所以这里立刻重新拉一次自己的：
            # 拉回来是空的话，就地销毁并靠岸 —— 管理员一清空订阅，
            # 在航的客户端当场停。
            self._reload_subscription()

        elif kind == "release":
            # 这条广播是**发布**时推给所有人的，不是"给这个客户的升级通知"。
            # 所以必须先跟自己比一比：跟自己一样（或更低）就一个字都别说 ——
            # 明明已经是最新的版本了，结果框里却跳一句「有新版本：1.0.31」，
            # 只会让人以为更新没成功（用户就是这么撞上的）。
            version = payload.get("version") or ""
            if version and update.compare_versions(version, VERSION) > 0:
                bus.result(f"发现新版本：V{version}")

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

        def on_ok(payload) -> None:
            cfg, found, index = payload
            # 节点列表落到界面上（点亮底下那排灯）。放在这里而不是
            # _do_start_kernel 里，是因为那边在工作线程，碰控件不安全。
            self._apply_links(found, index)
            # 线路方向同理：工作线程只改了 _opts 里的值，这里的字得在这刷
            self._sync_route_label()
            session.node_name = self._node_name
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
            # 启航没成功 —— 我们自己设过的那条系统代理必须还回去。
            #
            # 不还的话注册表指向一个**没人在听的端口**：用户接下来是断网的，
            # 而且连"点更新"都会失败（requests 会老老实实走那条死代理，
            # 于是"系统代理加 TUN 的时候下不了更新"）。
            if sysproxy.has_backup():
                try:
                    sysproxy.clear_proxy()
                    bus.system("系统代理已还原")
                except OSError:
                    pass
            self._set_state(STATE_STORM, message)
            bus.error(message)
            if code == "no_subscription":
                # 服务端明确不给 —— 当场把本地订阅销毁，一点不留
                self._wipe_subscription()
            elif code in ("unauthorized", "banned", "expired", "bad_credentials"):
                self.logged_out.emit()   # 令牌废了，回登录页

        Worker(self._do_start_kernel).run_with(on_ok, on_err)

    def _wipe_subscription(self) -> None:
        """销毁本地订阅。

        服务端说"没有"的时候必须真的把它抹掉，而不是留着下次再试 ——
        不然"服务端关掉订阅"就只是下次启动失败，本地那份节点信息还在。
        """
        self._sub_text = ""
        self._revision = ""
        self._links = []
        self._active = 0
        self._node_name = ""
        session.node_name = ""
        self.lights.set_nodes([])

    def _reload_subscription(self) -> None:
        """重新拉订阅。收到推送或点「更新」时调用。

        拉回来是空的就地销毁；在航的话自动靠岸 —— 这就是服务端
        "关掉订阅"能立刻生效的那条路径。
        """
        Worker(self._refresh_after_login).run_with(self._on_reloaded, self._on_reload_failed)

    def _on_reloaded(self, payload) -> None:
        revision, text, route_mode = payload
        self._revision = revision
        # 先把方向收下：下面的重连就按新方向来，不用连两次。
        # reconnect=False —— 这个函数自己就会重启内核。
        note = self._apply_route_mode(route_mode, reconnect=False)
        if not self._apply_subscription(text):
            self._wipe_subscription()
            if session.sailing:
                bus.error("订阅已停止分发，自动靠岸")
                self._dock()
            elif note:
                bus.result(note)
            else:
                bus.result("订阅：已停止分发")
            return

        link = self._active_link()
        # 在航的时候订阅换了节点：把新节点用起来，别让界面显示的和实际
        # 连着的对不上（管理员改了链接，这条路径会走到）。
        # 线路方向换了也走这儿 —— 反正都要按新的 opts 重启一次。
        if session.sailing and link is not None:
            try:
                kernel.stop()
                kernel.start(link.outbound, self._opts)
            except Exception as exc:                  # noqa: BLE001
                self._set_state(STATE_STORM, f"重新启航失败：{exc}")
                return
        tail = f"订阅已更新：{link.name}" if link else "订阅已更新"
        bus.result(f"{note} · {tail}" if note else tail)

    def _on_reload_failed(self, code: str, message: str) -> None:
        if code in ("unauthorized", "banned", "expired"):
            bus.error(message)
            if session.sailing:
                self._dock()
            self.logged_out.emit()

    # ------------------------------------------------------------------
    # 节点：解析、点亮、切换
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_subscription(text: str) -> list:
        """订阅明文 -> 节点列表。**不碰界面**，所以能在工作线程里跑。"""
        return links.parse(text).links if (text or "").strip() else []

    def _apply_links(self, found: list, index: int | None = None) -> bool:
        """把一份解析好的节点列表落到界面上（**只能在 GUI 线程调**）。

        一个节点都没有时灯全灭 —— 用户一眼就看出来"服务端没给东西"，
        不用去读结果框里那行字。
        """
        self._links = list(found)
        if not self._links:
            self._active = 0
            self._node_name = ""
            session.node_name = ""
            self.lights.set_nodes([])
            self.refresh()
            return False
        # 订阅变短了的话，别让 _active 指向不存在的那一盏
        self._active = index if index is not None else self._active
        if not (0 <= self._active < len(self._links)):
            self._active = 0
        self._sync_active()
        return True

    def _apply_subscription(self, text: str) -> bool:
        """解析 + 落到界面。给 GUI 线程用的那条便捷路径。"""
        self._sub_text = text
        return self._apply_links(self._parse_subscription(text))

    def _sync_active(self) -> None:
        """把"当前第几个"同步到界面：节点名、这排灯、状态行。"""
        link = self._active_link()
        self._node_name = link.name if link else ""
        session.node_name = self._node_name
        self.lights.set_nodes([n.name for n in self._links], self._active)
        self.refresh()

    def _active_link(self):
        """当前选中的那个节点（NodeLink），没有就 None。"""
        if 0 <= self._active < len(self._links):
            return self._links[self._active]
        return None

    def _switch_node(self, index: int) -> None:
        """点了第 index 盏灯。

        没在航的话只是换个选择，下次启航用它；在航的话当场重连 ——
        停内核再用新节点起来。系统代理不用动（还是指本机那个口）。
        """
        if not (0 <= index < len(self._links)) or index == self._active:
            return
        self._active = index
        self._sync_active()

        link = self._active_link()
        if link is None:
            return
        if not session.sailing:
            bus.result(f"已选择：{link.name}")
            return

        was_tun = self._opts.use_tun
        try:
            kernel.stop()
            kernel.start(link.outbound, self._opts)
        except Exception as exc:                      # noqa: BLE001 - 要兜住任何启动失败
            self._set_state(STATE_STORM, f"切到「{link.name}」失败：{exc}")
            if was_tun:
                bus.error(f"切换失败：{exc}")
            return
        bus.result(f"已切换到：{link.name}")

    def _fetch_subscription(self) -> str:
        """拉订阅并解密。结果只留在内存，返回明文（可能为空串）。

        每次启航、每次点「更新」都会走这里 —— 这就是"每次交互都要
        重新证明身份"的那个闭环。服务端想收回，下一次交互就拿不到了。

        ⚠ 这个函数会在**工作线程**里被调用（启航那条路），所以这里只把
          route_mode 落到 _opts 这个数据结构上，不碰任何控件 —— 界面上的
          那行字由主线程的 on_ok 去刷。
        """
        resp, text = api.subscription_text()
        self._revision = resp.revision
        self._sub_text = text
        self._opts.route_mode = Route.clean(resp.route_mode)
        return text

    def _do_start_kernel(self):
        """启航：拉订阅 -> 挑一个节点 -> 拉起内核。

        ★ 服务端不下发节点，也无从知道客户端连的是哪台 ——
          节点全在客户端解密出来的订阅里。
        """
        # 报给服务端的接管方式。两种**可以同时开**（见 options.py），所以不是
        # 二选一 —— 只报 "tun" 的话，面板上看不出这台机器还挂着系统代理。
        # 服务端那个字段是自由字符串，不校验，所以叠加值直接发得过去。
        if self._opts.use_tun and self._opts.use_system_proxy:
            mode = "system_proxy+tun"
        elif self._opts.use_tun:
            mode = "tun"
        else:
            mode = "system_proxy"
        # 先建会话：被封/到期在这里就会被挡下，不用等到启航一半
        cfg = api.fetch_config(mode)
        # 这次启航的线路方向，以服务端这次给的为准（工作线程：只改数据）
        self._opts.route_mode = Route.clean(cfg.route_mode)

        # 每次都重新拉，不吃缓存 —— 管理员刚清空订阅的话，这次启航就得失败
        text = self._fetch_subscription()
        if not text.strip():
            raise SubscriptionError(
                "服务端没有下发订阅（账号可能已到期、被停用，或管理员清空了订阅栏）",
                code="no_subscription",
            )

        parsed = links.parse(text)
        found = parsed.links
        if not found:
            raise SubscriptionError(f"订阅里没有能用的节点（{len(parsed.skipped)} 行认不出来）")

        # ⚠ 这一段跑在工作线程里，**不能碰任何控件**（灯、标签都不行）。
        #   解析结果原样回传给 on_ok，由它在 GUI 线程落到界面上。
        index = self._active if 0 <= self._active < len(found) else 0
        link = found[index]
        self._session_id = cfg.session_id
        self._node_name = link.name
        self._heartbeat_seconds = max(10, int(cfg.heartbeat_interval or 30))
        kernel.start(link.outbound, self._opts)
        return cfg, found, index

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------
    def _on_heartbeat(self) -> None:
        if not session.sailing or not self._session_id:
            self._hb_timer.stop()
            return

        # 内核半路没了，得当场发现并收拾。
        #
        # 不看的话程序还显示"已启航"，而注册表里的系统代理指向一个**没人
        # 在听的端口** —— 用户那边就是断网，还查不出原因（浏览器、以及
        # 我们自己点更新，全会老老实实去撞那条死代理）。
        # 就地靠岸：把代理还回去、把界面拉回真实状态。
        if not kernel.running:
            bus.error("内核已经不在了（可能被杀掉或崩了），自动靠岸")
            self._dock()
            return

        sid = self._session_id

        def on_ok(data) -> None:
            if not isinstance(data, dict):
                return
            if data.get("revoked"):
                bus.error("已被管理员下线，自动靠岸")
                self._dock()
                return
            # 心跳响应里带着服务端当前的订阅指纹。变了就说明订阅被改过，
            # 立刻重新拉一次 —— 被清空的话这里就会当场销毁并靠岸。
            revision = data.get("revision")
            if revision and revision != self._revision:
                self._reload_subscription()

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
                # 提权重启是要**真的**关掉本进程，不能收进托盘
                Qt.callLater(self.force_close)
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
            wiped = False
            route_mode = ""
            try:
                rel = api.latest_release()
                if update.compare_versions(rel.version, VERSION) > 0:
                    parts.append(f"发现新版本：V{rel.version}")
                    # 服务端返回的是接口模型，这里转成 update 模块那套 ——
                    # 摘要和大小要跟着走，下载完得靠它们核对。
                    newer = update.UpdateInfo(
                        latest=rel.version, current=VERSION, url=rel.url,
                        notes=rel.notes, size=rel.size, sha256=rel.sha256,
                        min_version=rel.min_version,
                    )
                else:
                    # 跟自己一样、或比自己低，都是"已是最新"。
                    # （以前这句写的是「更新：1.0.31 最新」—— 跟上面那条广播
                    #   一样容易被读成"我是不是没更新成功"。）
                    parts.append(f"当前已是最新版本 V{VERSION}")
            except CanoeApiError as exc:
                parts.append(f"更新：{exc.message}")

            if api.token:
                try:
                    # 和启航走的是同一条路：重新证明身份 -> 解密订阅
                    sub, text = api.subscription_text()
                    revision = sub.revision
                    route_mode = sub.route_mode
                    names = [n.name for n in links.parse(text).links] if text.strip() else []
                    if not names:
                        # 空的 / 认不出的 —— 服务端没给，本地那份必须销毁
                        wiped = True
                        parts.append("订阅：已停止分发")
                    else:
                        # 报**当前选中的**那个。以前报的是"第一个"，
                        # 用户切到第二个之后这里就会对不上。
                        here = names[min(self._active, len(names) - 1)]
                        suffix = "（最新）" if revision and revision == self._revision else ""
                        extra = f"，共 {len(names)} 个节点" if len(names) > 1 else ""
                        parts.append(f"订阅：{here}{extra}{suffix}")
                except CanoeApiError as exc:
                    parts.append(f"订阅：{exc.message}")
            return " · ".join(parts), newer, revision, wiped, route_mode

        def on_ok(payload) -> None:
            self.update_btn.setEnabled(True)
            text, newer, revision, wiped, route_mode = payload
            if wiped:
                self._wipe_subscription()
                self._apply_route_mode(route_mode, reconnect=False)
                if session.sailing:
                    bus.error("订阅已停止分发，自动靠岸")
                    self._dock()
            elif revision:
                self._revision = revision
                # 线路方向也在这条路上收：它算在指纹里，变了 revision 必变。
                # reconnect=False —— 下面那句 _reload_subscription 自己会
                # 按新的 opts 重启一次内核。
                note = self._apply_route_mode(route_mode, reconnect=False)
                if note:
                    text = f"{note} · {text}"
                # 订阅可能变了（管理员加了/改了节点），把本地那份跟着刷新，
                # 底下的灯也就跟着变。
                self._reload_subscription()
            bus.result(text)
            if newer is not None:
                self._show_update(newer)

        def on_err(code: str, message: str) -> None:
            self.update_btn.setEnabled(True)
            bus.error(f"更新：{message}")

        Worker(work).run_with(on_ok, on_err)

    # ==================================================================
    # 更新：查到新版本之后
    # ------------------------------------------------------------------
    # 这一段回答的是"然后呢"。以前查到新版本只弹一个 QMessageBox，把
    # 下载地址念给用户听，剩下的（开浏览器、下载、解压、覆盖、重启）
    # 全得自己来。现在整条链子在这几个方法里走完：
    #
    #     _show_update  摆出弹窗
    #     _start_update 下载 -> 校验 -> 解包 -> 交班给 .bat
    #     _finish_update 看到"重启就绪"再退出，把位置让给新版本
    # ==================================================================
    def _show_update(self, info: update.UpdateInfo) -> None:
        if self._update_dialog is not None and self._update_dialog.isVisible():
            self._update_dialog.raise_()
            self._update_dialog.activateWindow()
            return

        dlg = UpdateDialog(info, parent=self)
        dlg.install_requested.connect(lambda: self._start_update(info))
        dlg.cancel_requested.connect(self._cancel_update)
        dlg.closed.connect(self._forget_update_dialog)
        self._update_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _forget_update_dialog(self) -> None:
        self._update_dialog = None

    def _cancel_update(self) -> None:
        """用户不想更了。置位开关，线程下一次写块的时候自己收手。"""
        self._update_cancel.set()
        if self._update_dialog is not None:
            self._update_dialog.fail("", cancelled=True)

    def _fail_update(self, message: str, cancelled: bool = False) -> None:
        if cancelled:
            bus.system("已取消更新")
        else:
            bus.error(f"更新：{message}")
        if self._update_dialog is not None:
            self._update_dialog.fail(message, cancelled=cancelled)

    def _start_update(self, info: update.UpdateInfo) -> None:
        """下载 -> 校验 -> 解包 -> 交班。全程在后台线程，界面不卡。"""
        if not info.url:
            self._fail_update("服务端没给安装包地址")
            return
        if not update.can_self_update():
            # 源码运行时"当前程序"是 python.exe，换了没意义。别让用户
            # 干等半天才发现。地址给出来，他自己下。
            self._fail_update(
                "当前不是以安装包方式运行的，没法自动更新。\n"
                f"安装包地址：{info.url}"
            )
            return

        self._update_cancel = threading.Event()
        cancel = self._update_cancel
        archive = update.update_cache_dir() / f"Canoe-{info.latest}-win64.zip"

        def work(on_progress=None):
            # 1) 下载 + 摘要校验（校验不过 update.download 自己会把文件删掉）
            update.download(
                info.url, archive,
                on_progress=on_progress,
                expected_sha256=info.sha256,
                expected_size=info.size,
                cancelled=cancel,
            )
            # 2) 解出 exe 放到当前程序旁边。**写权限在这里就会撞出来** ——
            #    宁可现在失败，也别等退了程序才发现换不了。
            new_exe = update.prepare_update(archive)
            # 3) 交班：写 .bat，等我们退出后由系统来完成替换和重启
            update.install_and_restart(new_exe)
            archive.unlink(missing_ok=True)

        def on_ok(_result) -> None:
            if self._update_dialog is not None:
                self._update_dialog.set_stage("restarting", "更新就绪，正在重启…")
            # 停一下让这句话画出来，不然窗口一闪就没了
            QTimer.singleShot(500, self._finish_update)

        def on_err(code: str, message: str) -> None:
            self._fail_update(message, cancelled=(code == "cancelled"))

        Worker(work).run_with(on_ok, on_err, on_progress=self._on_update_progress)

    def _on_update_progress(self, done: int, total: int) -> None:
        if self._update_dialog is not None:
            self._update_dialog.set_progress(done, total)

    def _finish_update(self) -> None:
        """退出自己，把位置让给门外那个 .bat。

        ★ 不能直接 os._exit：系统代理得还原、内核得停 —— 少这一步，
          新版本启动之前用户是断网状态，TUN 模式还会留下占着端口的残留。
          `QApplication.quit()` 会触发 aboutToQuit -> CanoeApp.shutdown()，
          那条路本来就是干这个的（app.py 里接的）。
        """
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:                                   # pragma: no cover - 兜底
            self.close()

    def _do_tcping(self) -> None:
        """测本机到**当前节点**的 TCP 握手延迟。

        测的是它到节点服务器那台机器的握手延迟，也就是"这条路通不通"。
        """
        self.tcping_btn.setEnabled(False)

        def work():
            text = self._sub_text or self._fetch_subscription()
            found = self._parse_subscription(text)
            index = self._active if 0 <= self._active < len(found) else 0
            link = found[index] if found else None
            if link is None:
                raise SubscriptionError("当前没有可用节点")
            ob = link.outbound
            return tcping(str(ob.get("server")), int(ob.get("server_port") or 0), 4)

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

        # 靶子按线路方向挑：回国模式下 gstatic 是**直连**的，拿它测等于
        # 没测代理（节点挂了也是绿的）。见 nettest.target_url。
        Worker(url_test, port, target_url(self._opts.route_mode)).run_with(on_ok, on_err)

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
        # 离舟就把内存里那份订阅抹掉。密钥也随令牌一起没了 ——
        # 想再用就得重新登舟，重新过一遍服务端。
        self._wipe_subscription()
        session.logout()
        self.logged_out.emit()
        # 吊销服务端令牌（幂等：没令牌 / 已失效都返回 ok）
        Worker(api.logout, sid or None).run_with()

    def force_close(self) -> None:
        """真的关掉（退出流程用）。

        默认关窗只是收进托盘，正在跑的事务一样不能被打断；
        退出、提权重启这类场合才需要它直接关。
        """
        self._force_close = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关窗。

        有托盘时**只是收起来** —— 代理继续跑，要退出得走托盘右键菜单。
        没托盘（或正在真退出）才走下面这套清理。

        清理时必须看 `kernel.running`：刚点完启航、内核正在起的那一两秒里
        `session.sailing` 还是 False，只看它会漏掉这时候关窗的情况。
        """
        if self.close_to_tray and not self._force_close:
            event.ignore()
            self.hide()
            bus.system("已收进托盘，右键托盘图标可以退出")
            return

        self._log_timer.stop()
        self._hb_timer.stop()
        if session.sailing or kernel.running or sysproxy.has_backup():
            self._dock()
        super().closeEvent(event)

        if self._force_close:
            # ★ 强制关 = **真的要退出**，不只是把窗口收掉。
            #
            # 少这一步的后果（真机上出过）：托盘在的时候 app.py 设了
            # setQuitOnLastWindowClosed(False)，窗口关掉进程照样活着 ——
            # 变成个只有托盘图标、没有窗口的僵尸。
            # 而"以管理员身份重启"正是走 force_close 的：新进程起来了、
            # 旧进程不退，于是**两个实例**抢同一个 20818 端口和同一张
            # canoe 网卡，启航永远失败；用户关掉一个还有一个，报成
            # "点 × 窗口不消失"。
            QApplication.quit()

    def start_with_node(self, username: str, node_name: str = "") -> None:
        """登录成功后进入主界面。

        节点名现在得从订阅里解出来，登录响应里没有 —— 所以先空着，
        后台拉一次订阅补上。拉不到也不拦着进主界面：用户至少能看到
        "为什么没有节点"，而不是卡在登录页。
        """
        session.login(username, node_name)
        self._session_id = ""
        self._revision = ""
        self._sub_text = ""
        self._links = []
        self._active = 0
        self._node_name = ""
        self.lights.set_nodes([])
        self.refresh()
        Worker(self._refresh_after_login).run_with(
            self._on_subscription_ready, self._on_subscription_failed
        )

    def _refresh_after_login(self):
        """登录后立刻拉一次订阅，把节点名和那排灯显示出来。

        顺手把线路方向也带回去 —— 它跟订阅走同一份响应（/api/subscription
        里有 route_mode），登舟之后不用再单独问一次。
        """
        resp, text = api.subscription_text()
        return resp.revision, text, resp.route_mode

    def _on_subscription_ready(self, payload) -> None:
        revision, text, route_mode = payload
        self._revision = revision
        self._apply_route_mode(route_mode)
        if not self._apply_subscription(text):
            self._wipe_subscription()
            bus.error("服务端没有下发订阅，请联系管理员")

    def _on_subscription_failed(self, code: str, message: str) -> None:
        bus.error(f"订阅：{message}")
        if code in ("unauthorized", "banned", "expired", "bad_credentials"):
            self.logged_out.emit()
