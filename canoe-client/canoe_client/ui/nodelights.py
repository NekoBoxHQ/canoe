"""主界面底部那排「节点灯」。

一个灯 = 一个能用的节点：亮着就是有，暗着就是没有。一个灯都不亮 =
订阅里没有能解析出来的节点（服务端没给，或者链接全认不出来）。

点一下切到那个节点，在航的话当场换过去重连。**不写字** —— 这是刻意的：
节点名已经在上面那行大字里了，底下再标一遍既挤又重复，用户要的就是
"像灯一样"。

**也没有 tooltip**：鼠标扫过去弹一串节点名，一样是"显示"，一样碍事。
想知道哪盏是哪个，点一下，上面那行字就换了。

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
PAD = 18                 # 最边上那盏离外框多远（其余间距由格宽均分）
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
        self.update()

    def count(self) -> int:
        return self._count

    def active(self) -> int:
        return self._active

    def name_at(self, index: int) -> str:
        return self._names[index] if 0 <= index < len(self._names) else ""

    # ------------------------------------------------------------------
    def _cell(self) -> tuple[float, float]:
        """每盏灯占一格：返回 (第一格左边缘, 每格宽度)。

        格子是**均分整个排面**的，灯在格子正中 —— 所以间距是"平均分配"
        的。第一版用的是固定间距 + 整排居中，结果 6 盏挤在中间、左右各
        空一大截，看着很散（用户反馈的）。
        """
        width = max(self.width(), SLOTS * BOX)     # 窄到放不下就别硬撑
        usable = max(width - 2 * PAD, SLOTS * BOX)
        return (width - usable) / 2, usable / SLOTS

    def _boxes(self) -> list[QRectF]:
        x0, step = self._cell()
        y = (self.height() - BOX) / 2
        return [
            QRectF(x0 + i * step + (step - BOX) / 2, y, BOX, BOX)
            for i in range(SLOTS)
        ]

    def _index_at(self, x: float) -> int | None:
        """按格子判定，不用按方块本身 —— 22px 的方块要鼠标精确点太费劲。"""
        x0, step = self._cell()
        if step <= 0 or x < x0 or x > x0 + step * SLOTS:
            return None
        return min(int((x - x0) / step), SLOTS - 1)

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
