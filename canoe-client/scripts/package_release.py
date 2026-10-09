"""把 dist/Canoe 打成可以直接上传到管理面板的发布包。

    python scripts/package_release.py

产物：dist/Canoe-<版本>-win64.zip
      + 同目录的 .sha256 文件

打出来的包直接丢进面板「发布」页就能用 —— 面板那边会自己算 sha256，
这里顺手也算一份，方便你核对传输有没有出问题。

设计上只做两件事，都是容易手工搞错的：
  1. 把 SHIP_README.txt 复制成产物里的「使用说明.txt」——
     用户在解压出来的文件夹里第一眼要找的是它。
  2. zip 里必须**带一层 Canoe/ 目录**。不带的话用户解压出来是一堆散文件，
     很容易只把 Canoe.exe 拖走，然后报「缺 _internal」。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DIST = BASE / "dist"
APP = DIST / "Canoe"
SHIP = BASE / "SHIP_README.txt"

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
    if not (APP / "Canoe.exe").is_file():
        print("[x] 没找到 dist/Canoe/Canoe.exe —— 先跑 build.bat 或 PyInstaller")
        return 1

    # 1) 用户说明
    if SHIP.is_file():
        shutil.copy2(SHIP, APP / "使用说明.txt")
        print("[*] 使用说明.txt 已就位")
    else:
        print("[!] 没有 SHIP_README.txt，包里不会带说明")

    ver = version()
    out = DIST / f"Canoe-{ver}-win64.zip"

    # 2) 打包 —— arcname 统一加一层 Canoe/，保证解压出来是个完整文件夹
    files = sorted(p for p in APP.rglob("*") if p.is_file())
    print(f"[*] 打包 {len(files)} 个文件 -> {out.name}")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in files:
            z.write(path, arcname=f"Canoe/{path.relative_to(APP).as_posix()}")

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (DIST / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")

    size_mb = out.stat().st_size / 1024 / 1024
    print()
    print("=" * 56)
    print(f"  版本    {ver}")
    print(f"  产物    {out}")
    print(f"  大小    {size_mb:.1f} MB")
    print(f"  sha256  {digest}")
    print("=" * 56)
    print()
    print("  下一步：打开管理面板 -> 发布 -> 上传安装包，版本号填 " + ver)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
