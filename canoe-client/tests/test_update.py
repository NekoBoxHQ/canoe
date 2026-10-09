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
        check("★ 替换失败会重试，不是试一次就算了", "goto swap" in script)
        # ★ 换文件必须**先改名挪开旧的**。正在运行的 exe 删不掉（WinError 5），
        #   所以 `move /y new cur` 在旧进程还活着时永远失败 —— 用户那边就是
        #   "更新一直 ping 个没停"、而且压根没装上。改名对运行中的 exe 是允许的。
        # ren 的第二个参数必须是**名字**不是路径，所以脚本里用的是裸文件名。
        # 这里正好也钉死这一点 —— 写成完整路径会静默失败（第一版就是）。
        check("★ 先改名挪开旧的（运行中的 exe 删不掉、但能改名）",
              f'ren "{fake_exe2.name}" "{fake_exe2.name}.old"' in script,
              "没看到 ren ...old；这样旧进程一活着 move 就永远失败")
        check("★ ren 用的是裸名字（第二个参数给路径会静默失败）",
              f'"{fake_exe2}.old"' not in script)
        check("★ 替换成功后把新程序拉起来", f'start "" "{fake_exe2}"' in script)
        # 用户在真机上撞到过：更新完第一次启动弹
        #   Failed to load Python DLL '...\_MEI000005842\python313.dll'.
        #   LoadLibrary: 找不到指定的模块。
        # ★★ 2026-10-10 找到病根了，跟 pid、跟解压目录撞名都无关 ★★
        #   这个脚本是老进程的子进程，**继承了老进程的 PyInstaller 环境变量**；
        #   而新 exe 被换到了**同一个路径**上，于是引导器里那句
        #       if (_PYI_ARCHIVE_FILE == 自己的归档文件名) -> 继承父进程环境
        #   成立 —— 新进程认定自己是"父进程已经解压好的那一半"，**不再解压**，
        #   直接沿用 _PYI_APPLICATION_HOME_DIR 指的那个**老 _MEI 目录**；而那个
        #   目录此刻正被老进程的引导器删除。报错里印出来的目录名属于**老实例**，
        #   所以看起来才像 pid 撞名。
        #   手工双击一直没事，是因为 Explorer 的环境里没有这些变量。
        #   这台机器上确定性复现过：把那三个变量喂进去 -> 卡在那句报错上；
        #   再加上 PYINSTALLER_RESET_ENVIRONMENT=1 -> 正常起来。
        check("★ 拉起新版本前清掉继承来的 PyInstaller 环境变量（DLL 报错的病根）",
              "PYINSTALLER_RESET_ENVIRONMENT=1" in script
              and "set _PYI_ARCHIVE_FILE=" in script
              and "set _PYI_APPLICATION_HOME_DIR=" in script
              and "set _PYI_PARENT_PROCESS_LEVEL=" in script,
              "没有清 _PYI_*，也没设 PYINSTALLER_RESET_ENVIRONMENT")
        check("★ 起 cmd 时环境就洗过一遍（双保险，源头不漏）",
              isinstance(kwargs.get("env"), dict)
              and not any(k.startswith("_PYI_") for k in kwargs["env"])
              and kwargs["env"].get("PYINSTALLER_RESET_ENVIRONMENT") == "1",
              f"env={'None' if kwargs.get('env') is None else '没洗'}")

        check("★ 重试有上限（换不动也要退出）", "lss 30 goto swap" in script)
        # ★ 这里**故意没有**"等旧进程退出"那一段。以前靠"能不能删掉 .old"来判，
        #   但 .old 正是回滚要用的那份 —— 先删了它，更新失败就没得退了。
        #   换文件那个循环本身就覆盖了"旧进程还在"：文件锁着，move 必然失败。
        check("★ 不许提前删 .old（它是回滚的底本）",
              ":waitold" not in update._BAT)
        # ★ 早先还加过一段"等旧进程的 _MEI 解压目录消失再启动"，方向正好是反的：
        #   病根就是环境变量把新进程引到了那个目录上，等它消失等于保证新进程扑空。
        check("★ 不许再等旧解压目录消失（那一步是反的）",
              ":waitmei" not in update._BAT and "{old_mei}" not in script)
        check("★ 换完先等一会儿再启动（杀毒扫描刚落盘的 exe）",
              "ping -n 5 127.0.0.1" in script, "没找到启动前的等待")
        # 光看进程在不在不算数 —— 引导器失败时也会留一个挂在原生错误框上的进程。
        # 判据是程序自己起来后写的那个启动脚印（started.txt，见 mark_started）。
        check("★ 启动后要确认它真的起来了（光看进程不算数）",
              "started.txt" in script and "goto done" in script,
              "重启之后没有确认步骤")
        check("★ 判断依据是程序自己写的启动脚印", "{marker}" not in script,
              "占位符没替换")
        # 用户原话："更新环境要静默，不要一堆弹窗，这样让客户感觉不安全"。
        # 连试三次、每次开跑前 taskkill 收弹窗 = 最多三个原生框，改成只试一次。
        check("★ 只启动一次（不连试三次，免得弹一堆原生框）",
              "goto relaunch" not in update._BAT, "又把重试循环加回来了")
        # ★ 起不来就**回滚**。用户真机上撞到过新版本起不来
        #   （"Failed to load Python DLL"），而旧程序还躺在 .old 里 ——
        #   宁可退回去用旧版本，也不能让人手里是个打不开的程序。
        check("★ 起不来要把旧版本换回去（不能留个打不开的程序）",
              f'ren "{fake_exe2.name}" "{fake_exe2.name}.bad"' in script
              and f'ren "{fake_exe2.name}.old" "{fake_exe2.name}"' in script,
              "没看到回滚")
        check("★ 回滚之后要把它拉起来", script.rstrip().endswith('start "" "{}"'.format(fake_exe2))
              or f'start "" "{fake_exe2}"' in script)
        # 真机事故：那段 `tasklist | findstr` 轮询会**永久卡住** ——
        # cmd 等 findstr，findstr 等一个永远不来的 EOF，更新挂死 18 分钟。
        # 重试 move 本身就是"等旧进程退出"（进程在跑时文件锁着、move 必失败），
        # 根本不需要再去问系统 pid 还在不在。
        check("★ 不许有 tasklist|findstr 那种管道轮询（卡死过一次）",
              "tasklist" not in update._BAT and "findstr" not in update._BAT,
              "那段轮询把一次真实更新挂死了 18 分钟")
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
        marker = sand / "started.txt"
        bat = sand / "canoe-update.bat"
        bat.write_text(
            update._BAT.replace("{old_name}", old.name + ".old")
            .replace("{cur_name}", old.name)
            .replace("{new}", str(new))
            .replace("{cur}", str(old))
            .replace("{log}", str(log))
            .replace("{marker}", str(marker)),
            encoding="ascii",
        )

        # 替身程序（hostname.exe）当然不会写启动脚印，所以这里由测试扮演
        # "程序起来了"：后台不停把脚印戳出来。**反复戳是必须的** —— 脚本
        # 每一轮开跑前都会先 del 掉它，只在最后戳一次很可能正好撞在脚本的
        # 两次检查之间，白白被判成"没起来"。
        stop_touch = threading.Event()

        def touch_loop() -> None:
            while not stop_touch.is_set():
                try:
                    marker.write_text("1", encoding="ascii")
                except OSError:
                    pass
                stop_touch.wait(0.5)

        toucher = threading.Thread(target=touch_loop, daemon=True)
        toucher.start()
        try:
            proc = subprocess.run(["cmd", "/c", str(bat)], cwd=str(sand),
                                  capture_output=True, text=True, timeout=120)
        finally:
            stop_touch.set()
            toucher.join(timeout=5)
        check("脚本跑完退出码 0", proc.returncode == 0, f"{proc.returncode} {proc.stderr[:200]}")
        check("★ 文件真的被换掉了", old.read_bytes() == host_exe.read_bytes(),
              f"{old.stat().st_size} 字节")
        check("换完之后 .new 没了（是 move 不是 copy）", not new.exists())
        check("换完之后 .old 也被收掉了（旧进程早退了，del 删得掉）",
              not (sand / "Canoe.exe.old").exists())
        check("没有失败日志（说明没走到失败分支）", not log.exists())
        # .bat 特意**不**自删：删掉正在执行的批处理，cmd 读不到下一行，
        # 会退回 1 并喷一句"找不到批处理文件"。留着不影响什么 ——
        # 下次启动 cleanup_leftovers() 会清掉它（[4] 里验过那个函数）。
        check("★ 脚本里没有自删（自删会让 cmd 退出码变成 1）",
              'del "%~f0"' not in update._BAT)

        # 重试循环得**有上限**：换不动也要退出，不能一直转。
        # 真机上出过事：早先那版先 `tasklist | findstr` 轮询 pid，那条管道
        # 永久卡住，把一个更新挂死了 18 分钟（cmd 等 findstr、findstr 等一个
        # 永远不来的 EOF），桌面上只剩 Canoe.exe.new 和一个转不动的 .bat。
        # 所以这里钉死两件事：上限还在，而且脚本里**不许**再有那种轮询。
        fail_bat = sand / "fail.bat"
        fail_bat.write_text(
            update._BAT.replace("{old_name}", old.name + ".old")
            .replace("{cur_name}", old.name)
            .replace("{new}", str(sand / "does-not-exist.exe"))
            .replace("{cur}", str(old))
            .replace("{log}", str(log))
            .replace("{marker}", str(marker))
            # 上限改成 2 次，等价逻辑但测试只要等几秒
            # （换文件那一步会失败：new 指向一个不存在的文件）
            .replace("lss 30 goto swap", "lss 2 goto swap"),
            encoding="ascii",
        )
        t0 = time.time()
        proc2 = subprocess.run(["cmd", "/c", str(fail_bat)], cwd=str(sand),
                               capture_output=True, text=True, timeout=90)
        took = time.time() - t0
        check("★ 换不动时会放弃（重试有上限，不会一直转）",
              took < 60, f"跑了 {took:.1f}s")
        # 日志是 cmd 写的，用的是系统 ANSI 代码页（中文机器上是 GBK），
        # 不是 UTF-8 —— 读它得容错，最后给用户看的时候也一样。
        check("★ 放弃时留下失败日志（用户能知道出了什么事）",
              log.is_file() and "could not replace" in log.read_bytes().decode("utf-8", "replace"),
              f"log 存在={log.is_file()}")
        # 顺带钉住那个读法：按 utf-8 硬读会 UnicodeDecodeError
        import inspect as _ins  # noqa: PLC0415

        src = _ins.getsource(update.last_update_log)
        check("★ 读日志时容得下非 UTF-8（cmd 写的是 GBK）",
              "gbk" in src or "mbcs" in src, "last_update_log 只按 utf-8 读会崩")
        # 真拿一份 GBK 字节喂给它，确认不再崩。
        # ⚠ 文件名必须是 canoe-update.log —— last_update_log() 是拿
        #   _current_exe() 的**目录**再拼上这个名字去找的。
        probe_dir = tmp / "gbklog"
        probe_dir.mkdir(exist_ok=True)
        real_cur = update._current_exe
        try:
            (probe_dir / "canoe-update.log").write_bytes("中文日志".encode("gbk"))
            update._current_exe = lambda: probe_dir / "Canoe.exe"  # type: ignore[assignment]
            check("★ 真喂一份 GBK 的日志也不崩", update.last_update_log() == "中文日志",
                  "读法还是有问题")
        finally:
            update._current_exe = real_cur  # type: ignore[assignment]
        log.unlink(missing_ok=True)

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

    # --- 8. 清掉强杀留下的 _MEI 残留 ---
    #
    # 目录名是确定性的：`_MEI` + 引导器 pid 的 8 位十六进制 + `_wtempnam` 补的
    # 一位（拿报错截图里那个名字验）。这类残留是被强杀/崩溃留下来的 —— 引导器
    # 退出前要删掉整个目录，而内核 bin/sing-box.exe 还住在里面时删不掉。
    # 清理纯粹是收拾地方，不承担正确性（那个 DLL 报错的病根是环境变量继承，
    # 见 update.py 里的 _BAT 注释）。所以只清确定死掉的那批。
    print("\n[8] 清理 _MEI 残留")
    m = update._MEI_RE.fullmatch("_MEI00003ae42")     # 用户截图里那个名字
    check("★ 目录名里确实能解出 pid（拿报错截图里的名字验）",
          m is not None and (int(m.group(1), 16) >> 4) == 15076,
          "解码规则对不上")

    import shutil as _shutil  # noqa: PLC0415

    fake_tmp = tmp / "fake-temp"
    fake_tmp.mkdir(exist_ok=True)
    aged = time.time() - update._STALE_AFTER - 600        # 够老

    def mk_mei(pid: int, *, ours: bool = True, age: float = aged) -> Path:
        d = fake_tmp / ("_MEI%08x" % ((pid << 4) | 2))
        (d / "assets").mkdir(parents=True, exist_ok=True)
        if ours:
            (d / "assets" / "canoe.ico").write_bytes(b"ico")
        (d / "python313.dll").write_bytes(b"x")
        os.utime(d, (age, age))
        return d

    # ⚠ 不能拿"刚退出的进程"当死 pid —— 只要还有谁攥着它的句柄，
    #   OpenProcess 照样回答"活着"（实测过）。用一个确定不存在的。
    dead_pid = 999_999
    check("前提：这个 pid 确实不存在（不然下面几条测的不是同一件事）",
          not update._pid_alive(dead_pid))

    ours_dead = mk_mei(dead_pid)                       # 我们的 + pid 没了 + 够老 -> 该删
    # ⚠ 这几个必须用**不同的 pid** —— 目录名是 pid 决定的，重了就是同一个目录
    others_dead = mk_mei(dead_pid + 12, ours=False)    # 别的 PyInstaller 程序 -> 该留
    ours_live = mk_mei(os.getpid())                    # 我们的 + pid 活着 -> 该留
    ours_fresh = mk_mei(dead_pid + 4, age=time.time())  # 刚建出来的 -> 该留
    current = mk_mei(dead_pid + 8)                     # 当前进程自己用的 -> 该留
    # 只解压到一半就死掉的残骸：还没来得及有 assets/，但 bin/sing-box.exe 已经
    # 落盘了 —— 那也是我们的，得认得出来。
    ours_partial = fake_tmp / ("_MEI%08x" % (((dead_pid + 16) << 4) | 2))
    (ours_partial / "bin").mkdir(parents=True)
    (ours_partial / "bin" / "sing-box.exe").write_bytes(b"x")
    os.utime(ours_partial, (aged, aged))

    gone = update.clean_stale_temp_dirs(root=fake_tmp, mine=current)
    check("★ 强杀留下的、pid 已经没了的 -> 清掉", not ours_dead.exists(), str(gone))
    check("★ 只解压到一半的残骸也认得出来（bin/sing-box.exe 也是标记）",
          not ours_partial.exists())
    check("★ 别的程序的 _MEI 一律不碰（误删等于砸人家饭碗）", others_dead.is_dir())
    check("★ pid 还活着的不碰（可能是正在启动的另一个实例）", ours_live.is_dir())
    check("★ 刚建出来的不碰（提权那个实例可能正在启动）", ours_fresh.is_dir())
    check("★ 当前进程自己用的那个不碰", current.is_dir())
    check("清掉的个数对得上", gone == 2, str(gone))
    _shutil.rmtree(fake_tmp, ignore_errors=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
