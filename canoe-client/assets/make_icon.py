"""生成轻舟图标：极简线条，一叶小舟 + 水波。

配色：深蓝 / 墨青 / 白。冷淡风。

用法：
    python make_icon.py

产出 assets/canoe.png（512）和 assets/canoe.ico（多尺寸）。
用 Pillow 直接绘制而不是用外部素材，好处是尺寸、粗细、配色都可复现可调。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent

# 取自 canoe_core.Palette，这里不 import 是为了让本脚本能独立运行
INK = (13, 22, 34)          # 墨底
DEEP_BLUE = (29, 78, 137)   # 深蓝
INK_CYAN = (42, 127, 143)   # 墨青
WHITE = (242, 246, 250)     # 白

SS = 4          # 超采样倍数，为了边缘平滑
SIZE = 512
S = SIZE * SS


def _round_rect_mask(size: int, radius_ratio: float = 0.22) -> Image.Image:
    """圆角方形蒙版 —— Windows 图标常见形态。"""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    return mask


def _wave(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float, amp: float,
          width: int, color: tuple, cycles: float = 1.5, steps: int = 140) -> None:
    """画一条正弦水波。极简线条里的"水"必须是弯的，直线不像水。"""
    import math

    pts = []
    for i in range(steps + 1):
        t = i / steps
        px = S * (x0 + (x1 - x0) * t)
        py = S * y - S * amp * math.sin(t * cycles * 2 * math.pi)
        pts.append((px, py))
    d.line(pts, fill=color, width=width, joint="curve")


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- 底：整块墨色圆角方块，不做渐变，保持冷淡干净 ---
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=INK)

    line_w = int(S * 0.026)   # 船舷
    thin_w = int(S * 0.018)   # 桅杆 / 水波

    hull_top = 0.545

    # --- 船身：平直船舷 + 弧形船底（半圆），合起来是一个干净的船形 ---
    d.line(
        [int(S * 0.175), int(S * hull_top), int(S * 0.825), int(S * hull_top)],
        fill=WHITE, width=line_w,
    )
    # PIL 的 arc 从 3 点钟顺时针：0 -> 180 正好是下半圆
    d.arc(
        [int(S * 0.175), int(S * (hull_top - 0.135)), int(S * 0.825), int(S * (hull_top + 0.135))],
        start=0, end=180, fill=WHITE, width=line_w,
    )

    # --- 桅杆 ---
    mast_x = int(S * 0.455)
    d.line([mast_x, int(S * hull_top), mast_x, int(S * 0.215)], fill=WHITE, width=thin_w)

    # --- 帆：左直边贴着桅杆，底边收在船舷上，外缘向外鼓起 ---
    sail_left = mast_x + int(S * 0.022)
    sail_right = mast_x + int(S * 0.185)
    sail_top = int(S * 0.290)
    sail_bottom = int(S * (hull_top - 0.030))

    # 外缘用一条外凸的曲线，比直边更像被风吹满的帆
    import math as _math

    a = (sail_left, sail_top)          # 顶点
    c = (sail_right, sail_bottom)      # 右下的外角
    dx, dy = c[0] - a[0], c[1] - a[1]
    length = _math.hypot(dx, dy) or 1.0
    # 指向右上方的法线
    nx, ny = dy / length, -dx / length
    bulge = S * 0.055

    leech = []
    for i in range(41):
        t = i / 40
        px = a[0] + dx * t + nx * bulge * _math.sin(_math.pi * t)
        py = a[1] + dy * t + ny * bulge * _math.sin(_math.pi * t)
        leech.append((px, py))

    d.polygon([a, (sail_left, sail_bottom), (sail_right, sail_bottom), *reversed(leech)],
              fill=INK_CYAN)

    # --- 水波：三条，长短与相位错开 ---
    _wave(d, 0.185, 0.470, 0.735, 0.018, thin_w, WHITE + (255,))
    _wave(d, 0.520, 0.815, 0.780, 0.018, thin_w, WHITE + (255,))
    _wave(d, 0.235, 0.640, 0.838, 0.020, int(S * 0.014), INK_CYAN + (255,))

    # --- 收尾：降采样 + 圆角蒙版 ---
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    img.putalpha(_round_rect_mask(SIZE))
    return img


def main() -> int:
    img = draw_icon()

    png_path = HERE / "canoe.png"
    img.save(png_path)
    print(f"[+] {png_path}")

    # Windows 图标：多尺寸打包，任务栏/资源管理器/Alt-Tab 各取所需
    ico_path = HERE / "canoe.ico"
    img.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"[+] {ico_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
