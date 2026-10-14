"""从设计稿生成轻舟的图标资源。

用法（在 canoe-client 目录下）：

    .venv/Scripts/python assets/make_icon.py

输入输出：

    assets/logo-source.png   设计稿原图（圆形徽标，四周透明）—— 换图标换这张
            │
            ▼
    assets/canoe-logo.png    512×512，缩到 94% 居中，四周留白统一
    assets/canoe.png         同上（兼容旧名字：窗口 / 任务栏图标读它）
    assets/canoe.ico         多尺寸，Windows 图标（桌面 / 资源管理器 / Alt-Tab / 托盘）

为什么要有这一步：设计稿的留白是它自己的（这张是四周各 2.7%），直接用的话
每个尺寸的边距都不一样；而且 .ico 需要**每一档单独出图** —— 把一张大图硬缩到
16px，Windows 反而会拿 256 那帧去凑合，小图标就糊。这里七档各自按目标尺寸
重采样（多步降采样，比一步从 512 缩到 16 干净）。

⚠ 原来这份依赖 **Pillow**，而它不在依赖里 —— 那个脚本其实一直跑不起来。
现在只用 PySide6（本来就在依赖里），不需要任何额外库；
.ico 是手写的容器（ICONDIR + 各帧 PNG），格式本来就简单。
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt   # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter           # noqa: E402

SOURCE = HERE / "logo-source.png"
LOGO = HERE / "canoe-logo.png"
PNG = HERE / "canoe.png"
ICO = HERE / "canoe.ico"

BIG = 512            # 大图边长
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
#: 徽标占画布多大 —— 剩下的是留白（图标不能顶到边）
FILL = 0.94


def fit(source: QImage, size: int) -> QImage:
    """把设计稿按 FILL 缩到 size×size 上（居中、透明底）。

    多步降采样：512→16 一步做，Qt 会漏掉细节、边缘发糊；每次最多缩一半
    地降到目标，干净得多。
    """
    out = QImage(size, size, QImage.Format_ARGB32)
    out.fill(Qt.transparent)
    inner = max(1, int(round(size * FILL)))
    scaled = source
    # 先缩到比目标大一点的档，再逐级减半，最后一步到目标
    scale_src = source
    while scale_src.width() > inner * 2:
        nw = max(inner, scale_src.width() // 2)
        scale_src = scale_src.scaled(nw, nw, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    scaled = scale_src.scaled(inner, inner, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    off = (size - scaled.width()) / 2
    p.drawImage(QRectF(off, off, scaled.width(), scaled.height()), scaled)
    p.end()
    return out


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_ico(frames: list[tuple[int, bytes]]) -> bytes:
    """按 ICO 格式拼一个多尺寸图标（6 字节头 + 每档 16 字节目录 + 各帧数据）。

    宽高各占 1 字节，所以 256 只能写成 0（约定）。
    """
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    data = bytearray()
    for size, blob in frames:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        data += blob
    return header + bytes(entries) + bytes(data)


def alpha_pixels(img: QImage) -> int:
    rgba = img.convertToFormat(QImage.Format_ARGB32)
    return sum(1 for y in range(rgba.height()) for x in range(rgba.width())
               if rgba.pixelColor(x, y).alpha() > 8)


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(
            f"找不到设计稿 {SOURCE}\n"
            f"把那张圆形徽标存成 logo-source.png 放进 assets/ 再跑一次。"
        )
    QGuiApplication([])

    src = QImage(str(SOURCE))
    if src.isNull():
        raise SystemExit(f"{SOURCE} 读不出来（是不是格式不对？）")
    print(f"[1/3] 设计稿 {SOURCE.name}  {src.width()}×{src.height()}"
          f"  带 alpha={src.hasAlphaChannel()}")

    out = fit(src, BIG)
    out.save(str(LOGO))
    out.save(str(PNG))
    print(f"[2/3] 写出 {LOGO.name} / {PNG.name}（{BIG}px，缩到 {FILL:.0%} 居中）")

    print(f"[3/3] 生成 {ICO.name}（{len(ICO_SIZES)} 档，各自单独重采样）")
    frames = []
    for size in ICO_SIZES:
        img = fit(src, size)
        frames.append((size, png_bytes(img)))
        print(f"    {size:>3}px  {len(frames[-1][1]):>6} 字节  不透明像素 {alpha_pixels(img):>4}")
    ICO.write_bytes(build_ico(frames))

    print()
    for path in (LOGO, PNG, ICO):
        print(f"    {path.name:16} {path.stat().st_size:>8} 字节")

    if alpha_pixels(fit(src, 16)) < 40:
        print("    [x] 16px 那档几乎是空白 —— 图标不能用")
        return 1
    print("    各尺寸都画出了东西")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
