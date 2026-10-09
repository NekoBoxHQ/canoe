"""把 dist/Canoe.exe 打成可以直接上传到管理面板的发布包。

    python scripts/package_release.py

产物：dist/Canoe-<版本>-win64.zip
      + 同目录的 .sha256 文件

zip 里**只有一个 Canoe.exe**。用户下载解压出来就一个文件，双击即用，
不会有一堆 dll 和 _internal 铺在桌面上，也不会有人把 exe 单独拖走
然后报「缺 _internal」——那是 onedir 时代的坑，改 onefile 之后没有了。

打出来的包直接丢进面板「发布」页就能用 —— 面板那边会自己算 sha256，
这里顺手也算一份，方便你核对传输有没有出问题。
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DIST = BASE / "dist"
#: onefile 的产物就是这个文件（见 canoe.spec）
APP = DIST / "Canoe.exe"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def version() -> str:
    core = BASE.parent / "canoe-core"
    ns: dict = {}
    try:
        exec((core / "canoe_core" / "version.py").read_text(encoding="utf-8"), ns)
        return str(ns["VERSION"])
    except (OSError, KeyError):
        return "0.0.0"


def main() -> int:
    if not APP.is_file():
        print("[x] 没找到 dist/Canoe.exe —— 先跑 build.bat 或 PyInstaller")
        return 1

    ver = version()
    out = DIST / f"Canoe-{ver}-win64.zip"

    # 打包 —— 就一个文件，平铺在 zip 根下，别套目录
    size_mb = APP.stat().st_size / 1024 / 1024
    print(f"[*] 打包 Canoe.exe（{size_mb:.1f} MB）-> {out.name}")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(APP, arcname="Canoe.exe")

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (DIST / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")

    print()
    print("=" * 56)
    print(f"  版本    {ver}")
    print(f"  产物    {out}")
    print(f"  大小    {out.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  内含    Canoe.exe（单文件，解压出来就这一个）")
    print(f"  sha256  {digest}")
    print("=" * 56)
    print()
    print("  下一步：canoe release " + out.name + "   （或面板 -> 发布）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
