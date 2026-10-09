"""把 dist/Canoe.exe 打成可以直接上传到管理面板的发布包。

    python scripts/package_release.py

产物：dist/Canoe-<版本>-win64.zip
      + 同目录的 .sha256 文件

zip 里**只有一个 Canoe.exe**。用户下载解压出来就一个文件，双击即用，
不会有一堆 dll 和 _internal 铺在桌面上，也不会有人把 exe 单独拖走
然后报「缺 _internal」——那是 onedir 时代的坑，改 onefile 之后没有了。

打出来的包直接丢进面板「发布」页就能用 —— 面板那边会自己算 sha256，
这里顺手也算一份，方便你核对传输有没有出问题。

★ 打包之前会先让 dist/Canoe.exe 自己跑一遍 `--selftest`，跑不过就**不出包**。
  单文件 exe 被截断了仍然是合法的 PE、照样能启动，只是解不出 python313.dll ——
  发出去就是一整批客户端"更新完打不开"（踩过一次）。
"""
from __future__ import annotations

import hashlib
import subprocess
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


def verify_app() -> bool:
    """打完包之前，先让 dist/Canoe.exe 自己跑一遍自检。

    ★ 为什么非做不可：这个 exe 是要发给用户、会被直接执行的。而单文件 exe
      被**截断**之后仍然是合法的 PE，**照样能启动** —— 只是解不出
      python313.dll，弹一句 "Failed to load Python DLL" 就完事，用户那边看到
      的是"更新完就打不开了"。真踩过：发布时读的是一个还在写的文件，87MB 的
      包装成 47MB 发了出去，客户端下载校验也"通过"（它核对的就是这份残包的
      摘要），装上才发现是残的。

      `--selftest` 的退出码只看**本地完整性**（内核在不在、bin/ 解出来没有、
      cryptography 的动态库能不能加载），不看网络 —— 拿它当"这个 exe 是不是
      完整的"这道闸正合适，离线也照跑。
    """
    print(f"[*] 自检 {APP.name} …（要解压约 80MB，等十几秒）")
    try:
        proc = subprocess.Popen(
            [str(APP), "--selftest"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        print(f"[x] 自检跑不起来：{exc}")
        return False

    try:
        out, _ = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        # 起不来，或者卡在引导器那个原生错误框上。**按 pid 连子树一起收** ——
        # 不能按映像名收，那会把开发机上正开着的轻舟一并杀掉。
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        proc.communicate()
        print("[x] 自检超时 —— 这个 exe 起不来（或者卡在某个错误框上），别发")
        return False

    if proc.returncode != 0:
        print(f"[x] 自检没通过（退出码 {proc.returncode}）—— 这个 exe 是残的，别发")
        print((out or b"").decode("utf-8", "replace")[:2000])
        return False

    print("[ok] 自检通过")
    return True


def main() -> int:
    if not APP.is_file():
        print("[x] 没找到 dist/Canoe.exe —— 先跑 build.bat 或 PyInstaller")
        return 1

    if not verify_app():
        print("[x] 没出包 —— 先把这个 exe 修好再发。")
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
