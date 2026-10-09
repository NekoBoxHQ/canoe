"""轻舟 · 客户端自更新测试。

覆盖「点更新之后到底会发生什么」这条链子：

    1. 大小 / 摘要校验 —— 下载下来的东西是要被执行的，这是唯一一道闸
    2. 解包：从发布 zip 里取出 Canoe.exe
    3. 交班脚本：.bat 的内容、编码、路径
    4. 边角：取消、写不进去、包坏了

★ 绝不真的替换任何 exe —— 把 subprocess.Popen 换掉，只看它准备干什么。
   这个测试跑在开发机上，真跑一遍会把开发机上的 python.exe 顶掉。

用法：
    python tests/test_update.py
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_client import update  # noqa: E402

passed = failed = 0

#: 够大就行 —— prepare_update 会拦"文件太小"，所以造个 1.5MB 的假 exe
FAKE_EXE = b"MZ" + b"\0" * (1_500_000)


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def make_zip(path: Path, member: str = "Canoe.exe", payload: bytes = FAKE_EXE) -> bytes:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, payload)
    return path.read_bytes()


class _Handler(BaseHTTPRequestHandler):
    """吐一份固定内容。状态码 / 是否截断由类属性控制。"""

    blob = b""
    status = 200
    truncate = False

    def do_GET(self):  # noqa: N802
        if self.status >= 400:
            self.send_response(self.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.blob)))
        self.end_headers()
        self.wfile.write(self.blob[: len(self.blob) // 2] if self.truncate else self.blob)

    def log_message(self, *a):
        pass


def main() -> int:
    print("\n== 轻舟 · 客户端自更新测试 ==\n")
    tmp = Path(tempfile.mkdtemp(prefix="canoe-update-test-"))

    # --- 1. 版本比较 / 大小显示 ---
    print("[1] 版本与大小")
    check("human_size 空值", update.human_size(0) == "未知大小")
    check("human_size KB", update.human_size(50 * 1024) == "50 KB", update.human_size(50 * 1024))
    check("human_size MB", update.human_size(61 * 1024 * 1024) == "61.0 MB",
          update.human_size(61 * 1024 * 1024))
    info = update.UpdateInfo(latest="1.1.0", current="1.0.0", min_version="1.0.5")
    check("is_newer", info.is_newer)
    check("★ 低于服务端要求的最低版本 -> must_upgrade", info.must_upgrade)
    check("不低于就不强制",
          not update.UpdateInfo(latest="1.1.0", current="1.0.5", min_version="1.0.5").must_upgrade)
    check("没写最低版本就不强制",
          not update.UpdateInfo(latest="1.1.0", current="0.9.0").must_upgrade)

    # --- 2. 取 exe ---
    print("\n[2] 从发布包里取出 exe（prepare_update）")
    good = tmp / "good.zip"
    blob = make_zip(good)
    digest = hashlib.sha256(blob).hexdigest()

    fake_exe = tmp / "Canoe.exe"
    fake_exe.write_bytes(b"MZ fake")
    real_frozen, real_exec = getattr(sys, "frozen", None), sys.executable
    sys.frozen = True
    sys.executable = str(fake_exe)
    try:
        check("★ 没打包运行时明确拒绝（不装作能更新）", update.can_self_update())
        staged = update.staging_path()
        check("★ 新程序放在当前程序同一个目录（同卷改名，快且稳）",
              staged.parent == fake_exe.parent and staged.name == "Canoe.exe.new",
              str(staged))

        out = update.prepare_update(good)
        check("解出来的路径就是 staged", out == staged, str(out))
        check("★ 内容跟包里一致", out.read_bytes() == FAKE_EXE,
              f"{out.stat().st_size} 字节")
        check("取完就没了中间文件之外的垃圾", out.is_file())

        # 包里没有 exe
        noexe = tmp / "noexe.zip"
        make_zip(noexe, member="readme.txt", payload=b"hello")
        try:
            update.prepare_update(noexe)
            check("包里没 exe 应当报错", False)
        except update.UpdateError as exc:
            check("★ 包里没 exe -> 可读报错", "没有 exe" in exc.message, exc.message)

        # 坏的 zip
        bad = tmp / "bad.zip"
        bad.write_bytes(b"not a zip at all")
        try:
            update.prepare_update(bad)
            check("坏包应当报错", False)
        except update.UpdateError as exc:
            check("★ 坏包 -> 可读报错", "损坏" in exc.message, exc.message)

        # 不是 Windows 程序
        notwin = tmp / "notwin.zip"
        make_zip(notwin, payload=b"PK\x03\x04" + b"\0" * 1_500_000)
        try:
            update.prepare_update(notwin)
            check("非 PE 文件应当报错", False)
        except update.UpdateError as exc:
            check("★ 取出来的不是 Windows 程序 -> 拒收", "Windows" in exc.message, exc.message)
            check("拒收时把半成品删掉（不留 Canoe.exe.new）", not staged.exists())

        # 太小
        tiny = tmp / "tiny.zip"
        make_zip(tiny, payload=b"MZ" + b"\0" * 100)
        try:
            update.prepare_update(tiny)
            check("太小的应当报错", False)
        except update.UpdateError as exc:
            check("★ 太小的包 -> 拒收", "太小" in exc.message, exc.message)
    finally:
        if real_frozen is None:
            del sys.frozen
        else:
            sys.frozen = real_frozen
        sys.executable = real_exec

    # --- 3. 下载：校验是唯一那道闸 ---
    print("\n[3] 下载与校验")
    _Handler.blob = blob
    _Handler.status = 200
    _Handler.truncate = False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/Canoe.zip"

    try:
        dest = tmp / "dl.zip"
        seen: list[tuple[int, int]] = []
        update.download(url, dest, on_progress=lambda d, t: seen.append((d, t)),
                        expected_sha256=digest, expected_size=len(blob))
        check("下载成功", dest.is_file() and dest.read_bytes() == blob)
        check("★ 回调报了进度，而且最后一格是 100%",
              bool(seen) and seen[-1][0] == len(blob) and seen[-1][1] == len(blob),
              f"{len(seen)} 次，最后 {seen[-1] if seen else None}")

        # 摘要对不上 —— 这条最重要
        dest2 = tmp / "dl2.zip"
        try:
            update.download(url, dest2, expected_sha256="0" * 64)
            check("摘要不对应当报错", False)
        except update.UpdateError as exc:
            check("★ 摘要对不上 -> 拒收", exc.code == "bad_digest", exc.code)
            check("★ 拒收时把文件删掉（绝不留半成品）", not dest2.exists())

        # 大小对不上
        dest3 = tmp / "dl3.zip"
        try:
            update.download(url, dest3, expected_size=len(blob) + 999)
            check("大小不对应当报错", False)
        except update.UpdateError as exc:
            check("★ 大小对不上 -> 拒收", exc.code == "size_mismatch", exc.code)

        # 服务端没给摘要：也要能下（老版本服务端），但大小还是要对
        dest4 = tmp / "dl4.zip"
        update.download(url, dest4, expected_size=len(blob))
        check("服务端没给 sha256 时照样能下", dest4.read_bytes() == blob)

        # 404
        _Handler.status = 404
        try:
            update.download(url, tmp / "dl5.zip")
            check("404 应当报错", False)
        except update.UpdateError as exc:
            check("★ 404 -> 可读报错", exc.code == "http_error", exc.code)
        _Handler.status = 200

        # 取消
        cancel = threading.Event()
        cancel.set()          # 一开始就是置位的：模拟"刚点就开始"的那种取消
        try:
            update.download(url, tmp / "dl6.zip", cancelled=cancel)
            check("取消应当抛 DownloadCancelled", False)
        except update.DownloadCancelled as exc:
            check("★ 取消抛的是 DownloadCancelled（不是错误）", exc.code == "cancelled")
            check("★ 取消后不留半成品", not (tmp / "dl6.zip").exists())
    finally:
        srv.shutdown()

    # --- 4. 交班脚本 ---
    print("\n[4] 替换脚本（.bat）")
    fake_exe2 = tmp / "Canoe2.exe"
    fake_exe2.write_bytes(b"MZ old")
    staged2 = tmp / "Canoe2.exe.new"
    staged2.write_bytes(b"MZ new")

    launched: list = []
    real_popen = update.subprocess.Popen
    update.subprocess.Popen = lambda *a, **k: launched.append((a, k)) or object()
    sys.frozen = True
    sys.executable = str(fake_exe2)
    try:
        bat = update.install_and_restart(staged2)
        check("写了一个 .bat", bat.is_file() and bat.suffix == ".bat", str(bat))
        check("★ 启动了一次 cmd（替换动作交给系统做）", len(launched) == 1, str(len(launched)))
        args, kwargs = launched[0]
        check("★ 起的是 cmd /c 那个脚本",
              args[0][:2] == ["cmd", "/c"] and args[0][2] == str(bat), str(args[0][:3]))
        check("★ 新进程脱离控制台、另起进程组（要能活过我们退出）",
              kwargs.get("creationflags") == 0x00000008 | 0x00000200,
              str(kwargs.get("creationflags")))

        script = bat.read_text(encoding="ascii")   # 不是纯 ASCII 这里就抛
        check("★ 脚本是纯 ASCII（批处理按控制台代码页读，中文会乱码）", True)
        check("★ 等文件锁释放后再替换（ping 当 sleep）", "ping -n 2 127.0.0.1" in script)
        check("★ 替换失败会重试，不是试一次就算了", "goto retry" in script)
        check("★ 替换成功后把新程序拉起来", f'start "" "{fake_exe2}"' in script)
        check("脚本里带上了新程序路径", str(staged2) in script)
        check("脚本里带上了当前程序路径", str(fake_exe2) in script)
        check("失败会记日志（不然用户只看到'点完没反应'）",
              "canoe-update.log" in script)

        # 没准备好新程序时不该乱写脚本
        try:
            update.install_and_restart(tmp / "nope.exe")
            check("新程序不存在应当报错", False)
        except update.UpdateError as exc:
            check("★ 新程序还没解出来就拒绝交班", exc.code == "no_staged", exc.code)

        # 不能拿当前程序自己替换自己
        try:
            update.install_and_restart(fake_exe2)
            check("自己换自己应当报错", False)
        except update.UpdateError as exc:
            check('★ 拒绝「新程序=当前程序」这种调用', exc.code == "same_file", exc.code)

        # 收拾残局
        (fake_exe2.with_name("canoe-update.log")).write_text("上次失败了", encoding="utf-8")
        leftover = fake_exe2.with_name("Canoe.exe.new")
        leftover.write_bytes(b"x")
        check("★ 能读出上次失败留下的日志", update.last_update_log() == "上次失败了",
              update.last_update_log())
        check("读完就把日志删了（只提醒一次）",
              not fake_exe2.with_name("canoe-update.log").exists())
        update.cleanup_leftovers()
        check("★ 启动时顺手清掉上次没删成的东西",
              not bat.exists() and not leftover.exists())
    finally:
        update.subprocess.Popen = real_popen
        sys.executable = real_exec

    # --- 5. 没打包运行时一律拒绝 ---
    print("\n[5] 源码运行时不装作能自更新")
    del sys.frozen
    check("can_self_update() 为假", not update.can_self_update())
    for name, fn in (("prepare_update", lambda: update.prepare_update(good)),
                     ("install_and_restart", lambda: update.install_and_restart())):
        try:
            fn()
            check(f"{name} 应当拒绝", False)
        except update.UpdateError as exc:
            check(f"★ {name} 明确拒绝并说清原因", exc.code == "not_frozen", exc.code)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
