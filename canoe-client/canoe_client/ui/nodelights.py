"""主界面底部那排「节点灯」。

一个灯 = 一个能用的节点：亮着就是有，暗着就是没有。一个灯都不亮 =
订阅里没有能解析出来的节点（服务端没给，或者链接全认不出来）。

点一下切到那个节点，在航的话当场换过去重连。**不写字** —— 这是刻意的：
节点名已经在上面那行大字里了，底下再标一遍既挤又重复，用户要的就是
"像灯一样"。鼠标停上去会有 tooltip 说是哪个节点（只在悬停时出现，
平时一点痕迹都没有）。

关于"当前用的是哪个"：这排灯里只有一个是**正在用**的。都画成一样的话
点起来是盲的 —— 你不知道现在在哪盏上。所以正在用的那盏多一个白色灯芯
（像点亮的灯泡），其余亮着的就是纯色块。仍然没有一个字。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from canoe_core import Palette as P

#: 固定 6 个位置。灯的数量不跟着节点数变 —— 那样窗口底部会一会儿
#: 宽一会儿窄。固定 6 个，有几个节点就亮几个。
SLOTS = 6

BOX = 22                 # 单个灯的边长
GAP = 12                 # 灯与灯的间距
PAD = 16                 # 灯和外框之间
HEIGHT = 46              # 整排的高度


class NodeLights(QWidget):
    """点亮几盏、点哪一盏、切到哪一盏。"""

    #: 位置数量。跟模块级那个是同一个值，挂到类上是为了让外部能
    #: `NodeLights.SLOTS` 地引用，不用再去 import 模块常量。
    SLOTS = SLOTS

    #: 用户点了第 index 盏（从 0 数）。只在点的是"亮着的、且不是当前"时发。
    node_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        self._count = 0       # 有几个能用的节点
        self._active = -1     # 正在用的是第几个（-1 = 一个都没有）
        self._names: list[str] = []

    # ------------------------------------------------------------------
    def set_nodes(self, names: list[str], active: int = -1) -> None:
        """更新这排灯。

        names 超过 6 个时只认前 6 个 —— 位置就这么多。多出来的不算丢：
        它们还在订阅里，只是这排灯代表不了（节点数由管理员控制，
        6 个以上本来就该是"该加灯了"的信号，不该偷偷挤进来）。
        """
        self._names = list(names)[:SLOTS]
        self._count = len(self._names)
        self._active = active if 0 <= active < self._count else (-1 if not self._count else 0)
        self._refresh_tooltip()
        self.update()

    def count(self) -> int:
        return self._count

    def active(self) -> int:
        return self._active

    def name_at(self, index: int) -> str:
        return self._names[index] if 0 <= index < len(self._names) else ""

    # ------------------------------------------------------------------
    def _boxes(self) -> list[QRectF]:
        """6 个灯的位置。整个排面居中 —— 灯数固定，所以位置也是固定的。"""
        total = SLOTS * BOX + (SLOTS - 1) * GAP
        x = (self.width() - total) / 2
        y = (self.height() - BOX) / 2
        return [
            QRectF(x + i * (BOX + GAP), y, BOX, BOX)
            for i in range(SLOTS)
        ]

    def _index_at(self, x: float) -> int | None:
        for i, box in enumerate(self._boxes()):
            # 命中范围放宽到半个间距 —— 22px 的方块按像素点有点费劲
            if box.left() - GAP / 2 <= x <= box.right() + GAP / 2:
                return i
        return None

    def _refresh_tooltip(self) -> None:
        if not self._count:
            self.setToolTip("订阅里还没有节点")
            return
        lines = [
            f"{i + 1}. {self._names[i]}" + ("　（当前）" if i == self._active else "")
            for i in range(self._count)
        ]
        if self._count < SLOTS:
            lines.append("点一下切换节点")
        self.setToolTip("\n".join(lines))

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # 外框：一颗胶囊。底色压得很暗，让上面的灯自己发光。
        outer = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(P.CARD_LINE), 1))
        p.setBrush(QColor(13, 27, 51, 150))       # CARD 半透明
        p.drawRoundedRect(outer, outer.height() / 2, outer.height() / 2)

        boxes = self._boxes()
        for i, box in enumerate(boxes):
            if i < self._count:
                self._paint_lit(p, box, i == self._active)
            else:
                self._paint_dark(p, box)
        p.end()

    @staticmethod
    def _paint_lit(p: QPainter, box: QRectF, active: bool) -> None:
        """亮着的灯：绿->青渐变 + 一圈外辉。"""
        # 外辉：几层逐渐变淡的放大方块，不引模糊也像在发光
        for step in (5, 3, 1):
            glow = box.adjusted(-step, -step, step, step)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(34, 197, 94, 18 if active else 10))
            p.drawRoundedRect(glow, 7 + step, 7 + step)

        grad = QLinearGradient(box.topLeft(), box.bottomRight())
        if active:
            grad.setColorAt(0.0, QColor(P.GREEN))
            grad.setColorAt(1.0, QColor(P.CYAN))
        else:
            # 没在用的灯稍微压一点亮度 —— 一眼能看出当前在哪盏
            c1, c2 = QColor(P.GREEN), QColor(P.CYAN)
            c1.setAlpha(170)
            c2.setAlpha(170)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)

        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(box, 7, 7)

        if active:
            # 白色灯芯
            core = box.adjusted(BOX * 0.32, BOX * 0.32, -BOX * 0.32, -BOX * 0.32)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(core)

    @staticmethod
    def _paint_dark(p: QPainter, box: QRectF) -> None:
        """没节点占的位子：暗格子，看得见但不着。"""
        p.setPen(QPen(QColor(P.CARD_LINE_SOFT), 1))
        p.setBrush(QColor(21, 40, 70, 120))
        p.drawRoundedRect(box, 7, 7)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() != Qt.LeftButton:
            return
        index = self._index_at(event.position().x())
        # 点暗格子、点当前这盏，都当没点 —— 不弹提示、不报错，灯不亮就是
        # 没什么可点的。
        if index is not None and index < self._count and index != self._active:
            self.node_selected.emit(index)
