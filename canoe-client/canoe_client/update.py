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


#: 单文件 exe 解压出来的临时目录名。跟着 PyInstaller 的引导器源码对过：
#:
#:     swprintf(prefix, 16, L"_MEI%08x", _getpid());   /* pyi_utils_win32.c */
#:     application_home_dir_w = _wtempnam(tempdir_path, prefix);
#:
#: 也就是 `_MEI` + 引导器 pid 的 8 位十六进制 + `_wtempnam` 补的那一位。
#: 实测这台机器上 6/6 对得上（pid 3464 -> `_MEI0000d882`），所以名字右移 4 位
#: 就能拿回那个 pid —— 下面用它判断"当初建这个目录的实例还在不在"。
_MEI_RE = re.compile(r"_MEI([0-9a-fA-F]+)")

#: 目录要多老才敢动（秒）。
#:
#: pid 那一道不够稳：**刚退出的进程，只要还有谁攥着它的句柄，OpenProcess
#: 照样成功**（实测：`cmd /c exit` 之后问那个 pid，回答是"还活着"，而
#: tasklist 里已经没有了）。所以再加一道时间护栏 —— 几分钟前刚建出来的目录
#: 一律不碰，那多半是另一个正在启动的实例（比如提权后那个）。宁可少清一个，
#: 也不要误删一个正在启动的实例。
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

    为什么留在盘上：引导器退出前要把这个目录整个删掉，而我们启动的内核
    `bin/sing-box.exe` 就住在里面 —— 内核还活着（或者刚被强杀、句柄还没完全
    放手）时那个文件删不掉，整个目录就跟着留下了。

    ⚠ 别再把这个和那句 "Failed to load Python DLL" 扯上关系 —— 那是误会。
      真病根是环境变量继承（见 _BAT 第 2 段），而且引导器建目录走的是
      `_wtempnam()`，本来就会挑一个不重名的。这里清残留纯粹是收拾地方，
      不承担正确性 —— 清了更好，清不掉也不影响这次能不能起来。

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
            # 两个都算"我们的"：完整的包里有 assets/canoe.ico；只解压到一半就
            # 死掉的残骸可能还来不及有 assets/，但 bin/sing-box.exe 已经落盘了。
            if not ((path / "assets" / "canoe.ico").is_file()
                    or (path / "bin" / "sing-box.exe").is_file()):
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
#: 三段，顺序都有原因：
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
#:      ⚠ `ren` 的第二个参数必须是**裸名字**，不是路径 —— 给完整路径会
#:        静默失败，而这一步错了看不出来：ren 失败、move 跟着失败，你只会
#:        发现"怎么更新都不动"。下面传的是 cur.name。
#:
#:   2. **在一个干净的环境里拉起新版本。★★ 这就是那个 DLL 报错的病根 ★★**
#:
#:      这个脚本是老进程的子进程，**继承了老进程的 PyInstaller 环境变量**。
#:      而新 exe 被换到了**同一个路径**上，于是引导器里那句继承判定
#:
#:          if (_PYI_ARCHIVE_FILE == 自己的归档文件名)  ->  继承父进程的环境
#:
#:      成立 —— 新进程认定自己是"父进程已经解压好的那一半"，**不再解压**，
#:      直接沿用 `_PYI_APPLICATION_HOME_DIR` 指的那个**老 _MEI 目录**。而那个
#:      目录此刻正被老进程的引导器删除：
#:
#:          Failed to load Python DLL '...\_MEI000005842\python313.dll'.
#:          LoadLibrary: 找不到指定的模块。
#:
#:      那个目录名里编码的 pid 属于**老实例**，所以看起来像"pid 撞名"。不是。
#:      真正的原因是我们把新 exe 放到了同一个路径上，让名字比对通过了。
#:      2026-10-10 在这台机器上确定性复现过：把这几个变量喂进去，exe 就挂在
#:      那句报错上；再加上 PYINSTALLER_RESET_ENVIRONMENT=1 就一切正常。
#:      手工双击一直没事，是因为 Explorer 的环境里没有这些变量。
#:
#:      `PYINSTALLER_RESET_ENVIRONMENT=1` 是引导器**自带的开关**，含义就是
#:      "我是一个全新的顶层进程"：它会清掉继承来的那些值、从头解压。下面同时
#:      把变量显式清空，不让整个修复吊在一个开关上。
#:
#:   3. **确认它真起来了；没起来就把旧版本换回去**。
#:
#:      光看进程在不在不算数 —— 引导器解压失败时也会留一个挂在原生错误框上的
#:      进程。判据是程序自己写的启动脚印（started.txt，见 mark_started）。
#:      **只试一次**：以前是连试三次、每次开跑前 taskkill 收弹窗，用户看到的
#:      就是"一堆弹窗"（原话："更新环境要静默，不要一堆弹窗，这样让客户感觉
#:      不安全"）。起不来就直接退回旧版本 —— 宁可让人用旧版本，也不能让人
#:      手里是个打不开的程序。
_BAT = r"""@echo off
rem Canoe self-update handover. Pure ASCII: cmd reads .bat in the console code page.
setlocal
cd /d "%~dp0"

rem --- 1) swap -----------------------------------------------------------
rem A running exe cannot be deleted or overwritten, but it CAN be renamed. So
rem rename the old one aside first (that always works), then drop the new one
rem in. If the move did not land, the source file is still there - retry.
rem NOTE: ren's second argument is a NAME, not a path. A full path fails
rem silently, and you only notice because nothing ever updates.
rem
rem Two things this must never do:
rem   - start a program that is not there: on Windows that pops a modal
rem     "Windows cannot find ..." box, which nothing can dismiss, so the whole
rem     handover hangs. Only :swapped may reach "start", and only with an exe
rem     that is proven to be on disk.
rem   - count "the old exe is still in place" as success. The move landing is
rem     what we check, not the mere presence of a file with that name.
set /a tries=0
:swap
if not exist "{new}" goto giveup
if exist "{cur}" del "{old_name}" >nul 2>&1
if exist "{cur}" ren "{cur_name}" "{old_name}" >nul 2>&1
move /y "{new}" "{cur}" >nul 2>&1
if exist "{new}" goto retry
if exist "{cur}" goto swapped
:retry
if not exist "{cur}" ren "{old_name}" "{cur_name}" >nul 2>&1
rem ping is used as a sleep - "timeout" fails when stdin is redirected.
ping -n 2 127.0.0.1 >nul
set /a tries+=1
if %tries% lss 30 goto swap
:giveup
echo [%date% %time%] could not replace "{cur}" > "{log}"
exit /b 1
:swapped

rem --- 2) start the new build in a CLEAN PyInstaller environment ----------
rem !! THIS IS THE FIX for "Failed to load Python DLL" after an update !!
rem
rem This script is a child of the OLD Canoe process, so it inherited that
rem process's PyInstaller environment. The new exe is dropped in AT THE SAME
rem PATH as the old one, so the bootloader's inherit check
rem
rem     if (_PYI_ARCHIVE_FILE == our own archive filename) -> keep parent env
rem
rem passes. The new process then concludes it is the child half of a onefile
rem parent that has already unpacked, so it does NOT unpack - it just uses
rem _PYI_APPLICATION_HOME_DIR, i.e. the OLD _MEI directory, which the old
rem process is deleting at that very moment:
rem
rem     Failed to load Python DLL '...\_MEI000005842\python313.dll'.
rem     LoadLibrary: The specified module could not be found.
rem
rem The pid encoded in that directory name belongs to the OLD instance, which
rem is why this looked like a pid collision. It is not - it is the move onto
rem the same path that makes the name check pass.
rem
rem Starting the exe by hand always worked because Explorer's environment
rem carries none of these variables. Reproduced deterministically here:
rem feeding in the three variables below makes the exe hang on that very
rem dialog; adding PYINSTALLER_RESET_ENVIRONMENT=1 makes it start normally.
rem
rem PYINSTALLER_RESET_ENVIRONMENT=1 is the bootloader's own switch for "I am a
rem brand-new top-level process": it wipes the inherited values and unpacks
rem from scratch. We clear the variables explicitly as well, so the fix does
rem not hinge on a single knob.
set PYINSTALLER_RESET_ENVIRONMENT=1
set _PYI_APPLICATION_HOME_DIR=
set _PYI_ARCHIVE_FILE=
set _PYI_PARENT_PROCESS_LEVEL=
set _PYI_SPLASH_IPC=
del "{marker}" >nul 2>&1
rem Settle a moment first, so we do not race the antivirus scan of the fresh exe.
ping -n 5 127.0.0.1 >nul
start "" "{cur}"

rem --- 3) confirm the new build came up; otherwise roll back ---------------
rem started.txt is written by the program itself once its window is up (see
rem mark_started). Do not wait for a process instead: a failed unpack also
rem leaves a process behind, sitting on a native error box.
set /a waited=0
:waitup
ping -n 3 127.0.0.1 >nul
if exist "{marker}" goto done
set /a waited+=1
if %waited% lss 10 goto waitup

rem Reaching here means the NEW exe does not start in this environment. The
rem previous program is still sitting there as .old - go back to it. A user
rem stuck on an older version is far better than a user with a program that
rem will not open. Move the broken one aside first (it may still be holding
rem the name behind a native error box), then give .old its name back.
echo [%date% %time%] the new version did not come up; rolled back to the previous one > "{log}"
if not exist "{old_name}" exit /b 0
taskkill /F /IM "{cur_name}" >nul 2>&1
ren "{cur_name}" "{bad_name}" >nul 2>&1
ren "{old_name}" "{cur_name}" >nul 2>&1
if not exist "{cur}" exit /b 0
del "{marker}" >nul 2>&1
start "" "{cur}"
exit /b 0

:done
rem The new build is confirmed up, so the rollback copy can go (if it will not
rem delete, fine - cleanup_leftovers() collects it next start).
del "{old_name}" >nul 2>&1

rem Deliberately NOT "del %~f0": deleting the running batch file makes cmd fail
rem to read its next line and exit 1 with "The batch file cannot be found" -
rem noise for no gain. The leftover .bat is removed by cleanup_leftovers() on
rem the next startup, which we run anyway.
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
    # 注意占位符里**没有**旧程序那个解压目录（sys._MEIPASS）—— 早先加过一段
    # "等它消失再启动"，方向正好是反的：报错的病根是环境变量把新进程引到了
    # 那个目录上，等它消失等于保证新进程一定扑空。见 _BAT 第 2 段。
    script = (
        _BAT.replace("{old_name}", cur.name + ".old")
        .replace("{bad_name}", cur.name + ".bad")
        .replace("{cur_name}", cur.name)
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
    # ★ 交给脚本的环境必须是**洗过的**。我们自己是 PyInstaller 单文件进程，
    #   环境里有 _PYI_ARCHIVE_FILE / _PYI_APPLICATION_HOME_DIR /
    #   _PYI_PARENT_PROCESS_LEVEL；原样漏下去，新 exe（路径和我们完全相同）
    #   就会被引导器判定成"父进程已经解压好的那一半"，不重新解压、直接用我们
    #   这个正在被删的 _MEI 目录 —— 那就是那句 "Failed to load Python DLL"。
    #   脚本里自己也清了一遍（见 _BAT 第 2 段），这里再挡一道：从源头就不漏。
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(cur.parent),
            creationflags=detached,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
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
