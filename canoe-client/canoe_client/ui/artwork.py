"""轻舟界面美术 —— 夜色山水 + 帆船 Logo + 线性图标。

**全部用 QPainter 矢量绘制，不依赖任何图片资源。**
好处：任意窗口尺寸都清晰、打包体积为零、配色跟着 Palette 走。

三块内容：
    paint_night()      夜色山水背景（渐变天空 / 月亮 / 三层山脊 / 水面倒影 / 帆船）
    sailboat_logo()    轻舟的帆船徽标
    icon()             界面用的线性图标（用户、锁、眼睛、箭头、刷新、终端、链接、文档…）
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from canoe_core import Palette as P

from ..config import ASSETS_DIR

# --------------------------------------------------------------------------
# 背景：可选底图（有就用图，没有就代码画）
# --------------------------------------------------------------------------

#: 放了这张图就用它铺满整窗：`assets/ocean_bg.png`。
#: 打包会自动带上（canoe.spec 里 assets/*.png 是通配的，加素材不用改 spec）。
#:
#: 为什么两套都留着：
#:   · 代码画的那套（paint_night）—— 任意窗口尺寸/缩放都锐利、零体积，
#:     两个页面还能各自调构图（登录页地平线 0.885、主界面 0.72）。
#:   · 位图 —— 能放照片、网图，好看的上限高得多，但放大就糊、构图写死。
#: 谁合适用谁；素材没到位时自动退回代码那套，不会开天窗。
BG_IMAGE_NAME = "ocean_bg.png"

_bg_pixmap: QPixmap | None = None
_bg_loaded = False


def background_pixmap() -> QPixmap | None:
    """读一次底图并缓存（每次重绘都读盘太浪费）。没有就返回 None。

    ⚠ 只读一次：跑起来之后往 assets/ 里丢图不会生效，得重启。
    """
    global _bg_pixmap, _bg_loaded
    if not _bg_loaded:
        _bg_loaded = True
        path = ASSETS_DIR / BG_IMAGE_NAME
        pm = QPixmap(str(path)) if path.is_file() else QPixmap()
        _bg_pixmap = None if pm.isNull() else pm
    return _bg_pixmap


def paint_background_image(p: QPainter, width: float, height: float) -> bool:
    """有底图就铺满整窗并返回 True；没有返回 False（调用方退回代码画的那套）。

    铺法是**等比放大到铺满、居中再裁掉多出来的边**（cover），不是拉伸 ——
    拉伸（IgnoreAspectRatio）在比例不对的时候会把月亮压成椭圆。
    图正好是窗口那个比例（420:621）时，两种铺法结果一样。
    """
    pm = background_pixmap()
    if pm is None:
        return False
    scaled = pm.scaled(
        int(round(width)), int(round(height)),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (scaled.width() - width) / 2.0
    y = (scaled.height() - height) / 2.0
    p.drawPixmap(QRectF(0, 0, width, height), scaled,
                 QRectF(x, y, width, height))
    return True


# --------------------------------------------------------------------------
# 背景：夜色山水
# --------------------------------------------------------------------------


def _draw_stars(p: QPainter, w: float, sky_bottom: float, count: int = 44) -> None:
    """天空里的星星。

    ⚠ 坐标用**固定公式**算（sin 取小数部分），不用 random —— 每次渲染必须
      一模一样，截图验收和测试要可比（山脊顶点同一个道理）。
    越靠地平线越淡（大气感），月亮附近不画（会被光晕吃掉，也免得像噪点）。
    """
    p.setPen(Qt.NoPen)
    for i in range(count):
        fx = (math.sin(i * 12.9898 + 4.1) * 43758.5453) % 1.0
        fy = (math.sin(i * 78.233 + 1.7) * 43758.5453) % 1.0
        x = fx * w
        y = fy * sky_bottom * 0.9
        # 靠地平线那几颗淡下去，天顶那几颗亮 —— 不然整片天糊成砂纸
        depth = 1.0 - (y / sky_bottom if sky_bottom else 0.0)
        alpha = int(22 + 96 * depth * fy)
        if alpha < 12:
            continue
        r = 0.6 + 1.2 * fy
        p.setBrush(QColor(228, 240, 255, alpha))
        p.drawEllipse(QPointF(x, y), r, r)


def _ridge_edge(w: float, base_y: float, height: float,
                points: tuple[tuple[float, float], ...]) -> QPainterPath:
    """只取山脊**上沿**那条线（不含底边）—— 用来打月光。"""
    path = QPainterPath()
    for i, (x, lift) in enumerate(points):
        pt = QPointF(w * x, base_y - height * lift)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    return path

#: 远 / 中 / 近三层山脊。每项是 (整幅高度的占比, 顶点数据)。
#: 山都不高 —— 美化稿里山脊只比地平线冒出一小截，不能顶到卡片上去。
#: 顶点是 (x 比例, 相对该层最高点的抬起比例)，手写死的 ——
#: 不用随机数，保证每次渲染完全一致（截图验收和测试都要可比）。
_RIDGES: tuple[tuple[float, tuple[tuple[float, float], ...]], ...] = (
    # 远山：最高、起伏最大
    (0.115, (
        (0.00, 0.34), (0.05, 0.58), (0.09, 0.42), (0.14, 0.72), (0.19, 0.46),
        (0.24, 0.30), (0.31, 0.52), (0.37, 0.78), (0.42, 0.50), (0.48, 0.68),
        (0.54, 0.40), (0.60, 0.62), (0.66, 0.82), (0.71, 0.52), (0.78, 0.36),
        (0.84, 0.60), (0.90, 0.44), (0.95, 0.66), (1.00, 0.40),
    )),
    # 中山
    (0.082, (
        (0.00, 0.22), (0.08, 0.44), (0.15, 0.26), (0.22, 0.50), (0.30, 0.32),
        (0.38, 0.56), (0.45, 0.34), (0.53, 0.52), (0.61, 0.30), (0.69, 0.48),
        (0.77, 0.28), (0.85, 0.46), (0.93, 0.30), (1.00, 0.42),
    )),
    # 近山：最矮、最平缓
    (0.055, (
        (0.00, 0.14), (0.10, 0.26), (0.20, 0.16), (0.31, 0.30), (0.42, 0.18),
        (0.54, 0.28), (0.66, 0.15), (0.78, 0.26), (0.89, 0.17), (1.00, 0.24),
    )),
)


def _ridge_path(width: float, base_y: float, height: float,
                points: tuple[tuple[float, float], ...]) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(0.0, base_y)
    for x, lift in points:
        path.lineTo(width * x, base_y - height * lift)
    path.lineTo(width, base_y)
    path.closeSubpath()
    return path


def paint_night(p: QPainter, width: float, height: float, *,
                horizon: float = 0.60,
                mountain: float = 1.0,
                boat: bool = True,
                moon: bool = True) -> None:
    """画一整幅夜色山水。

    horizon  —— 地平线在整幅图里的纵向位置（0 顶 1 底）
    mountain —— 山的高矮倍率，0 表示只留水面
    """
    p.setRenderHint(QPainter.Antialiasing, True)
    w, h = float(width), float(height)
    sky_bottom = h * horizon

    # --- 天空 ---
    sky = QLinearGradient(0, 0, 0, sky_bottom)
    sky.setColorAt(0.00, QColor(P.NIGHT_TOP))
    sky.setColorAt(0.55, QColor(P.NIGHT_MID))
    sky.setColorAt(1.00, QColor(P.NIGHT_HORIZON))
    p.fillRect(QRectF(0, 0, w, sky_bottom + 1), sky)

    # --- 星星 ---
    _draw_stars(p, w, sky_bottom)

    # --- 月亮 + 光晕 ---
    # 压在**地平线附近**（美化稿里月亮就悬在山脊上方），不要挂到天顶去，
    # 否则会正好跑到品牌字后面。
    mx = w * 0.72
    my = sky_bottom * 0.90
    if moon:
        radius = max(10.0, min(w, h) * 0.036)
        p.setPen(Qt.NoPen)
        # 两层晕：外层又宽又淡（整片天有一点点亮），内层紧贴着月亮
        for span, a1, a2 in ((radius * 8.0, 26, 0), (radius * 4.6, 64, 22)):
            glow = QRadialGradient(QPointF(mx, my), span)
            glow.setColorAt(0.00, QColor(228, 238, 255, a1))
            glow.setColorAt(0.45, QColor(170, 205, 255, a2))
            glow.setColorAt(1.00, QColor(120, 170, 255, 0))
            p.setBrush(QBrush(glow))
            p.drawEllipse(QPointF(mx, my), span, span)
        p.setBrush(QColor(P.MOON))
        p.drawEllipse(QPointF(mx, my), radius, radius)

    # --- 三层山脊 ---
    if mountain > 0:
        for idx, ((lift, points), color) in enumerate(
            zip(_RIDGES, (P.MOUNT_FAR, P.MOUNT_MID, P.MOUNT_NEAR))
        ):
            top = h * lift * mountain
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawPath(_ridge_path(w, sky_bottom, top, points))
            # 脊线上压一道月光。有这道边，三层山才"立"得起来；没有的话
            # 三块纯色糊成一片，看着闷（用户要"背景再舒服点"）。
            # 越远越淡 —— 空气透视，近处最亮。
            p.setPen(QPen(QColor(158, 196, 244, (34, 56, 84)[idx]), 1.1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(_ridge_edge(w, sky_bottom, top, points))

    # --- 水面 ---
    water_top = sky_bottom
    water = QLinearGradient(0, water_top, 0, h)
    water.setColorAt(0.00, QColor(P.NIGHT_HORIZON))
    water.setColorAt(0.28, QColor(P.WATER))
    water.setColorAt(1.00, QColor(P.WATER_DEEP))
    p.setPen(Qt.NoPen)
    p.fillRect(QRectF(0, water_top, w, h - water_top + 1), water)

    if h - water_top < 6:
        return

    # 月光在水面的反光柱
    if moon:
        col = QLinearGradient(0, water_top, 0, h)
        col.setColorAt(0.0, QColor(200, 226, 255, 46))
        col.setColorAt(1.0, QColor(200, 226, 255, 0))
        band = max(6.0, w * 0.055)
        p.setBrush(QBrush(col))
        p.drawRect(QRectF(mx - band / 2, water_top, band, h - water_top))

    # 水波纹：越往下越淡、越疏
    for i in range(14):
        t = i / 13.0
        y = water_top + (h - water_top) * (t ** 1.35)
        alpha = int(42 * (1.0 - t) ** 1.6) + 4
        p.setPen(QPen(QColor(150, 190, 240, alpha), 1.0))
        left = w * (0.02 + 0.03 * math.sin(i * 2.1))
        right = w * (0.98 - 0.03 * math.cos(i * 1.7))
        p.drawLine(QPointF(left, y), QPointF(right, y))

    # 月光柱里的碎光：几粒横向小短线，水面才不是一块死板
    if moon:
        p.setPen(Qt.NoPen)
        for j in range(7):
            t = (j + 1) / 8.0
            y = water_top + (h - water_top) * (t ** 1.25)
            half = w * (0.018 + 0.030 * t)
            p.setBrush(QColor(214, 234, 255, int(58 * (1.0 - t) ** 1.1) + 10))
            p.drawEllipse(QPointF(mx, y), half, 0.9)

    # --- 帆船剪影 ---
    if boat and h - water_top > 40:
        _draw_boat(p, w * 0.5, water_top + (h - water_top) * 0.42,
                   max(18.0, min(w, h) * 0.085))


def _draw_boat(p: QPainter, cx: float, cy: float, size: float) -> None:
    """水面上一叶小舟的剪影。"""
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(6, 14, 28, 235))

    hull = QPainterPath()
    hull.moveTo(cx - size * 0.92, cy)
    hull.quadTo(cx, cy + size * 0.62, cx + size * 0.92, cy)
    hull.quadTo(cx, cy + size * 0.20, cx - size * 0.92, cy)
    p.drawPath(hull)

    sail = QPainterPath()
    sail.moveTo(cx - size * 0.06, cy - size * 1.62)
    sail.lineTo(cx + size * 0.78, cy - size * 0.06)
    sail.lineTo(cx - size * 0.06, cy - size * 0.06)
    sail.closeSubpath()
    p.drawPath(sail)

    jib = QPainterPath()
    jib.moveTo(cx - size * 0.16, cy - size * 1.30)
    jib.lineTo(cx - size * 0.74, cy - size * 0.06)
    jib.lineTo(cx - size * 0.16, cy - size * 0.06)
    jib.closeSubpath()
    p.drawPath(jib)


# --------------------------------------------------------------------------
# 帆船徽标
# --------------------------------------------------------------------------


def _boat_pixmap(size: int) -> QPixmap:
    """徽标本体，画在 size×size 的正方形里。"""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    p.scale(s / 100.0, s / 100.0)

    # 主帆（青 -> 亮蓝渐变）
    sail = QLinearGradient(28, 6, 62, 62)
    sail.setColorAt(0.0, QColor("#7FE9FF"))
    sail.setColorAt(1.0, QColor(P.ACCENT))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(sail))
    main_sail = QPainterPath()
    main_sail.moveTo(50, 8)
    main_sail.lineTo(76, 66)
    main_sail.quadTo(62, 72, 50, 70)
    main_sail.closeSubpath()
    p.drawPath(main_sail)

    # 副帆（更浅，往前倾）
    jib = QLinearGradient(20, 24, 46, 66)
    jib.setColorAt(0.0, QColor("#BFF2FF"))
    jib.setColorAt(1.0, QColor("#3FA9FF"))
    p.setBrush(QBrush(jib))
    fore_sail = QPainterPath()
    fore_sail.moveTo(44, 22)
    fore_sail.lineTo(22, 66)
    fore_sail.quadTo(34, 71, 46, 68)
    fore_sail.closeSubpath()
    p.drawPath(fore_sail)

    # 船身
    hull = QLinearGradient(18, 70, 84, 84)
    hull.setColorAt(0.0, QColor("#5FE0FF"))
    hull.setColorAt(1.0, QColor("#2E7BFF"))
    p.setBrush(QBrush(hull))
    body = QPainterPath()
    body.moveTo(14, 72)
    body.lineTo(86, 72)
    body.quadTo(78, 88, 50, 88)
    body.quadTo(22, 88, 14, 72)
    body.closeSubpath()
    p.drawPath(body)

    # 桅杆
    p.setPen(QPen(QColor("#D6F3FF"), 2.4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(48, 6), QPointF(48, 70))

    p.end()
    return pm


_LOGO_CACHE: dict[int, QPixmap] = {}
#: 设计稿文件优先。读不到就退回下面那套矢量画的帆船 —— 界面不能开天窗。
_LOGO_FILE = "canoe-logo.png"
_logo_source: QPixmap | None = None
_logo_missing = False

#: 小于这个尺寸就用矢量版：圆环徽章缩到十几像素会糊成一团，
#: 简单的线条反而认得出来。
_VECTOR_BELOW = 26


def _load_logo_source() -> QPixmap | None:
    global _logo_source, _logo_missing
    if _logo_source is not None or _logo_missing:
        return _logo_source
    path = ASSETS_DIR / _LOGO_FILE
    if path.is_file():
        pm = QPixmap(str(path))
        if not pm.isNull():
            _logo_source = pm
            return _logo_source
    _logo_missing = True
    return None


def sailboat_logo(size: int = 84) -> QPixmap:
    """轻舟徽标。带缓存，界面重建时不必重画。

    优先用**设计稿**（`assets/canoe-logo.png`，圆环 + 点阵地图 + 帆船 + 水波）；
    文件不在、或者尺寸太小（< 26px）时，退回矢量画的那叶小舟。
    """
    if size in _LOGO_CACHE:
        return _LOGO_CACHE[size]

    pm: QPixmap | None = None
    if size >= _VECTOR_BELOW:
        source = _load_logo_source()
        if source is not None:
            pm = source.scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
    if pm is None:
        pm = _boat_pixmap(size)

    _LOGO_CACHE[size] = pm
    return pm


def app_mark(size: int = 20) -> QPixmap:
    """标题栏上的小徽标（小尺寸会走矢量版，见 sailboat_logo）。"""
    return sailboat_logo(size)


# --------------------------------------------------------------------------
# 线性图标
# --------------------------------------------------------------------------


def _icon_user(p: QPainter, r: QRectF) -> None:
    p.drawEllipse(QPointF(r.center().x(), r.top() + r.height() * 0.28),
                  r.width() * 0.21, r.width() * 0.21)
    arc = QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.56,
                 r.width() * 0.68, r.height() * 0.62)
    path = QPainterPath()
    path.moveTo(arc.left(), arc.bottom())
    path.arcTo(arc, 180, -180)
    p.drawPath(path)


def _icon_user_plus(p: QPainter, r: QRectF) -> None:
    inner = QRectF(r.left(), r.top(), r.width() * 0.74, r.height())
    _icon_user(p, inner)
    cx, cy = r.right() - r.width() * 0.12, r.bottom() - r.height() * 0.2
    arm = r.width() * 0.18
    p.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
    p.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))


def _icon_lock(p: QPainter, r: QRectF) -> None:
    body = QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.44,
                  r.width() * 0.68, r.height() * 0.50)
    p.drawRoundedRect(body, 3, 3)
    shackle = QRectF(r.left() + r.width() * 0.30, r.top() + r.height() * 0.14,
                     r.width() * 0.40, r.height() * 0.56)
    path = QPainterPath()
    path.moveTo(shackle.left(), shackle.bottom())
    path.arcTo(shackle, 180, -180)
    p.drawPath(path)
    p.drawPoint(QPointF(body.center().x(), body.center().y()))


def _icon_eye(p: QPainter, r: QRectF) -> None:
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.06, r.center().y())
    path.quadTo(r.center().x(), r.top() + r.height() * 0.10,
                r.right() - r.width() * 0.06, r.center().y())
    path.quadTo(r.center().x(), r.bottom() - r.height() * 0.10,
                r.left() + r.width() * 0.06, r.center().y())
    p.drawPath(path)
    p.drawEllipse(r.center(), r.width() * 0.13, r.width() * 0.13)


def _icon_eye_off(p: QPainter, r: QRectF) -> None:
    _icon_eye(p, r)
    p.drawLine(QPointF(r.left() + r.width() * 0.10, r.bottom() - r.height() * 0.10),
               QPointF(r.right() - r.width() * 0.10, r.top() + r.height() * 0.10))


def _icon_arrow_right(p: QPainter, r: QRectF) -> None:
    y = r.center().y()
    p.drawLine(QPointF(r.left() + r.width() * 0.12, y),
               QPointF(r.right() - r.width() * 0.14, y))
    p.drawLine(QPointF(r.right() - r.width() * 0.44, y - r.height() * 0.26),
               QPointF(r.right() - r.width() * 0.14, y))
    p.drawLine(QPointF(r.right() - r.width() * 0.44, y + r.height() * 0.26),
               QPointF(r.right() - r.width() * 0.14, y))


def _icon_refresh(p: QPainter, r: QRectF) -> None:
    ring = QRectF(r.left() + r.width() * 0.12, r.top() + r.height() * 0.12,
                  r.width() * 0.76, r.height() * 0.76)
    p.drawArc(ring, 60 * 16, 280 * 16)
    tip = QPointF(ring.center().x() + ring.width() / 2 * math.cos(math.radians(-60)),
                  ring.center().y() + ring.height() / 2 * math.sin(math.radians(-60)))
    a = r.width() * 0.20
    p.drawLine(tip, QPointF(tip.x() - a, tip.y() + a * 0.30))
    p.drawLine(tip, QPointF(tip.x() - a * 0.30, tip.y() - a))


def _icon_terminal(p: QPainter, r: QRectF) -> None:
    box = QRectF(r.left() + r.width() * 0.08, r.top() + r.height() * 0.16,
                 r.width() * 0.84, r.height() * 0.68)
    p.drawRoundedRect(box, 4, 4)
    p.drawLine(QPointF(box.left() + box.width() * 0.20, box.top() + box.height() * 0.32),
               QPointF(box.left() + box.width() * 0.40, box.top() + box.height() * 0.50))
    p.drawLine(QPointF(box.left() + box.width() * 0.40, box.top() + box.height() * 0.50),
               QPointF(box.left() + box.width() * 0.20, box.top() + box.height() * 0.68))
    p.drawLine(QPointF(box.left() + box.width() * 0.52, box.top() + box.height() * 0.68),
               QPointF(box.right() - box.width() * 0.16, box.top() + box.height() * 0.68))


def _icon_link(p: QPainter, r: QRectF) -> None:
    def ring(offset: float) -> None:
        box = QRectF(r.left() + r.width() * offset, r.top() + r.height() * 0.30,
                     r.width() * 0.52, r.height() * 0.40)
        p.drawRoundedRect(box, box.height() / 2, box.height() / 2)

    ring(0.02)
    ring(0.46)
    p.drawLine(QPointF(r.left() + r.width() * 0.40, r.center().y()),
               QPointF(r.right() - r.width() * 0.36, r.center().y()))


def _icon_doc(p: QPainter, r: QRectF) -> None:
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.24, r.top() + r.height() * 0.12)
    path.lineTo(r.left() + r.width() * 0.60, r.top() + r.height() * 0.12)
    path.lineTo(r.right() - r.width() * 0.20, r.top() + r.height() * 0.36)
    path.lineTo(r.right() - r.width() * 0.20, r.bottom() - r.height() * 0.12)
    path.lineTo(r.left() + r.width() * 0.24, r.bottom() - r.height() * 0.12)
    path.closeSubpath()
    p.drawPath(path)
    for i in (0.44, 0.60):
        p.drawLine(QPointF(r.left() + r.width() * 0.36, r.top() + r.height() * i),
                   QPointF(r.right() - r.width() * 0.36, r.top() + r.height() * i))


def _icon_globe(p: QPainter, r: QRectF) -> None:
    c = r.center()
    rad = min(r.width(), r.height()) * 0.42
    p.drawEllipse(c, rad, rad)
    p.drawLine(QPointF(c.x() - rad, c.y()), QPointF(c.x() + rad, c.y()))
    p.drawEllipse(c, rad * 0.46, rad)


def _icon_minus(p: QPainter, r: QRectF) -> None:
    y = r.center().y()
    p.drawLine(QPointF(r.left() + r.width() * 0.20, y),
               QPointF(r.right() - r.width() * 0.20, y))


def _icon_close(p: QPainter, r: QRectF) -> None:
    p.drawLine(QPointF(r.left() + r.width() * 0.22, r.top() + r.height() * 0.22),
               QPointF(r.right() - r.width() * 0.22, r.bottom() - r.height() * 0.22))
    p.drawLine(QPointF(r.right() - r.width() * 0.22, r.top() + r.height() * 0.22),
               QPointF(r.left() + r.width() * 0.22, r.bottom() - r.height() * 0.22))


_ICONS = {
    "minus": _icon_minus,
    "close": _icon_close,
    "user": _icon_user,
    "user-plus": _icon_user_plus,
    "lock": _icon_lock,
    "eye": _icon_eye,
    "eye-off": _icon_eye_off,
    "arrow-right": _icon_arrow_right,
    "refresh": _icon_refresh,
    "terminal": _icon_terminal,
    "link": _icon_link,
    "doc": _icon_doc,
    "globe": _icon_globe,
}

_ICON_CACHE: dict[tuple[str, int, str, float], QPixmap] = {}


def icon(name: str, size: int = 18, color: str = P.TEXT, width: float = 1.7) -> QPixmap:
    """取一枚线性图标。颜色/尺寸/线宽相同的会走缓存。"""
    key = (name, size, color, width)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    draw = _ICONS.get(name)
    if draw is not None:
        inset = width + 1.0
        draw(p, QRectF(inset, inset, size - inset * 2, size - inset * 2))
    p.end()

    _ICON_CACHE[key] = pm
    return pm
