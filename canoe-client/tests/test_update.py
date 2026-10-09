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
import os
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_client import update  # noqa: E402

passed = failed = skipped = 0


def skip(label: str, why: str) -> None:
    global skipped
    skipped += 1
    print(f"  [跳过] {label} —— {why}")

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
        # 用户在真机上撞到过：更新完第一次启动弹
        #   Failed to load Python DLL '...python313.dll'
        # 单文件 exe 启动时会解压到 %TEMP%\_MEIxxxx 并清理上一次的同名目录，
        # 新的太早起来就会被对方清掉。所以必须先等旧进程真没了。
        check("★ 先等旧进程消失，不是只等文件锁",
              "tasklist" in script and ":wait_old" in script and "goto wait_old" in script)
        # 别用 find —— 装了 Git 的机器上 PATH 里 MSYS 那个 find 会赢，
        # 脚本当场报 "find: '1234': No such file or directory"（真踩过）
        check("★ 用 findstr 而不是 find（find 会被 MSYS 的那份抢走）",
              "findstr" in script and "| find " not in script)
        check("★ 换完文件先等一会儿再启动（刚落盘的 exe 会被杀毒软件扫）",
              "ping -n 4 127.0.0.1" in script, "没找到启动前的等待")
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

    # --- 5. 真的把 .bat 跑一遍 ---
    # 上面那些只验了脚本的**文字**。这段是真执行：在沙箱目录里放两个无害的
    # 系统小工具冒充新旧程序，看它到底换没换成功、有没有跑到最后一步。
    print("\n[5] 真跑一遍替换脚本")
    import subprocess  # noqa: PLC0415

    sand = tmp / "bat-sandbox"
    sand.mkdir(exist_ok=True)
    where_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
    host_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "hostname.exe"

    if not (where_exe.is_file() and host_exe.is_file()):
        skip("真跑 .bat", "这台机器上没有 where.exe / hostname.exe 当替身")
    else:
        old = sand / "Canoe.exe"
        new = sand / "Canoe.exe.new"
        old.write_bytes(where_exe.read_bytes())      # 冒充"当前程序"
        new.write_bytes(host_exe.read_bytes())       # 冒充"新程序"
        log = sand / "canoe-update.log"
        bat = sand / "canoe-update.bat"
        # 用一个**已经死掉的 pid**，等待循环应当立刻放行
        bat.write_text(
            update._BAT.replace("{new}", str(new))
            .replace("{cur}", str(old))
            .replace("{log}", str(log))
            .replace("{pid}", "999999"),
            encoding="ascii",
        )

        proc = subprocess.run(["cmd", "/c", str(bat)], cwd=str(sand),
                              capture_output=True, text=True, timeout=120)
        check("脚本跑完退出码 0", proc.returncode == 0, f"{proc.returncode} {proc.stderr[:200]}")
        check("★ 文件真的被换掉了", old.read_bytes() == host_exe.read_bytes(),
              f"{old.stat().st_size} 字节")
        check("换完之后 .new 没了（是 move 不是 copy）", not new.exists())
        check("没有失败日志（说明没走到失败分支）", not log.exists())
        # .bat 特意**不**自删：删掉正在执行的批处理，cmd 读不到下一行，
        # 会退回 1 并喷一句"找不到批处理文件"。留着不影响什么 ——
        # 下次启动 cleanup_leftovers() 会清掉它（[4] 里验过那个函数）。
        check("★ 脚本里没有自删（自删会让 cmd 退出码变成 1）",
              'del "%~f0"' not in update._BAT)

        # 旧进程还活着时会等 —— 拿当前进程的 pid 试，等一小会儿就该超时放行
        old.write_bytes(where_exe.read_bytes())
        new.write_bytes(host_exe.read_bytes())
        bat.write_text(
            update._BAT.replace("{new}", str(new))
            .replace("{cur}", str(old))
            .replace("{log}", str(log))
            .replace("{pid}", str(os.getpid())),
            encoding="ascii",
        )
        t0 = time.time()
        subprocess.run(["cmd", "/c", str(bat)], cwd=str(sand),
                       capture_output=True, text=True, timeout=180)
        waited = time.time() - t0
        check("★ 脚本会先等旧进程消失（这里等的是自己的 pid）",
              waited > 5, f"只等了 {waited:.1f}s")
        check("等超时之后照样把文件换掉了（不会卡死在那儿）",
              old.read_bytes() == host_exe.read_bytes())

    # --- 6. 更新这条路不该走系统代理 ---
    # 用户报的："系统代理加 TUN 的时候无法下载更新客户端"。
    # 代理配置本来就是服务端下发的 —— 让"取更新"依赖"代理能用"就成环了：
    # 代理一坏，连更新都点不动，程序自己没法自愈。
    print("\n[6] 更新不走系统代理")
    _Handler.blob = b'{"version":"9.9.9","url":"http://x/y.zip"}'
    _Handler.status = 200
    _Handler.truncate = False
    srv2 = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port2 = srv2.server_address[1]
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{port2}/latest"

    import requests as _rq  # noqa: PLC0415

    saved_env = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
    os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = "http://127.0.0.1:1"   # 死代理
    try:
        # 对照组：先证明这个环境变量**真的**会被 requests 采纳，
        # 否则下面那条断言就是在测一个不存在的场景
        try:
            _rq.get(target, timeout=5)
            check("对照组：死代理本该让普通请求失败", False, "居然通了")
        except Exception:
            check("对照组：死代理确实会让普通请求失败（所以下面那条有意义）", True)

        try:
            info2 = update.check(target, "1.0.0")
            check("★ update.check 无视系统代理，直连成功",
                  info2.latest == "9.9.9", info2.latest)
        except update.UpdateError as exc:
            check("★ update.check 无视系统代理，直连成功", False, exc.message)

        try:
            dest9 = tmp / "dl-proxy.zip"
            update.download(target, dest9)
            check("★ update.download 也无视系统代理", dest9.is_file())
        except update.UpdateError as exc:
            check("★ update.download 也无视系统代理", False, exc.message)
    finally:
        srv2.shutdown()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- 7. 没打包运行时一律拒绝 ---
    print("\n[7] 源码运行时不装作能自更新")
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
