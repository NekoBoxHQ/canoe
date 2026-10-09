"""客户端更新：查版本 -> 下载安装包 -> 换掉自己 -> 重启。

从服务端的 /api/client/latest 拉一个 JSON：

    { "version": "1.1.0", "url": "https://.../Canoe-1.1.0-win64.zip",
      "notes": "修复 TUN 快速重连卡顿", "sha256": "...", "size": 83276159 }

地址由 config.update_url 派生（服务端地址写死，见 config.py），
管理端在面板上「发布版本」就会生效，客户端不用改代码。

**为什么要 sha256**：这是全工程唯一一条"服务端说什么客户端就做什么"的路 ——
下载下来的东西会被直接执行。不校验等于把执行权交出去。
服务端发布时会算好摘要，这里核对不过就整个丢掉，绝不留半成品。

不引第三方版本比较库 —— 三段式版本号手写十几行就够，少一个依赖。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import CONFIG_DIR, config

TIMEOUT = 15
#: 下载用的超时是 (连接, 读取) 两段 —— 60MB 的包读一段就要一会儿，
#: 只给一个总数会在慢网络下中途断掉。
DOWNLOAD_TIMEOUT = (10, 60)
CHUNK = 256 * 1024


@dataclass
class UpdateInfo:
    latest: str
    current: str
    url: str = ""
    notes: str = ""
    size: int = 0
    sha256: str = ""
    min_version: str = ""

    @property
    def is_newer(self) -> bool:
        return compare_versions(self.latest, self.current) > 0

    @property
    def must_upgrade(self) -> bool:
        """低于服务端要求的版本 —— 必须升，不能"稍后再说"。"""
        return bool(self.min_version) and compare_versions(self.current, self.min_version) < 0


class UpdateError(Exception):
    def __init__(self, message: str, code: str = "update_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class DownloadCancelled(UpdateError):
    """用户自己按的取消 —— 不是故障，界面上不该报成错误。"""

    def __init__(self, message: str = "已取消下载") -> None:
        super().__init__(message, code="cancelled")


def parse_version(text: str) -> tuple[int, ...]:
    """从 'v1.2.3-beta' 里取出 (1, 2, 3)。取不到就返回空元组。"""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3])


def compare_versions(a: str, b: str) -> int:
    """a > b 返回 1，相等 0，小于 -1。位数不齐按 0 补。"""
    pa, pb = list(parse_version(a)), list(parse_version(b))
    if not pa or not pb:
        return 0
    width = max(len(pa), len(pb))
    pa += [0] * (width - len(pa))
    pb += [0] * (width - len(pb))
    return (pa > pb) - (pa < pb)


def human_size(n: int) -> str:
    if n <= 0:
        return "未知大小"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _session() -> requests.Session:
    """更新这条路用的会话。**不走系统代理**。

    跟 api.py 里同一个理由：更新地址就是服务端自己的域名，而代理配置是
    服务端下发的。让"取更新"依赖"代理能用"会成环 —— 代理一坏，连更新都
    下不下来，程序自己没法自愈。

    现实里踩到过：系统代理指向 127.0.0.1:20818 而内核已经退了，那条代理
    就是个死端口，requests 老老实实走它，于是"系统代理 + TUN 的时候点更新
    没反应"。直连就一直通。
    """
    s = requests.Session()
    s.headers.update({"User-Agent": "Canoe-Client/1.0"})
    s.trust_env = False
    return s


def check(url: str, current: str) -> UpdateInfo:
    """拉取更新信息。url 为空或格式不对会抛 UpdateError。"""
    if not url or not url.strip():
        raise UpdateError("未配置更新地址（阶段4 部署服务端后填入）")

    try:
        resp = _session().get(url.strip(), timeout=TIMEOUT,
                              verify=config.ca_bundle)
    except requests.exceptions.SSLError as exc:
        raise UpdateError(f"TLS 握手失败：{exc}", code="tls_error") from exc
    except requests.exceptions.ConnectionError as exc:
        raise UpdateError(f"连不上更新地址：{exc}", code="network") from exc
    except requests.exceptions.Timeout as exc:
        raise UpdateError("更新地址响应超时", code="timeout") from exc

    if resp.status_code >= 400:
        raise UpdateError(f"更新地址返回 HTTP {resp.status_code}", code="http_error")

    try:
        data = json.loads(resp.text)
    except ValueError as exc:
        raise UpdateError("更新信息不是合法 JSON", code="bad_json") from exc

    if not isinstance(data, dict) or not data.get("version"):
        raise UpdateError("更新信息里没有 version 字段", code="bad_json")

    return UpdateInfo(
        latest=str(data["version"]),
        current=current,
        url=str(data.get("url", "")),
        notes=str(data.get("notes", "")),
        size=int(data.get("size") or 0),
        sha256=str(data.get("sha256") or ""),
        min_version=str(data.get("min_version") or ""),
    )


# ----------------------------------------------------------------------
# 下载
# ----------------------------------------------------------------------
def download(
    url: str,
    dest: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    expected_sha256: str = "",
    expected_size: int = 0,
    cancelled=None,
) -> Path:
    """把安装包流式下载到 dest，边下边报进度，完了核对大小和摘要。

    on_progress(done, total) —— total 拿不到时是 0。
    cancelled 是一个 threading.Event，置位就中途放弃并删掉半成品。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not url:
        raise UpdateError("服务端没给下载地址", code="no_url")

    def note(done: int, total: int) -> None:
        if on_progress:
            on_progress(done, total)

    try:
        resp = _session().get(
            url, stream=True, timeout=DOWNLOAD_TIMEOUT,
            verify=config.ca_bundle,
        )
    except requests.exceptions.SSLError as exc:
        raise UpdateError(f"TLS 握手失败：{exc}", code="tls_error") from exc
    except requests.exceptions.ConnectionError as exc:
        raise UpdateError(f"连不上下载地址：{exc}", code="network") from exc
    except requests.exceptions.Timeout as exc:
        raise UpdateError("下载地址响应超时", code="timeout") from exc

    with resp:
        if resp.status_code >= 400:
            raise UpdateError(f"下载地址返回 HTTP {resp.status_code}", code="http_error")

        total = expected_size
        try:
            total = int(resp.headers.get("Content-Length") or 0) or expected_size
        except (TypeError, ValueError):
            total = expected_size

        digest = hashlib.sha256()
        done = 0
        note(0, total)
        try:
            with open(dest, "wb") as fh:
                for block in resp.iter_content(CHUNK):
                    if cancelled is not None and cancelled.is_set():
                        raise DownloadCancelled()
                    if not block:
                        continue
                    fh.write(block)
                    digest.update(block)
                    done += len(block)
                    note(done, total)
        except BaseException:
            # 半成品绝不留着 —— 下次进来会把它当成"下好了"
            dest.unlink(missing_ok=True)
            raise

    if expected_size and done != expected_size:
        dest.unlink(missing_ok=True)
        raise UpdateError(
            f"安装包大小不对（服务端说 {human_size(expected_size)}，拿到 {human_size(done)}）",
            code="size_mismatch",
        )

    # ★ 校验没通过就当没下过。这条路下载的东西是要拿去执行的。
    if expected_sha256:
        got = digest.hexdigest()
        if got.lower() != expected_sha256.strip().lower():
            dest.unlink(missing_ok=True)
            raise UpdateError(
                f"安装包校验失败（摘要对不上）\n服务端：{expected_sha256[:16]}…\n"
                f"实际：  {got[:16]}…\n文件已丢弃，请联系管理员确认发布包。",
                code="bad_digest",
            )

    note(done, total or done)
    return dest


# ----------------------------------------------------------------------
# 换掉自己
# ----------------------------------------------------------------------
def can_self_update() -> bool:
    """能不能自己换掉自己。只有打包成 exe 跑的时候才行 ——
    源码运行时"当前程序"是 python.exe，换它毫无意义。"""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def _current_exe() -> Path:
    return Path(sys.executable).resolve()


def staging_path() -> Path:
    """新程序先放哪儿。

    放**当前程序同一个目录**：替换时是同卷改名，一步到位，不用跨盘拷贝。
    也顺带把"这个目录能不能写"这件事提前问出来了 —— 写在 Program Files
    下面就当场失败，而不是等我们退了它才失败。
    """
    return _current_exe().with_name("Canoe.exe.new")


def prepare_update(archive: Path) -> Path:
    """从发布包里取出 Canoe.exe，放到当前程序旁边待用。返回它的路径。"""
    if not can_self_update():
        raise UpdateError("当前不是以安装包方式运行的，没法自动更新", code="not_frozen")

    archive = Path(archive)
    dest = staging_path()
    if not archive.is_file():
        raise UpdateError("安装包不见了，请重新下载", code="no_archive")

    try:
        with zipfile.ZipFile(archive) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            # 包里正常就一个 Canoe.exe；万一换了名字，取第一个 exe
            member = next((n for n in names if n.lower().endswith(".exe")), None)
            if member is None:
                raise UpdateError("安装包里没有 exe", code="bad_archive")
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
    except zipfile.BadZipFile as exc:
        raise UpdateError("安装包损坏（解不开）", code="bad_archive") from exc
    except PermissionError as exc:
        dest.unlink(missing_ok=True)
        raise UpdateError(
            f"没有权限在 {dest.parent} 下写入。\n"
            "把轻舟换到桌面或其它自己的目录再更新，或者手动解压覆盖。",
            code="no_permission",
        ) from exc
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"写入新程序失败：{exc}", code="io_error") from exc

    if dest.stat().st_size < (1 << 20):
        dest.unlink(missing_ok=True)
        raise UpdateError("取出来的程序太小，包可能是坏的", code="bad_archive")
    # ⚠ 头两个字节要单独读出来、**出了 with 再删**。在 with 里面 unlink
    #   会撞上 WinError 32（文件还开着），删不掉，还会把 PermissionError
    #   原样抛给用户 —— 明明该说的是"这不是个 Windows 程序"。
    with open(dest, "rb") as fh:
        magic = fh.read(2)
    if magic != b"MZ":
        dest.unlink(missing_ok=True)
        raise UpdateError("取出来的不是 Windows 程序", code="bad_archive")
    return dest


def start_marker() -> Path:
    """启动脚印：新版本界面真的起来之后写这个文件。

    更新后的重启是 .bat 收尾的，而它没别的好办法知道"新版本到底起来没有"。
    光看进程在不在不行 —— 引导器解压失败时也会留一个挂着的进程（还常常挂
    着一个原生错误框），那不算起来。所以让程序自己在界面起来之后留个脚印。
    """
    return CONFIG_DIR / "started.txt"


def mark_started() -> None:
    """记一笔"我起来了"。由 app.main() 在界面真的起来之后调。"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        start_marker().write_text(str(int(time.time())), encoding="ascii")
    except OSError:
        pass


#: 单文件 exe 解压出来的临时目录名：`_MEI` + hex(pid*16+2)，补到 8 位。
#: **是确定性的，不是随机数** —— 实测 5/5：名字里那个值 >> 4 就是引导器的 pid。
_MEI_RE = re.compile(r"_MEI([0-9a-fA-F]+)")

#: 目录要多老才敢动（秒）。
#:
#: pid 那一道不够稳：**刚退出的进程，只要还有谁攥着它的句柄，OpenProcess
#: 照样成功**（实测：`cmd /c exit` 之后问那个 pid，回答是"还活着"，而
#: tasklist 里已经没有了）。所以再加一道时间护栏 —— 几分钟前刚建出来的目录
#: 一律不碰，那多半是另一个正在启动的实例（比如提权后那个）。误删它正好就是
#: 我们要修的那个故障，宁可少清一个。
_STALE_AFTER = 3600


def _pid_alive(pid: int) -> bool:
    """pid 还在不在。

    ⚠ Windows 上**不能**用 `os.kill(pid, 0)` —— CPython 在 Windows 上的
      os.kill 遇到非控制台信号会直接 TerminateProcess，那等于把人家杀掉。
      这里用 OpenProcess 问一下，问不到就当它没了。
    """
    if os.name != "nt":
        # 非 Windows 没有这种打包形态，保守当它活着（不删）
        return True
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def clean_stale_temp_dirs(root: Path | None = None, mine: Path | None = None) -> int:
    """清掉自己被强杀后留在 %TEMP% 的 _MEI 目录，返回清掉几个。

    单文件 exe 每次启动解压约 80MB 到 `%TEMP%\\_MEIxxxx`，正常退出时引导器
    自己会删。但**被强杀**（任务管理器结束进程、崩溃、被安全软件掐掉）就删不掉，
    目录原地留下 —— 实测这台机器上攒了 13 个、约 1.9GB。

    光占地方还不是最要命的：**这个名字是确定性的**，里面编码着引导器的 pid
    （见 _MEI_RE）。Windows 会重用 pid，一旦重用到同一个 pid，新进程算出来的
    名字和那个残留目录**一模一样**。这就是"更新后第一次启动偶发起不来、手动
    再开一次又好了"最像的成因 —— 也是为什么我按这种规律反复启动复现不出来：
    每次拿到的都是新 pid，而残留早被我清过一次。

    所以按 pid 清，而且只清**确定是死的**那一批：

      · 目录是我们的          —— 里面有 assets/canoe.ico。别的 PyInstaller
                                 程序一律不碰，误删等于砸人家饭碗。
      · 名字里那个 pid 不在了 —— 还活着的一律不碰，那可能是另一个正在启动
                                 的实例，删了正好复现我们要修的那个故障。
      · 够老（超过 _STALE_AFTER）—— pid 这道不够稳，见 _STALE_AFTER 的注释。
      · 不是当前进程自己用的那个（sys._MEIPASS）。

    几种判不准的情况（pid 被重用、句柄还攥在别人手里）都会**少清一个**，
    方向都是安全的 —— 宁可留着占地方，也不能误删一个活着的实例。
    """
    root = Path(root) if root is not None else Path(tempfile.gettempdir())
    if mine is None:
        base = getattr(sys, "_MEIPASS", None)
        mine = Path(base).resolve() if base else None
    else:
        mine = Path(mine).resolve()

    cleared = 0
    try:
        candidates = list(root.glob("_MEI*"))
    except OSError:
        return 0

    for path in candidates:
        try:
            m = _MEI_RE.fullmatch(path.name)
            if m is None or not path.is_dir():
                continue
            if mine is not None and path.resolve() == mine:
                continue
            if not (path / "assets" / "canoe.ico").is_file():
                continue        # 不是我们的包，别动
            try:
                if time.time() - path.stat().st_mtime < _STALE_AFTER:
                    continue    # 刚建出来的，多半是另一个正在启动的实例
            except OSError:
                continue
            owner = int(m.group(1), 16) >> 4
            if owner and _pid_alive(owner):
                continue        # 人家还活着（或者 pid 被别的活进程占了）
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                cleared += 1
        except (OSError, ValueError):
            continue
    return cleared


def _write_bat(path: Path, script: str) -> None:
    """把替换脚本写下来。

    ★ 编码按**系统 ANSI 代码页**（Windows 上是 `mbcs`），不是 UTF-8。
      cmd 读 .bat 用的是控制台代码页 —— 中文机器上就是 GBK。安装目录带中文
      （用户名是中文时 `%USERPROFILE%\\Desktop` 就是）的时候，用 ascii 写会
      直接 UnicodeEncodeError（连更新都发不出去），用 utf-8 写则是 cmd 拿到
      一串乱码路径，move 和 start 全落空。纯 ASCII 路径下 mbcs 与 ascii 写
      出来完全一样。
    """
    try:
        path.write_text(script, encoding="mbcs")
    except (LookupError, UnicodeEncodeError):
        # 极端兜底：ANSI 也装不下这个字符。至少别让整个更新失败。
        path.write_text(script, encoding="utf-8", errors="replace")


#: 替换脚本。**通篇 ASCII** —— 批处理按控制台代码页读文件，掺中文会变乱码。
#:
#: 为什么非得绕这一圈：Windows 上正在运行的 exe 删不掉，也覆盖不了。
#: 任何"我先退出、退出前自己替换"的写法都死在"退出之后没人干活"。
#: 交给系统来做 —— 写个 .bat，让 cmd 去换。
#:
#: 三件事，顺序都有原因：
#:
#:   1. **换文件：先把旧的改名挪开，再把新的放进去**。
#:
#:      ★ 2026-10-10 改的，旧写法真出事了。原来是 `move /y new cur` 加重试
#:        循环 —— 而 move 要覆盖就得先删掉 cur，偏偏**正在运行的 exe 删不掉**
#:        （改名可以：实测改名成功、删除 WinError 5 拒绝访问）。于是旧进程
#:        多活一会儿，move 就一次都成功不了，那个循环得 ping 满一分钟才认输。
#:        用户的原话是"更新一直 ping 个没停"，而且更新**压根没装上**
#:        （桌面上还是旧版本）。`ren cur cur.old` 之后再 move 就没有这个坎，
#:        新版本立刻到位。
#:
#:      ⚠ 别再加那段 `tasklist ... | findstr ...` 的轮询。加过，出事了：
#:        那个管道会**永久卡住**，实测把一个真实更新挂死了 18 分钟 ——
#:        cmd 一直在等它的子进程 findstr，而 findstr 一直在等一个永远不
#:        来的输入结束。用户那边的表现就是"点了更新，程序关了，然后
#:        什么都没发生，桌面上留着 Canoe.exe.new 和这个 .bat"。
#:
#:   2. **等旧进程真的退干净，再拉新的**。旧 exe 改名成了 .old，但仍然被旧
#:      进程占着、删不掉 —— **删得掉就说明它退了**，拿这个当判据，不用去问
#:      系统"那个 pid 还在不在"。不等到就拉起新的，新实例会撞上单实例锁、
#:      白起一次。等不到就写日志收摊：文件已经换好了，下次打开就是新版本。
#:
#:   3. **拉起来之后确认它真的起来了，没起来就重开**。
#:
#:      ★ 这一条是真出过事才加的。用户报"更新后重启报错，找不到模块"，
#:        截图是引导器的原生框：
#:            Failed to load Python DLL '...\\_MEI00003ae42\\python313.dll'.
#:            LoadLibrary: 找不到指定的模块。
#:        但**手动再开一次就好了** —— 说明 exe 本身没坏，是这一次解压/加载
#:        偶发失败（杀软正在翻一个刚写出来的二进制）。既然重开一次能好，
#:        就让脚本自己重开。依据是程序起来后会写的那个 started.txt
#:        （见 mark_started）；连试三次都不行才写日志认输，日志会在下次
#:        启动时弹给用户看。
_BAT = r"""@echo off
rem Canoe self-update: swap the exe once the old process lets go, start the new
rem one, and restart it until it really comes up.
setlocal
cd /d "%~dp0"

rem --- 1) swap -------------------------------------------------------------
rem Rename the old exe aside FIRST. Renaming a running exe works; deleting one
rem does not. So a plain "move /y" onto it can NEVER succeed while the old
rem process is alive - the retry loop just pings for a minute, which is exactly
rem what a user saw ("the update keeps pinging and never stops", with the new
rem version not installed at all).
rem NOTE: ren's second argument is a NAME, not a path - passing a full path
rem silently fails. We cd'd to the exe's folder above, so bare names are what
rem we use here. Getting this wrong is invisible: ren fails, move then fails
rem too, and you only notice it because nothing ever updates.
set /a tries=0
:swap
if exist "{cur}" del "{old_name}" >nul 2>&1
if exist "{cur}" ren "{cur_name}" "{old_name}" >nul 2>&1
move /y "{new}" "{cur}" >nul 2>&1
if not exist "{cur}" goto unswap
if exist "{new}" goto unswap
goto swapped
:unswap
rem Did not land. Put the old one back - a failed update must never leave the
rem user with no exe at all (the old one is sitting there as .old).
if not exist "{cur}" ren "{old_name}" "{cur_name}" >nul 2>&1
rem ping is used as a sleep - "timeout" fails when stdin is redirected.
ping -n 2 127.0.0.1 >nul
set /a tries+=1
if %tries% lss 30 goto swap
echo [%date% %time%] could not replace "{cur}" > "{log}"
exit /b 1
:swapped

rem --- 2) wait until the OLD build has finished deleting its own unpack dir --
rem !! This is the fix for "Failed to load Python DLL" after an update. !!
rem
rem A onefile exe unpacks to %TEMP%\_MEI<hex(pid*16+2)> - the name is DERIVED
rem FROM THE PID. Windows hands out a just-freed pid again very quickly, so the
rem new process often computes THE SAME directory name the previous build had,
rem while that build's bootloader is still deleting it. The new process writes
rem python313.dll into it, the old one removes it, and loading the DLL fails:
rem     Failed to load Python DLL '...\_MEI00005842\python313.dll'
rem The user's screenshot showed exactly that - and decoding that name gives
rem the pid of the instance they were RUNNING at the time.
rem
rem So do not launch until the previous unpack directory is gone. The path
rem below is this process's own sys._MEIPASS, substituted at write time.
set /a waited=0
:waitmei
if not exist "{old_mei}" goto launch
ping -n 2 127.0.0.1 >nul
set /a waited+=1
if %waited% lss 30 goto waitmei
rem Still there after a minute - carry on anyway, the retries below cover it.

:launch
rem The new exe touches {marker} once its window is up - nothing else proves
rem it, because a failed unpack still leaves a process behind (sitting on a
rem native error box). Three shots, then roll back (see step 3).
set /a boots=0
:relaunch
rem If the previous attempt failed, it left a process stuck on a NATIVE ERROR
rem BOX ("Failed to load Python DLL ..."). Sweep it away before trying again -
rem otherwise the boxes pile up and the whole update looks alarming. Never
rem touch anything on the very first attempt (nothing of ours is running yet).
if %boots% gtr 0 taskkill /F /IM "Canoe.exe" >nul 2>&1
del "{marker}" >nul 2>&1
rem Settle first, so we do not race the antivirus scan of the fresh exe.
ping -n 5 127.0.0.1 >nul
start "" "{cur}"
set /a waited=0
:waitup
ping -n 3 127.0.0.1 >nul
if exist "{marker}" goto done
set /a waited+=1
if %waited% lss 12 goto waitup
set /a boots+=1
if %boots% lss 3 goto relaunch

rem --- 4) all three shots failed: roll back ---------------------------------
rem Reaching here means the NEW exe cannot start in this environment (a real
rem user hit "Failed to load Python DLL" here and we could not reproduce it).
rem The previous program is still sitting there as .old - go back to it. A user
rem stuck on an older version is far better than a user with a program that
rem will not open.
rem Move the broken one aside first (it may still be holding the name behind a
rem native error box), then give .old its name back and start it.
echo [%date% %time%] the new version did not come up; rolled back to the previous one > "{log}"
rem Sweep away whatever is stuck on a native error box before starting the old one.
taskkill /F /IM "Canoe.exe" >nul 2>&1
if not exist "{old_name}" exit /b 0
ren "{cur_name}" "{bad_name}" >nul 2>&1
ren "{old_name}" "{cur_name}" >nul 2>&1
if not exist "{cur}" exit /b 0
del "{marker}" >nul 2>&1
ping -n 5 127.0.0.1 >nul
start "" "{cur}"
exit /b 0

:done
rem The new build is confirmed up, so the rollback copy can go (if it will not
rem delete, fine - cleanup_leftovers() collects it next start).
del "{old_name}" >nul 2>&1

rem Deliberately NOT "del %~f0". Deleting the running batch file makes cmd
rem fail to read its next line and exit 1 with "The batch file cannot be
rem found" - noise in the logs for no gain. The leftover .bat is removed by
rem cleanup_leftovers() on the next startup, which we run anyway.
"""


def install_and_restart(new_exe: Path | None = None) -> Path:
    """安排好替换与重启，返回那个 .bat 的路径。**调用方下一步就该退出程序。**

    ★ 为什么不能自己动手：Windows 上正在运行的 exe 删不掉也覆盖不了。
      写一个 .bat 交给系统，等本进程退出（文件锁释放）后由它来
      `move /y` + `start`，这是这件事的标准解法。

    ⚠ 这个 .bat 是**本程序唯一会生成、而且之后会被执行的东西**，所以内容
      写死在上面，只有三个路径是变量，且都来自 sys.executable / 配置目录 /
      自己算出来的文件名 —— 不掺任何外部输入（URL、版本号、服务端给的
      字符串都不进）。
    """
    if not can_self_update():
        raise UpdateError("当前不是以安装包方式运行的，没法自动更新", code="not_frozen")

    cur = _current_exe()
    new = Path(new_exe).resolve() if new_exe else staging_path()
    if not new.is_file():
        raise UpdateError("还没准备好新程序", code="no_staged")
    if new == cur:
        raise UpdateError("新程序跟当前程序是同一个文件", code="same_file")

    bat = cur.with_name("canoe-update.bat")
    log = cur.with_name("canoe-update.log")
    # ⚠ 顺序：`{cur_name}` / `{old_name}` 必须排在 `{cur}` **前面** ——
    #   `{cur}` 是它们的前缀，先替换 `{cur}` 会把 `{cur_name}` 拆成
    #   `<路径>_name`。ren 只认名字不认路径，错了会静默失败（见 _BAT 里的注释）。
    # `{old_mei}` = 我们自己这次用的解压目录（onefile 才有）。交班脚本要等
    # 它消失之后才拉新版本 —— 新进程很可能算出一模一样的名字（pid 派生），
    # 而我们的引导器此刻正在删它。源码运行时没有 _MEIPASS，给个永远不存在的
    # 路径，那句 `if not exist` 直接就过了。
    old_mei = getattr(sys, "_MEIPASS", None) or str(CONFIG_DIR / "no-such-meipass")

    script = (
        _BAT.replace("{old_name}", cur.name + ".old")
        .replace("{bad_name}", cur.name + ".bad")
        .replace("{cur_name}", cur.name)
        .replace("{old_mei}", str(old_mei))
        .replace("{new}", str(new))
        .replace("{cur}", str(cur))
        .replace("{log}", str(log))
        .replace("{marker}", str(start_marker()))
    )
    try:
        _write_bat(bat, script)
    except OSError as exc:
        raise UpdateError(f"写替换脚本失败：{exc}", code="io_error") from exc

    # 新进程要能活过我们这一下，所以脱离控制台、另起进程组
    detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(cur.parent),
            creationflags=detached,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise UpdateError(f"启动替换脚本失败：{exc}", code="io_error") from exc
    return bat


def cleanup_leftovers() -> None:
    """收拾上次更新留下的残渣。启动时叫一次。

    正常情况下 .bat 跑完会自己删掉，但万一没删成（断电、被杀），
    下次启动顺手清一清 —— 也把上次失败的日志读出来报给用户。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        base = _current_exe().parent
    except OSError:
        return
    # Canoe.exe.old 是交班时被改名挪开的旧程序（见 _BAT：改名是唯一能对
    # 运行中的 exe 做的事）。旧进程退出前它删不掉，所以留到这次启动来收。
    for name in ("canoe-update.bat", "Canoe.exe.new", "Canoe.exe.old", "Canoe.exe.bad"):
        try:
            (base / name).unlink(missing_ok=True)
        except OSError:
            pass


def last_update_log() -> str:
    """上次更新失败留下的说明（没有就是空串）。"""
    try:
        path = _current_exe().with_name("canoe-update.log")
        if not path.is_file():
            return ""
        # ⚠ 这文件是 cmd 的 `echo ... > file` 写出来的，用的是**系统 ANSI
        #   代码页**（中文机器上是 GBK），不是 UTF-8。按 utf-8 硬读会
        #   UnicodeDecodeError，带 errors="replace" 又能读出满屏问号。
        #   所以按顺序试，谁先成功算谁。
        raw = path.read_bytes()
        text = ""
        for enc in ("utf-8", "mbcs", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = raw.decode("utf-8", "replace")
        path.unlink(missing_ok=True)
        return text.strip()
    except OSError:
        pass
    return ""


def update_cache_dir() -> Path:
    """安装包下到这儿。系统临时目录，重启会自己清。"""
    d = Path(tempfile.gettempdir()) / "canoe-update"
    d.mkdir(parents=True, exist_ok=True)
    return d
