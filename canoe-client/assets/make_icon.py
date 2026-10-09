"""从设计稿生成轻舟的 Logo 资源。

用法：
    python assets/make_icon.py

输入输出：

    assets/logo-source.png   设计稿原图（圆形徽章，四周是深色底）
            │
            │  ① 量出徽章那个圆，把圆外的底色抠成透明
            ▼
    assets/canoe-logo.png    512×512，圆外透明  ← 界面里用的就是这个
    assets/canoe.png         同上（兼容旧名字）
    assets/canoe.ico         多尺寸，Windows 图标（任务栏 / 资源管理器 / Alt-Tab）

设计稿换了只需要替换 logo-source.png 再跑一次，其余全自动。

为什么要有这一步：设计稿是一张**不透明**的方图，深色底会在浅色背景上
露出一块方形 —— 直接当图标用很难看。徽章本身是圆的，所以按圆抠。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "logo-source.png"
LOGO = HERE / "canoe-logo.png"
PNG = HERE / "canoe.png"
ICO = HERE / "canoe.ico"

SS = 4              # 超采样倍数，圆形边缘才平滑
SIZE = 512          # 输出边长
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

#: 底色大概是 (3,19,42)，亮环是 (2,132,227) 这种。取中间偏亮做阈值。
BRIGHT_SUM = 300


def find_badge_circle(im: Image.Image) -> tuple[float, float, float]:
    """量出徽章那个圆的圆心和半径。

    从画面**外缘往内**扫，第一个明显比底色亮的像素就是外圈亮环。
    （反过来从中心往外扫会一头撞在白色的帆上。）
    """
    px = im.load()
    w, h = im.size
    cx0, cy0 = w // 2, h // 2

    def bright(p) -> bool:
        return (p[0] + p[1] + p[2]) > BRIGHT_SUM

    left = next((x for x in range(0, w) if bright(px[x, cy0])), 0)
    right = next((x for x in range(w - 1, -1, -1) if bright(px[x, cy0])), w - 1)
    top = next((y for y in range(0, h) if bright(px[cx0, y])), 0)
    bottom = next((y for y in range(h - 1, -1, -1) if bright(px[cx0, y])), h - 1)

    if right <= left or bottom <= top:
        raise SystemExit(
            "没量出圆形徽章。确认 logo-source.png 是那张圆形设计稿，"
            "或者把 BRIGHT_SUM 调一调。"
        )

    print(f"    扫描: 左{left} 右{right} 上{top} 下{bottom}")
    return (
        (left + right) / 2,
        (top + bottom) / 2,
        max(right - left, bottom - top) / 2,
    )


def cut_out_circle(im: Image.Image) -> Image.Image:
    """把圆外的底色抠掉，裁成正方形。"""
    cx, cy, radius = find_badge_circle(im)
    print(f"    圆心 ({cx:.0f}, {cy:.0f})  半径 {radius:.0f}")

    # 亮环外面还有一圈光晕，往外放一点再羽化，免得切出生硬的边
    outer = radius * 1.045
    half = outer * 1.12          # 四周留一点白，做成图标不贴边

    crop = im.crop(
        (int(cx - half), int(cy - half), int(cx + half), int(cy + half))
    ).convert("RGBA")
    side = crop.width

    mask = Image.new("L", (side * SS, side * SS), 0)
    ImageDraw.Draw(mask).ellipse(
        [
            side * SS / 2 - outer * SS,
            side * SS / 2 - outer * SS,
            side * SS / 2 + outer * SS,
            side * SS / 2 + outer * SS,
        ],
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(SS * 0.8)).resize(
        (side, side), Image.LANCZOS
    )
    crop.putalpha(mask)
    return crop


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(
            f"找不到设计稿 {SOURCE}\n"
            f"把那张圆形 Logo 存成 logo-source.png 放进 assets/ 再跑一次。"
        )

    print(f"[1/2] 读设计稿 {SOURCE.name}")
    design = Image.open(SOURCE).convert("RGBA")
    print(f"    {design.width}×{design.height}")

    badged = cut_out_circle(design)
    out = badged.resize((SIZE, SIZE), Image.LANCZOS)

    out.save(LOGO)
    out.save(PNG)
    print(f"[2/2] 写出 {LOGO.name} / {PNG.name} / {ICO.name}")

    out.save(ICO, format="ICO", sizes=ICO_SIZES)

    for path in (LOGO, PNG, ICO):
        print(f"    {path.name:16} {path.stat().st_size:>8} 字节")

    # 缩到最小几档自查一下，别做出一个一像素的糊团
    for s in (16, 24, 32, 48):
        alpha = out.resize((s, s), Image.LANCZOS).getchannel("A").getextrema()
        if alpha == (0, 0):
            print(f"    [!] {s}px 整张透明，抠圆参数可能不对")
            return 1
    print("    各尺寸透明度正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
