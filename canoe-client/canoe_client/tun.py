"""全局（TUN）模式的先决条件检查。"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path


def is_admin() -> bool:
    """当前进程是否有管理员权限。TUN 模式必须要有。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重启自己（UAC 提权）。成功则当前进程应退出。"""
    import sys

    try:
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params or None, None, 1
        )
        return int(result) > 32  # >32 表示成功
    except OSError:
        return False


def wintun_present(bin_dir: Path | None = None) -> bool:
    candidates = []
    if bin_dir:
        candidates.append(bin_dir / "wintun.dll")
    candidates.append(Path.cwd() / "wintun.dll")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates.append(Path(system_root) / "System32" / "wintun.dll")
    return any(c.is_file() for c in candidates)


def check_tun_ready(bin_dir: Path | None = None) -> tuple[bool, str]:
    """返回 (是否可用, 不可用原因)。"""
    if os.name != "nt":
        return False, "全局模式仅支持 Windows"
    if not is_admin():
        return False, "全局模式需要管理员权限，请以管理员身份重新启航"
    if not wintun_present(bin_dir):
        return False, "缺少 wintun.dll，请把它和 sing-box.exe 放在同一目录"
    return True, ""
