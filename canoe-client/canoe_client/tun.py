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


#: TUN 的现场快照。一条命令把"现在系统里是什么状况"全捞出来。
#:
#: 为什么要这个：TUN 出问题的时候（网卡起不来、起了没网），隔着屏幕靠
#: 一轮轮问"你那儿网卡列表长什么样"太慢，而且用户描述不准。
#: 打进 --selftest 的报告里，用户跑一条命令把结果发过来就够了。
_DIAG_PS = r"""
$ad  = Get-NetAdapter -Name 'canoe' -ErrorAction SilentlyContinue
$ip  = if ($ad) { (Get-NetIPAddress -InterfaceAlias 'canoe' -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress) } else { '' }
$def = (Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty InterfaceAlias)
$win = @(Get-PnpDevice -Class Net -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'SWD\WINTUN*' })
[PSCustomObject]@{
  adapter_exists = [bool]$ad
  adapter_status = if ($ad) { [string]$ad.Status } else { '' }
  adapter_ip     = $ip
  default_route  = $def
  wintun_devices = $win.Count
  phantom_wintun = @($win | Where-Object { $_.Status -ne 'OK' }).Count
  singbox_procs  = @(Get-CimInstance Win32_Process -Filter "Name='sing-box.exe'" -ErrorAction SilentlyContinue).Count
} | ConvertTo-Json -Compress
"""


def diagnostics() -> dict:
    """TUN 现场：网卡在不在、有没有 IP、有没有幽灵设备、有几个内核在跑。"""
    import json
    import subprocess

    base: dict = {
        "admin": is_admin(),
        "wintun_dll": wintun_present(),
        "interface_name": "canoe",
    }
    if os.name != "nt":
        base["note"] = "非 Windows"
        return base

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _DIAG_PS],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        base["error"] = f"{exc.__class__.__name__}: {exc}"
        return base

    text = (proc.stdout or "").strip()
    try:
        base.update(json.loads(text))
    except ValueError:
        base["error"] = f"看不懂 PowerShell 的输出：{text[:200]}"
    return base
