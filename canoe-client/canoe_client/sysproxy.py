"""Windows 系统代理设置（Internet Settings）。

启航时备份用户原有设置，靠岸时**原样还原**。只影响「使用系统代理」的程序
（浏览器、大部分 Electron 应用等）。

★ 这里踩过一个能把用户彻底搞断网的坑，改代码前务必读完：

**坑：脏备份黑洞。**
`ProxyEnable=1` 而 `ProxyServer` 指向 `127.0.0.1:20818`、但内核已经不在时，
浏览器会把所有请求发到一个没人接的端口 —— 用户看到的就是「断网」。

要命的是它会**自我固化**：程序被强杀/崩溃后，注册表里留着这个脏状态；
下次启动时老逻辑会把它当成"用户的原始设置"备份下来，靠岸时再原样写回去。
于是脏一次就永久脏，怎么重启都没用。已复现（见 `tests/test_sysproxy.py`）。

由此定下两条铁律：

1. **绝不自食其果** —— 备份时如果发现当前值就是我们自己设的，
   那不是用户的设置，直接当成"没代理"。（`_clean_snapshot`）
2. **绝不让流量指向死端口** —— 靠岸时**先还原系统代理，再停内核**；
   另外启动时自愈一次，把上次崩溃留下的脏状态清掉。（`heal_on_start`）

备份同时落盘（`%APPDATA%\\Canoe\\proxy_backup.json`），
这样即使进程崩溃，下次启动也能把用户原本的设置还回去，而不是简单粗暴地关掉了事。
"""
from __future__ import annotations

import ctypes
import json
import socket
import winreg
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR

INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

# 常见内网地址不要走代理
DEFAULT_BYPASS = "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;192.168.*;<local>"

_INTERNET_OPTION_REFRESH = 37
_INTERNET_OPTION_SETTINGS_CHANGED = 39

#: 进程内备份（正常路径用它还原）。值为 (数据, 注册表类型) 二元组。
_backup: dict[str, Any] = {}

#: 本进程是否"设过代理且还没还回去"。
#: 注意不能拿 `_backup` 是否为空当判据 —— 用户原本就没代理时备份就是空的 {}，
#: 但代理确实是我们开的，靠岸时照样得关掉。所以单独用一个布尔量记。
_active: bool = False

#: 备份落盘位置：崩溃后还能把用户原本的设置还回去
BACKUP_FILE = CONFIG_DIR / "proxy_backup.json"

#: 我们设代理用的端口。用来认出"注册表里这条是不是我们自己写的"。
_our_port: int | None = None


def _value(entry: Any) -> Any:
    """`_read_values()` 里存的是 (数据, 类型) 二元组，取出数据本身。"""
    return entry[0] if isinstance(entry, tuple) else entry


# --------------------------------------------------------------------------
# 读写注册表
# --------------------------------------------------------------------------


def _read_values() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_READ) as k:
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
                try:
                    out[name] = winreg.QueryValueEx(k, name)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
    return out


def _refresh_wininet() -> None:
    """通知系统和已运行的程序设置变了，否则要重启浏览器才生效。"""
    try:
        wininet = ctypes.windll.Wininet
        wininet.InternetSetOptionW(0, _INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, _INTERNET_OPTION_REFRESH, 0, 0)
    except OSError:
        pass


# --------------------------------------------------------------------------
# 认出自己
# --------------------------------------------------------------------------


def _server_points_at(server: Any, port: int) -> bool:
    """ProxyServer 形如 ``http=127.0.0.1:20818;https=...;socks=...``。

    判断其中是否有哪一段指向 ``127.0.0.1:<port>``（也就是我们自己设的端口）。
    """
    if not isinstance(server, str) or not server:
        return False
    want = str(int(port))
    for part in server.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            part = part.split("=", 1)[1]
        host, sep, p = part.rpartition(":")
        if sep and p == want and host.strip().lower() in ("127.0.0.1", "localhost"):
            return True
    return False


def is_our_proxy(values: dict[str, Any] | None = None, port: int | None = None) -> bool:
    """注册表里当前挂着的是不是**我们自己**设的代理（开着且指向本机端口）。"""
    values = _read_values() if values is None else values
    if _value(values.get("ProxyEnable")) != 1:
        return False
    use_port = _our_port if port is None else port
    if use_port is None:
        return False
    return _server_points_at(_value(values.get("ProxyServer")), int(use_port))


def has_backup() -> bool:
    """当前进程有没有"设过代理但还没还回去"。靠它决定靠岸时要不要清理。"""
    return _active


def _port_alive(port: int, timeout: float = 0.25) -> bool:
    """本机端口上有没有人在监听（内核是不是还活着）。"""
    try:
        with socket.socket() as s:
            s.settimeout(timeout)
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except OSError:
        return False


# --------------------------------------------------------------------------
# 备份落盘
# --------------------------------------------------------------------------


def _save_backup(values: dict[str, Any]) -> None:
    try:
        BACKUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        # (值, 类型) 元组要转成列表才能进 JSON
        payload = {name: [pair[0], pair[1]] for name, pair in values.items()}
        BACKUP_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, IndexError):
        pass


def _load_backup() -> dict[str, Any]:
    try:
        raw = json.loads(BACKUP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, Any] = {}
    if isinstance(raw, dict):
        for name, pair in raw.items():
            if isinstance(pair, list) and len(pair) == 2:
                out[name] = (pair[0], pair[1])
    return out


def _drop_backup() -> None:
    try:
        BACKUP_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _clean_snapshot(port: int) -> dict[str, Any]:
    """读当前设置做备份 —— 但**剔掉我们自己的残留**。

    如果注册表里当前就是我们上次崩溃留下的代理，它不算"用户的原始设置"，
    否则脏状态会被反复备份、反复写回（脏备份黑洞就是这样来的）。
    """
    values = _read_values()
    if is_our_proxy(values, port):
        return {"ProxyEnable": (0, winreg.REG_DWORD)}
    return values


# --------------------------------------------------------------------------
# 对外
# --------------------------------------------------------------------------


def set_proxy(host: str, port: int, bypass: str = DEFAULT_BYPASS) -> None:
    """启航：备份用户设置，再把系统代理指向本机内核。"""
    global _backup, _our_port, _active
    _our_port = int(port)

    if not _active:
        # 先看有没有上次崩溃留下的落盘备份。如果有、而且注册表里现在这条
        # 就是我们的残留 —— 那落盘备份才是用户真正的原始设置，优先用它，
        # 别让"把自己当成用户设置"的脏备份黑洞重现。
        persisted = _load_backup()
        if persisted and is_our_proxy(_read_values(), int(port)):
            _backup = persisted
        else:
            _backup = _clean_snapshot(int(port))
        _save_backup(_backup)
        _active = True

    server = f"http={host}:{port};https={host}:{port};socks={host}:{port}"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, bypass)
        # 关掉 PAC，避免和手动代理冲突
        try:
            winreg.DeleteValue(k, "AutoConfigURL")
        except FileNotFoundError:
            pass
    _refresh_wininet()


def clear_proxy() -> None:
    """靠岸：还原到启航前的状态。

    两种情形：
      · 有备份 -> 原样还原（连用户原本的 ProxyOverride / PAC 一起还回去）。
      · 没备份但注册表里确实是**我们**留下的残留 -> 只关开关。

    两者都不满足就**什么都不做** —— 绝不去动用户自己的代理（比如他同时在跑
    Clash 挂着 127.0.0.1:7890），也绝不臆造 ProxyServer。
    """
    global _backup, _our_port, _active
    backup = _backup or _load_backup()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE
        ) as k:
            if backup:
                for name, (value, vtype) in backup.items():
                    try:
                        winreg.SetValueEx(k, name, 0, vtype, value)
                    except OSError:
                        pass
                if "AutoConfigURL" not in backup:
                    try:
                        winreg.DeleteValue(k, "AutoConfigURL")
                    except FileNotFoundError:
                        pass
            elif is_our_proxy(_read_values(), _our_port):
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            # else: 不是我们的，什么都不做
    except OSError:
        pass

    _backup = {}
    _our_port = None
    _active = False
    _drop_backup()
    _refresh_wininet()


def heal_on_start(port: int) -> bool:
    """启动时自愈：把上次崩溃留下的"死代理"清掉。

    如果注册表里 `ProxyEnable=1` 且指向我们自己的端口，但那个端口上没人监听，
    说明用户从"打开程序"到"点启航"这段时间其实是断网的。这里立刻修好。

    端口上有人在监听（比如用户另外还开着一个实例）就**不动** —— 那是正常的。

    返回是否真的做了修复（自检输出会打印它）。
    """
    if not is_our_proxy(port=int(port)):
        return False
    if _port_alive(int(port)):
        return False  # 内核还活着，这条代理是有效的，不要碰

    global _our_port, _backup
    _our_port = int(port)
    _backup = _load_backup()   # 有落盘备份就还回用户原本的设置
    clear_proxy()
    return True


def current_proxy() -> dict[str, Any]:
    return _read_values()


# 说明：模块顶层不碰注册表。所有写操作都发生在 set_proxy / clear_proxy /
# heal_on_start 里，导入本模块是零副作用的。
