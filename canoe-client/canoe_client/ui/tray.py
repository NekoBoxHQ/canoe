"""任务栏托盘。

关掉窗口不等于退出 —— 关窗只是把界面收起来，代理照常跑。
真正退出要**右键托盘图标 -> 退出**。

为什么这么做：代理工具关窗就断掉是很反直觉的（用户只是想腾个地方），
但让"关窗"直接退出又会让不知情的人以为程序还在。收进托盘是两头都占。

托盘不可用时（少见，比如某些精简版 Windows）自动退化成普通行为：
关窗就是关窗，照旧还原代理、停内核。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from canoe_core import BRAND_CN, SLOGAN_CN


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
        self.icon = QSystemTrayIcon(icon or QIcon(), self)
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

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def notify(self, title: str, message: str) -> None:
        """气泡提示。不支持托盘的平台上是空操作。"""
        if self.icon.isVisible() and QSystemTrayIcon.supportsMessages():
            self.icon.showMessage(title, message, self.icon.icon(), 3000)
