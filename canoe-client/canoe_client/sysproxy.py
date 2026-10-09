"""Windows 系统代理设置（Internet Settings）。

启航时备份用户原有设置，靠岸时**原样还原**，不要留下烂摊子。
只影响「使用系统代理」的程序（浏览器、大部分 Electron 应用等）。
"""
from __future__ import annotations

import ctypes
import winreg
from typing import Any

INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

# 常见内网地址不要走代理
DEFAULT_BYPASS = "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;192.168.*;<local>"

_INTERNET_OPTION_REFRESH = 37
_INTERNET_OPTION_SETTINGS_CHANGED = 39

# 备份用户原始设置，进程内保存
_backup: dict[str, Any] = {}


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


def set_proxy(host: str, port: int, bypass: str = DEFAULT_BYPASS) -> None:
    global _backup
    if not _backup:
        _backup = _read_values()

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
    """还原到启航前的状态。"""
    global _backup
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE
        ) as k:
            if _backup:
                for name, (value, vtype) in _backup.items():
                    try:
                        winreg.SetValueEx(k, name, 0, vtype, value)
                    except OSError:
                        pass
                if "AutoConfigURL" not in _backup:
                    try:
                        winreg.DeleteValue(k, "AutoConfigURL")
                    except FileNotFoundError:
                        pass
            else:
                # 没有备份（进程重启后调用）时，保守地只关开关
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
    except OSError:
        pass

    _backup = {}
    _refresh_wininet()


def current_proxy() -> dict[str, Any]:
    return _read_values()
