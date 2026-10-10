"""生成轻舟的图标资源 —— **全部由代码画出来**。

用法（在 canoe-client 目录下）：

    .venv/Scripts/python assets/make_icon.py

产出：

    assets/canoe-logo.png   512×512，透明底，界面里那张大图
    assets/canoe.png        同上（兼容旧名字：窗口 / 任务栏图标读的是它）
    assets/canoe.ico        多尺寸，Windows 图标（桌面 / 资源管理器 / Alt-Tab / 托盘）

为什么改成"画"、而不是原来那套"从设计稿抠圆"：

  · 原来那份依赖 **Pillow**，而它不在依赖里 —— 脚本实际上跑不起来，
    而且设计稿一换就得重新对阈值。
  · 现在船的几何只在 `canoe_client/ui/artwork.py` 的 `paint_boat()` 里
    写一份，界面里那个小徽标和这里的 .ico 用的是**同一份画法**，
    不会再出现"两个地方两条船"。

.ico 是手写的容器（ICONDIR + 各帧 PNG）：ICO 格式本来就很简单，不值得
为它引一个库；每帧都按目标尺寸**超采样**画出来（先画 8 倍再缩），
16px 那档才不会糊成一团。

⚠ 图标是**透明底的一条船**（用户定的），桌面壁纸是浅色时对比会弱一点 ——
  这是选型时就讲清楚的取舍，别自作主张给它加个圆底。
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

# QImage/QPainter 离屏就能跑，不需要窗口
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt          # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter           # noqa: E402

from canoe_client.ui.artwork import paint_boat                        # noqa: E402

LOGO = HERE / "canoe-logo.png"
PNG = HERE / "canoe.png"
ICO = HERE / "canoe.ico"

BIG = 512            # 大图边长
SS = 8               # 超采样倍数：先画 8 倍大再缩，边缘才干净
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
#: 船在画布里占多大（留边，图标不能顶到边）
FILL = 0.86


def render(size: int) -> QImage:
    """把船画到 size×size 上。"""
    big = QImage(size * SS, size * SS, QImage.Format_ARGB32)
    big.fill(Qt.transparent)
    p = QPainter(big)
    p.setRenderHint(QPainter.Antialiasing, True)
    paint_boat(p, big.width() / 2, big.height() * 0.47, big.width() * FILL)
    p.end()
    return big.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_ico(frames: list[tuple[int, bytes]]) -> bytes:
    """按 ICO 格式拼一个多尺寸图标。

    结构就两段：6 字节头 + 每个尺寸 16 字节日录 + 后面跟各帧数据。
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
    """数一下有多少个不透明像素 —— 用来确认小尺寸那几档没画成空白。"""
    rgba = img.convertToFormat(QImage.Format_ARGB32)
    n = 0
    for y in range(rgba.height()):
        for x in range(rgba.width()):
            if rgba.pixelColor(x, y).alpha() > 8:
                n += 1
    return n


def main() -> int:
    QGuiApplication([])

    print(f"[1/2] 画大图（{BIG}px，超采样 {SS}×）")
    out = render(BIG)
    out.save(str(LOGO))
    out.save(str(PNG))

    print(f"[2/2] 生成 {ICO.name}（{len(ICO_SIZES)} 档）")
    frames = []
    for size in ICO_SIZES:
        img = render(size)
        frames.append((size, png_bytes(img)))
        print(f"    {size:>3}px  {len(frames[-1][1]):>6} 字节  不透明像素 {alpha_pixels(img):>4}")
    ICO.write_bytes(build_ico(frames))

    print()
    for path in (LOGO, PNG, ICO):
        print(f"    {path.name:16} {path.stat().st_size:>8} 字节")

    # 自查：最小那档要是整张透明，说明画法或缩放坏了
    if alpha_pixels(render(16)) < 40:
        print("    [x] 16px 那档几乎是空白 —— 图标不能用")
        return 1
    print("    各尺寸都画出了东西")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
