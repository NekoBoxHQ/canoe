"""自绘的小控件。

QSS 画不出「蓝色方块里的白色对勾」和「圆环里的圆点」这类东西 —— 除非引入
图片资源。这里干脆用 QPainter 直接画，效果完全可控，也不增加打包体积。

    CheckBox     蓝色圆角方块 + 白勾
    RadioButton  圆环 + 实心圆点
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QRadioButton

from canoe_core import Palette as P

BOX = 17          # 指示器边长
GAP = 9           # 指示器与文字间距


def _text_rect(widget) -> QRectF:
    return QRectF(BOX + GAP, 0, widget.width() - BOX - GAP, widget.height())


def _text_color(widget) -> QColor:
    if not widget.isEnabled():
        return QColor(P.TEXT_FAINT)
    return QColor(P.TEXT) if widget.isChecked() else QColor(P.TEXT_DIM)


class CheckBox(QCheckBox):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(hint.width() + BOX + GAP)
        return hint

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        box = QRectF(0.5, (self.height() - BOX) / 2 + 0.5, BOX, BOX)

        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(P.ACCENT if self.isEnabled() else P.CARD_LINE))
            p.drawRoundedRect(box, 4.5, 4.5)
            p.setPen(QPen(QColor("#FFFFFF"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            tick = QPainterPath()
            tick.moveTo(box.left() + BOX * 0.26, box.center().y() + BOX * 0.02)
            tick.lineTo(box.left() + BOX * 0.44, box.center().y() + BOX * 0.22)
            tick.lineTo(box.left() + BOX * 0.76, box.center().y() - BOX * 0.24)
            p.drawPath(tick)
        else:
            p.setPen(QPen(QColor(P.CARD_LINE), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 4.5, 4.5)

        p.setPen(_text_color(self))
        p.drawText(_text_rect(self), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.end()


class RadioButton(QRadioButton):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(hint.width() + BOX + GAP)
        return hint

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        box = QRectF(0.5, (self.height() - BOX) / 2 + 0.5, BOX, BOX)

        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(P.ACCENT if self.isEnabled() else P.CARD_LINE))
            p.drawEllipse(box)
            p.setBrush(QColor("#FFFFFF"))
            inner = box.adjusted(BOX * 0.28, BOX * 0.28, -BOX * 0.28, -BOX * 0.28)
            p.drawEllipse(inner)
        else:
            p.setPen(QPen(QColor(P.CARD_LINE), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(box)

        p.setPen(_text_color(self))
        p.drawText(_text_rect(self), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.end()
