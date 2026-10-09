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


def make_zip(path: Path, members: dict[str, bytes] | None = None) -> bytes:
    """造一个发布包。

    形态跟真的一样：zip 里第一层是 `Canoe/`，里面是 Canoe.exe + _internal/。
    用户解压到 `C:\\` 就得到 `C:\\Canoe\\`。
    """
    if members is None:
        members = {
            "Canoe/Canoe.exe": FAKE_EXE,
            "Canoe/_internal/python313.dll": b"D" * 4096,
            "Canoe/_internal/sub/extra.dll": b"E" * 2048,
        }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, payload in members.items():
            z.writestr(name, payload)
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

    # --- 2. 解包 ---
    print("\n[2] 把发布包解到暂存目录（prepare_update）")
    good = tmp / "good.zip"
    blob = make_zip(good)
    digest = hashlib.sha256(blob).hexdigest()

    app_dir = tmp / "app"
    app_dir.mkdir(exist_ok=True)
    fake_exe = app_dir / "Canoe.exe"
    fake_exe.write_bytes(b"MZ fake")
    real_frozen, real_exec = getattr(sys, "frozen", None), sys.executable
    sys.frozen = True
    sys.executable = str(fake_exe)
    try:
        check("★ 没打包运行时明确拒绝（不装作能更新）", update.can_self_update())
        check("★ 安装目录 = 程序自己所在的目录",
              update.install_dir() == app_dir, str(update.install_dir()))
        staged = update.staging_dir()
        check("★ 暂存目录放在安装目录**旁边**（同卷，换起来是改名不是拷贝）",
              staged.parent == app_dir.parent and staged.name == app_dir.name + ".update",
              str(staged))

        out = update.prepare_update(good)
        check("解出来的就是暂存目录", out == staged, str(out))
        check("★ 里面剥掉了 Canoe/ 那一层，直接长成应用目录的样子",
              (out / "Canoe.exe").is_file() and (out / "_internal" / "python313.dll").is_file(),
              str(sorted(p.name for p in out.iterdir())))
        check("★ exe 内容跟包里一致", (out / "Canoe.exe").read_bytes() == FAKE_EXE)
        check("嵌套目录也解出来了", (out / "_internal" / "sub" / "extra.dll").is_file())

        # 包里没有 exe
        noexe = tmp / "noexe.zip"
        make_zip(noexe, {"Canoe/readme.txt": b"hello"})
        try:
            update.prepare_update(noexe)
            check("包里没 exe 应当报错", False)
        except update.UpdateError as exc:
            check("★ 包里没 exe -> 可读报错", "Canoe.exe" in exc.message, exc.message)

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
        make_zip(notwin, {
            "Canoe/Canoe.exe": b"PK\x03\x04" + b"\0" * 1_500_000,
            "Canoe/_internal/x.dll": b"x",
        })
        try:
            update.prepare_update(notwin)
            check("非 PE 文件应当报错", False)
        except update.UpdateError as exc:
            check("★ 取出来的不是 Windows 程序 -> 拒收", "Windows" in exc.message, exc.message)
            check("拒收时把半成品删掉（不留暂存目录）", not staged.exists())

        # 太小
        tiny = tmp / "tiny.zip"
        make_zip(tiny, {"Canoe/Canoe.exe": b"MZ" + b"\0" * 100, "Canoe/_internal/x.dll": b"x"})
        try:
            update.prepare_update(tiny)
            check("太小的应当报错", False)
        except update.UpdateError as exc:
            check("★ 太小的包 -> 拒收", "太小" in exc.message, exc.message)

        # 少了 _internal 的残包（截断过的包就长这样）
        partial = tmp / "partial.zip"
        make_zip(partial, {"Canoe/Canoe.exe": FAKE_EXE})
        try:
            update.prepare_update(partial)
            check("缺 _internal 的包应当报错", False)
        except update.UpdateError as exc:
            check("★ 缺 _internal -> 拒收（截断包就长这样）",
                  "_internal" in exc.message, exc.message)
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
    inst = tmp / "Canoe2"
    (inst / "_internal").mkdir(parents=True, exist_ok=True)
    (inst / "Canoe.exe").write_bytes(b"MZ old")
    stage2 = tmp / "Canoe2.update"
    (stage2 / "_internal").mkdir(parents=True, exist_ok=True)
    (stage2 / "Canoe.exe").write_bytes(b"MZ new")

    launched: list = []
    real_popen = update.subprocess.Popen
    update.subprocess.Popen = lambda *a, **k: launched.append((a, k)) or object()
    sys.frozen = True
    sys.executable = str(inst / "Canoe.exe")
    try:
        bat = update.install_and_restart(stage2)
        check("写了一个 .bat", bat.is_file() and bat.suffix == ".bat", str(bat))
        check("★ .bat 落在安装目录**外面**（整个目录待会儿要被改名挪走）",
              bat.parent == inst.parent, str(bat))
        check("★ 启动了一次 cmd（换目录交给系统做）", len(launched) == 1, str(len(launched)))
        args, kwargs = launched[0]
        check("★ 起的是 cmd /c 那个脚本",
              args[0][:2] == ["cmd", "/c"] and args[0][2] == str(bat), str(args[0][:3]))
        check("★ 新进程脱离控制台、另起进程组（要能活过我们退出）",
              kwargs.get("creationflags") == 0x00000008 | 0x00000200,
              str(kwargs.get("creationflags")))

        script = bat.read_text(encoding="ascii")   # 不是纯 ASCII 这里就抛
        check("★ 脚本是纯 ASCII（批处理按控制台代码页读，中文会乱码）", True)
        check("★ 重试里带 sleep（ping 当 sleep）", "ping -n 2 127.0.0.1" in script)
        check("★ 换目录失败会重试，不是试一次就算了", "goto swap" in script)
        # ★ 目录里有正在运行的 exe 时 ren 一定失败 —— 所以这个重试循环**本身
        #   就是"等旧进程退出"**。这也正是 onefile 时代那个"更新一直 ping 个
        #   没停"的病根：当年是 move 覆盖不了运行中的 exe。
        check("★ 先把旧目录改名挪开（ren 失败=旧进程还在，重试就是等它）",
              f'ren "{inst.name}" "{inst.name}.old"' in script, script[:500])
        # ren 只认名字不认路径。给完整路径它会**静默失败**（第一版就栽在这儿，
        # 而且测试全绿 —— 只有"旧进程没退"那条路会暴露）。
        check("★ ren 用的是裸名字（给完整路径会静默失败）",
              f'ren "{inst.name}" "{inst.name}.old"' in script
              and f'ren "{inst}" ' not in script,
              "ren 那行的路径不对")
        check("★ 再把暂存目录搬进来", f'move "{stage2}" "{inst.name}"' in script)
        check("★ 换失败要把旧目录改回去（不能把用户唯一的程序弄没）",
              f'if not exist "{inst.name}" ren "{inst.name}.old" "{inst.name}"' in script)
        check("★ 重试有上限（换不动也要退出）", "lss 60 goto swap" in script)
        check("★ 换完先等一会儿再启动（杀毒扫刚落盘的文件）",
              "ping -n 5 127.0.0.1" in script)
        check("★ 拉起来的是安装目录里的 Canoe.exe",
              f'start "" "{inst}\\Canoe.exe"' in script)
        # 用户在真机上撞到过：更新后重启起不来（原生框 "Failed to load Python
        # DLL"）。既然手工再开一次能好，脚本就得自己重开，而且要有依据知道
        # "到底起来没有" —— 光看进程在不在不算数。
        check("★ 启动后要确认它真的起来了（光看进程不算数）",
              "if exist" in script and "goto relaunch" in script,
              "重启之后没有确认步骤")
        check("★ 判断依据是程序自己写的启动脚印", "started.txt" in script
              and "{marker}" not in script, "脚本没去看 started.txt / 占位符没替换")
        check("★ 重开有上限（连试 3 次就不试了，别死循环）",
              "lss 3 goto relaunch" in script)
        # 真机事故：那段 `tasklist | findstr` 轮询会**永久卡住** ——
        # cmd 等 findstr，findstr 等一个永远不来的 EOF，更新挂死 18 分钟。
        check("★ 不许有 tasklist|findstr 那种管道轮询（卡死过一次）",
              "tasklist" not in update._BAT and "findstr" not in update._BAT,
              "那段轮询把一次真实更新挂死了 18 分钟")
        check("脚本里带上了暂存目录路径", str(stage2) in script)
        check("脚本里带上了安装目录路径", str(inst) in script)
        check("失败会记日志（不然用户只看到'点完没反应'）",
              "canoe-update.log" in script)

        # 没准备好新版本时不该乱写脚本
        try:
            update.install_and_restart(tmp / "nope")
            check("新版本不存在应当报错", False)
        except update.UpdateError as exc:
            check("★ 新版本还没解出来就拒绝交班", exc.code == "no_staged", exc.code)

        # 不能拿安装目录自己换自己
        try:
            update.install_and_restart(inst)
            check("自己换自己应当报错", False)
        except update.UpdateError as exc:
            check('★ 拒绝「新版本=当前安装目录」这种调用', exc.code == "same_dir", exc.code)

        # 收拾残局
        update.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (update.CONFIG_DIR / "canoe-update.log").write_text("上次失败了", encoding="utf-8")
        old_dir = inst.parent / (inst.name + ".old")
        old_dir.mkdir(exist_ok=True)
        (old_dir / "Canoe.exe").write_bytes(b"MZ old")
        check("★ 能读出上次失败留下的日志", update.last_update_log() == "上次失败了",
              update.last_update_log())
        check("读完就把日志删了（只提醒一次）",
              not (update.CONFIG_DIR / "canoe-update.log").exists())
        update.cleanup_leftovers()
        check("★ 启动时顺手清掉上次没删成的东西（交班脚本 + 旧目录）",
              not bat.exists() and not old_dir.exists())
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
        inst = sand / "Canoe"
        stage = sand / "Canoe.update"
        (inst / "_internal").mkdir(parents=True, exist_ok=True)
        (stage / "_internal").mkdir(parents=True, exist_ok=True)
        (inst / "Canoe.exe").write_bytes(where_exe.read_bytes())    # 冒充"当前版本"
        (stage / "Canoe.exe").write_bytes(host_exe.read_bytes())    # 冒充"新版本"
        (inst / "_internal" / "old.dll").write_bytes(b"old")
        (stage / "_internal" / "new.dll").write_bytes(b"new")

        new_exe_bytes = host_exe.read_bytes()
        log = tmp / "canoe-update.log"
        marker = sand / "started.txt"
        bat = sand / "canoe-update.bat"
        bat.write_text(
            update._BAT.replace("{old_dir}", str(sand / "Canoe.old"))
            .replace("{old_name}", inst.name + ".old")
            .replace("{parent}", str(sand))
            .replace("{stage}", str(stage))
            .replace("{install}", str(inst))
            .replace("{name}", inst.name)
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
        check("★ 整个安装目录被换掉了（Canoe.exe 是新的那份）",
              (inst / "Canoe.exe").read_bytes() == new_exe_bytes,
              f"{(inst / 'Canoe.exe').stat().st_size} 字节")
        check("★ 新版本带的东西也在（_internal/new.dll）",
              (inst / "_internal" / "new.dll").is_file())
        check("★ 旧版本的东西没跟过来（_internal/old.dll）",
              not (inst / "_internal" / "old.dll").exists())
        check("暂存目录没了（是 move 不是 copy）", not stage.exists())
        check("旧的目录被收掉了", not (sand / "Canoe.old").exists())
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
            update._BAT.replace("{old_dir}", str(sand / "Canoe.old"))
            .replace("{old_name}", inst.name + ".old")
            .replace("{parent}", str(sand))
            .replace("{stage}", str(sand / "does-not-exist"))
            .replace("{install}", str(inst))
            .replace("{name}", inst.name)
            .replace("{log}", str(log))
            .replace("{marker}", str(marker))
            # 上限改成 2 次，等价逻辑但测试只要等几秒
            # （换目录那一步会失败：暂存目录根本不存在）
            .replace("lss 60 goto swap", "lss 2 goto swap"),
            encoding="ascii",
        )
        t0 = time.time()
        proc2 = subprocess.run(["cmd", "/c", str(fail_bat)], cwd=str(sand),
                               capture_output=True, text=True, timeout=90)
        took = time.time() - t0
        check("★ 换不动时会放弃（重试有上限，不会一直转）",
              took < 60, f"跑了 {took:.1f}s")
        # ★ 换失败必须把旧目录**还回去** —— 否则一次失败的更新会让用户
        #   手里连个程序都没有。
        check("★ 放弃时把旧目录还回来了（不能让用户没有程序）",
              (inst / "Canoe.exe").is_file(), str(sorted(p.name for p in sand.iterdir())))
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
        # 真拿一份 GBK 字节喂给它，确认不再崩。日志在配置目录里。
        update.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (update.CONFIG_DIR / "canoe-update.log").write_bytes("中文日志".encode("gbk"))
        check("★ 真喂一份 GBK 的日志也不崩", update.last_update_log() == "中文日志",
              "读法还是有问题")
        (update.CONFIG_DIR / "canoe-update.log").unlink(missing_ok=True)

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
    # 单文件 exe 的临时目录名是**确定性的**：`_MEI` + hex(pid*16+2)，里面编码着
    # 引导器的 pid（实测 5/5 对得上，不是随机数）。被强杀留下的残留目录会在
    # pid 被重用的那天**正好撞名** —— 这是"更新后第一次启动偶发起不来、手动
    # 再开一次又好了"最像的成因。所以启动时按 pid 清残留，但只清确定死掉的那批。
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

    gone = update.clean_stale_temp_dirs(root=fake_tmp, mine=current)
    check("★ 强杀留下的、pid 已经没了的 -> 清掉", not ours_dead.exists(), str(gone))
    check("★ 别的程序的 _MEI 一律不碰（误删等于砸人家饭碗）", others_dead.is_dir())
    check("★ pid 还活着的不碰（可能是正在启动的另一个实例）", ours_live.is_dir())
    check("★ 刚建出来的不碰（提权那个实例可能正在启动）", ours_fresh.is_dir())
    check("★ 当前进程自己用的那个不碰", current.is_dir())
    check("清掉的个数对得上", gone == 1, str(gone))
    _shutil.rmtree(fake_tmp, ignore_errors=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
