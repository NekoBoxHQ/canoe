"""任务栏托盘。

关掉窗口不等于退出 —— 关窗只是把界面收起来，代理照常跑。
真正退出要**右键托盘图标 -> 退出**。

为什么这么做：代理工具关窗就断掉是很反直觉的（用户只是想腾个地方），
但让"关窗"直接退出又会让不知情的人以为程序还在。收进托盘是两头都占。

托盘不可用时（少见，比如某些精简版 Windows）自动退化成普通行为：
关窗就是关窗，照旧还原代理、停内核。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from canoe_core import BRAND_CN, SLOGAN_CN

#: TUN 开着时钉在图标右上角的那颗红点。
#:
#: 为什么是"点一下红点"而不是换一整张图标：任务栏上只有 16px，
#: 换整张图用户根本分不出来；右上角一颗红点是通行写法，一眼就懂。
DOT_COLOR = QColor("#E5484D")
#: 描一圈深色边 —— 图标本身是深蓝的，红点直接压上去会发糊
DOT_EDGE = QColor(0, 0, 0, 160)

#: 点哪几张。托盘会按 DPI 和任务栏设置挑，索性全给上。
_DOT_SIZES = (16, 20, 24, 32, 48, 64, 128)

#: 红点占图标的比例。
#:
#: 这个数调过两轮：0.46 那版像图标上破了个洞，0.34 那版用户说"太小了
#: 看不到"。0.44 是任务栏 16px 下还能一眼看见、又不至于盖住船帆的位置。
#: **别按大屏幕上的观感调** —— 托盘图标在 100% 缩放下真的只有 16px。
_DOT_RATIO = 0.44
#: 圆心离右上角留这点空隙，免得被任务栏边框切掉半颗
_DOT_PAD = 0.02


def with_dot(icon: QIcon) -> QIcon:
    """在图标右上角点一颗红点，返回新图标（原图标不动）。"""
    out = QIcon()
    seen: set[int] = set()
    for want in _DOT_SIZES:
        pm = icon.pixmap(want, want)
        if pm.isNull():
            continue
        # ⚠ 几何要按**拿到的那张的实测尺寸**算，不能按想要的那个数。
        #   QIcon 对超过源图的尺寸不会放大，只是把已有的最大那张给你：
        #   源图 32px 时要 48 拿回来的还是 32。照 48 算圆心，红点会落到
        #   画布外面；更阴的是那张"没点上红点的 32"会**把前面点好的那张
        #   覆盖掉** —— 最后拿到的图标干干净净，像是压根没画。
        #   （踩过，被测试逮住的。）
        size = pm.width()
        if size in seen:
            continue
        seen.add(size)

        pm = QPixmap(pm)          # 拷一份，别改到 QIcon 里缓存的那张
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        d = size * _DOT_RATIO
        pad = size * _DOT_PAD
        p.setPen(QPen(DOT_EDGE, max(0.8, size * 0.045)))
        p.setBrush(DOT_COLOR)
        # 圆心贴在右上角、正好不越界
        p.drawEllipse(QPointF(size - d / 2 - pad, d / 2 + pad), d / 2, d / 2)
        p.end()
        out.addPixmap(pm)
    return out if not out.isNull() else icon


def tray_available() -> bool:
    return QSystemTrayIcon.isSystemTrayAvailable()


class Tray(QObject):
    """托盘图标 + 右键菜单。

    只管"发信号"，具体做什么交给 app.py —— 这样测试里不用起真托盘。
    """

    show_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._base_icon = icon or QIcon()
        self._dot_icon: QIcon | None = None
        self._tun = False
        self.icon = QSystemTrayIcon(self._base_icon, self)
        self.icon.setToolTip(f"{BRAND_CN} · {SLOGAN_CN}")

        menu = QMenu()
        self._show_action = menu.addAction("显示主界面")
        self._show_action.triggered.connect(self.show_requested.emit)
        menu.addSeparator()
        self._quit_action = menu.addAction("退出")
        self._quit_action.triggered.connect(self.quit_requested.emit)

        # 菜单得挂在托盘上，否则会被当临时对象回收掉，右键什么都不弹
        self._menu = menu
        self.icon.setContextMenu(menu)

        # 双击图标 = 把界面叫回来（和大多数托盘程序一致）
        self.icon.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_requested.emit()

    def set_tun(self, active: bool) -> None:
        """TUN 起效时给托盘图标点一颗红点。

        TUN 接管的是**全部**流量，而用户关掉窗口之后程序就缩在托盘里 ——
        得让他一眼看出来"现在不只是挂了个系统代理"。这是它存在的意义。
        """
        active = bool(active)
        if active == self._tun:
            return
        self._tun = active
        if active:
            if self._dot_icon is None:
                self._dot_icon = with_dot(self._base_icon)
            self.icon.setIcon(self._dot_icon)
            self.icon.setToolTip(f"{BRAND_CN} · {SLOGAN_CN}（TUN 模式 · 接管全部流量）")
        else:
            self.icon.setIcon(self._base_icon)
            self.icon.setToolTip(f"{BRAND_CN} · {SLOGAN_CN}")

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def notify(self, title: str, message: str) -> None:
        """气泡提示。不支持托盘的平台上是空操作。"""
        if self.icon.isVisible() and QSystemTrayIcon.supportsMessages():
            self.icon.showMessage(title, message, self.icon.icon(), 3000)
