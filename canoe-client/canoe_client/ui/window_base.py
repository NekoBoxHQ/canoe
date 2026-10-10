"""无边框窗口基类 —— 自绘圆角 + 标题栏 + 拖动。

美化稿里的窗口是圆角、带蓝色描边、标题栏上有小帆船和 – × 的，
所以这里去掉系统边框自己画：

    FramelessWindow
      ├─ _TitleBar   小徽标 + 「轻舟 · 轻舟已过万重山」+ – ×
      └─ self.body   各页面自己的内容（用 self.body_layout 往里塞）

子类只需要实现 `paint_background(painter, w, h)` 来铺底图。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from canoe_core import BRAND_CN, SLOGAN_CN, Palette as P

from . import artwork as A

TITLEBAR_H = 38
CORNER_RADIUS = 14

#: 描边颜色，稍微透一点，免得太硬
_BORDER = QColor(46, 106, 190, 150)


class _TitleBar(QWidget):
    """自绘标题栏。按住空白处可以拖动窗口。"""

    def __init__(self, window: "FramelessWindow") -> None:
        super().__init__(window)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLEBAR_H)
        self._window = window
        self._drag: QPointF | None = None
        self.setCursor(Qt.ArrowCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(8)

        mark = QLabel()
        mark.setPixmap(A.app_mark(18))
        mark.setFixedSize(18, 18)
        lay.addWidget(mark)

        self.name = QLabel(BRAND_CN)
        self.name.setObjectName("WinTitle")
        lay.addWidget(self.name)

        self.sub = QLabel(f"· {SLOGAN_CN}")
        self.sub.setObjectName("WinSubtitle")
        lay.addWidget(self.sub)

        lay.addStretch(1)

        self.min_btn = self._win_button("minus", "最小化")
        self.min_btn.clicked.connect(self._window.showMinimized)
        lay.addWidget(self.min_btn)

        self.close_btn = self._win_button("close", "关闭", danger=True)
        self.close_btn.clicked.connect(self._window.close)
        lay.addWidget(self.close_btn)

    def set_title(self, title: str, subtitle: str | None = None) -> None:
        """换掉标题栏上的字。弹窗用 —— 顶着「轻舟 · 轻舟已过万重山」说
        「有新版本」会让人找不到重点。"""
        self.name.setText(title)
        self.sub.setText(f"· {subtitle}" if subtitle else "")
        self.sub.setVisible(bool(subtitle))

    def _win_button(self, icon_name: str, tip: str, danger: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("WinBtnDanger" if danger else "WinBtn")
        btn.setIcon(A.icon(icon_name, 14, P.TEXT_DIM))
        btn.setFixedSize(34, 24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        return btn

    # -- 拖动 ----------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition() - QPointF(self._window.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self._window.move((event.globalPosition() - self._drag).toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        super().mouseReleaseEvent(event)


#: 窗口宽度 —— 两个窗口一样宽。
WINDOW_W = 420

#: 主界面窗口的高度。这个数是**内容撑出来的**（main_view._build 里 GAP 那一串，
#: 自然高度 577 + 底边 BOTTOM_PAD 14 = 592），test_gui 有一条盯着
#: 「内容正好填满窗口」，所以它降不下去。
WINDOW_H = 592

#: 登录 / 注册那个窗口的高度 —— 比主界面**矮 89px**。
#:
#: 2026-10-10 去掉帆船徽标之后，用户要求"底下的空间往上缩，整个高度变矮"，
#: 否则删徽标没意义。矮的这几十 px 是从卡片下面那条山水带（auth_view 的
#: SCENE_BAND）里让出来的，不是从内容里抠的。
#:
#: ⚠ 代价：登录成功切到主界面时，窗口会长高 59px。想让两边一样高就得把主界面
#:   也压到 533 —— 那要把它的内容砍掉 59px，而它的间距是用户定死的 12px。
AUTH_WINDOW_H = 503


class FramelessWindow(QWidget):
    """圆角无边框窗口。子类用 `self.body_layout` 摆内容。"""

    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(BRAND_CN)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(width, height)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = _TitleBar(self)
        outer.addWidget(self.titlebar)

        self.body = QWidget()
        self.body.setObjectName("Body")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        outer.addWidget(self.body, 1)

    # -- 子类实现 ------------------------------------------------------
    def paint_background(self, painter: QPainter, width: float, height: float) -> None:
        """铺整窗底图。

        ★ 有 `assets/ocean_bg.png` 就用那张图铺满；没有就退回代码画的夜色。
          素材没到位时不会开天窗，这条是刻意的（见 artwork.BG_IMAGE_NAME）。
        """
        if A.paint_background_image(painter, width, height):
            return
        A.paint_night(painter, width, height, horizon=0.72)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        rounded = QPainterPath()
        rounded.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)

        # 底图裁进圆角里
        p.save()
        p.setClipPath(rounded)
        self.paint_background(p, float(self.width()), float(self.height()))
        p.restore()

        # 蓝色描边
        p.setPen(QPen(_BORDER, 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawPath(rounded)
        p.end()
