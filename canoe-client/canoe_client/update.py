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
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import config

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


#: 替换脚本。**全 ASCII** —— 批处理按控制台代码页读文件，掺中文会变乱码。
#:
#: 为什么非得绕这一圈：Windows 上正在运行的 exe 既删不掉也覆盖不了。
#: 任何"我先退出、退出前自己替换"的写法都死在"退出之后没人干活"。
#: 交给系统来做 —— cmd 等本进程真的没了（文件锁释放）再动手。
#:
#: 三个动作的顺序都是有原因的，别简化：
#:
#:   1. **等旧进程真的消失**，不是等文件锁松开。单文件 exe 启动时会解压到
#:      %TEMP%\_MEIxxxx，并且会顺手清理上一次留下的同名临时目录。新的
#:      这时候要是已经起来了，它自己的目录会被对方清掉，然后弹：
#:          Failed to load Python DLL '...python313.dll'
#:          LoadLibrary: 找不到指定的模块。
#:      用户在真机上就是这么栽的（更新完第一次启动起不来，再点一次才行）。
#:      tasklist 轮询能把这段窗口盖住。
#:   2. 换文件，失败就重试（旧进程收尾慢）。
#:   3. **换完再等三四秒才拉起来** —— 刚落盘的新 exe 会被杀毒软件实时扫描，
#:      扫的过程中去跑它，同样会撞上面的错。
_BAT = r"""@echo off
rem Canoe self-update: wait for the old process to exit, swap the exe, restart.
setlocal
cd /d "%~dp0"

set /a waited=0
:wait_old
rem Wait for OUR OWN pid, not "any Canoe.exe" - otherwise a second instance
rem the user happens to have open would stall this for the whole timeout.
rem findstr, not find: "find" collides with the MSYS/Git-Bash one, which
rem wins on PATH for anyone who has Git installed and then blows up with
rem "find: '1234': No such file or directory". findstr has no such twin.
tasklist /FI "PID eq {pid}" /NH /FO CSV 2>nul | findstr /C:"{pid}" >nul
if errorlevel 1 goto swap
set /a waited+=1
if %waited% geq 15 goto swap
rem ping is used as a sleep - "timeout" fails when stdin is redirected.
ping -n 2 127.0.0.1 >nul
goto wait_old

:swap
set /a tries=0
:retry
ping -n 2 127.0.0.1 >nul
move /y "{new}" "{cur}" >nul 2>&1
if not errorlevel 1 goto ok
set /a tries+=1
if %tries% lss 40 goto retry
echo [%date% %time%] could not replace "{cur}" > "{log}"
exit /b 1

:ok
rem let antivirus finish scanning the freshly written exe before running it.
ping -n 4 127.0.0.1 >nul
start "" "{cur}"

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
      写死在上面，只有两个路径是变量，且都来自 sys.executable 和自己算出来
      的文件名 —— 不掺任何外部输入（URL、版本号、服务端给的字符串都不进）。
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
    script = (
        _BAT.replace("{new}", str(new))
        .replace("{cur}", str(cur))
        .replace("{log}", str(log))
        # 只等**我们自己这个 pid** 消失。写成"等任何 Canoe.exe" 的话，
        # 用户正好开着第二个实例时这一等就是整整一个超时。
        .replace("{pid}", str(os.getpid()))
    )
    try:
        bat.write_text(script, encoding="ascii")
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
    for name in ("canoe-update.bat", "Canoe.exe.new"):
        try:
            (base / name).unlink(missing_ok=True)
        except OSError:
            pass


def last_update_log() -> str:
    """上次更新失败留下的说明（没有就是空串）。"""
    try:
        path = _current_exe().with_name("canoe-update.log")
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            path.unlink(missing_ok=True)
            return text
    except OSError:
        pass
    return ""


def update_cache_dir() -> Path:
    """安装包下到这儿。系统临时目录，重启会自己清。"""
    d = Path(tempfile.gettempdir()) / "canoe-update"
    d.mkdir(parents=True, exist_ok=True)
    return d
