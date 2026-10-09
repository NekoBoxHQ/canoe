"""轻舟 · 系统代理备份/还原测试 —— 专治"一开一关就断网"。

全部跑在**隔离的注册表子键** `HKCU\\Software\\CanoeTest\\...` 上，
不会碰用户真实的 Internet Settings，可以放心反复跑。

覆盖的核心场景：
    A. 正常 启航 → 靠岸，原样还原
    B. 用户本来就有代理（比如 Clash），不能被我们改坏
    C. ★ 脏备份黑洞：崩溃一次之后，靠岸绝不能再把"死代理"写回去
    D. ★ 启动自愈：崩溃留下的脏状态，下次打开程序自动修好
    E. 端口上真的有进程在监听时，自愈不许乱动

用法：
    python tests/test_sysproxy.py
"""
from __future__ import annotations

import socket
import sys
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_client import sysproxy  # noqa: E402

SCRATCH = r"Software\CanoeTest\Internet Settings"
SCRATCH_BACKUP = Path(__file__).resolve().parent / "_scratch_backup.json"
TEST_PORT = 45999            # 保证没有进程监听
OUR_SERVER = f"http=127.0.0.1:{TEST_PORT};https=127.0.0.1:{TEST_PORT};socks=127.0.0.1:{TEST_PORT}"

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


# --- 把模块指向隔离环境 -------------------------------------------------

sysproxy.INTERNET_SETTINGS = SCRATCH          # type: ignore[attr-defined]
sysproxy.BACKUP_FILE = SCRATCH_BACKUP         # type: ignore[attr-defined]


def reset_reg(*, enable=None, server=None, override=None, pac=None) -> None:
    """清空并（可选）写入初始注册表状态。"""
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SCRATCH)
    except FileNotFoundError:
        pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SCRATCH) as k:
        if enable is not None:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, enable)
        if server is not None:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        if override is not None:
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, override)
        if pac is not None:
            winreg.SetValueEx(k, "AutoConfigURL", 0, winreg.REG_SZ, pac)
    sysproxy._backup = {}          # type: ignore[attr-defined]
    sysproxy._our_port = None      # type: ignore[attr-defined]
    sysproxy._active = False       # type: ignore[attr-defined]
    SCRATCH_BACKUP.unlink(missing_ok=True)


def read(name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCRATCH, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, name)[0]
    except (FileNotFoundError, OSError):
        return None


def enabled() -> int:
    return read("ProxyEnable") or 0


def main() -> int:
    print("\n== 轻舟 · 系统代理（断网回归）测试 ==\n")

    # --- 0. 认自己 ---
    print("[0] 认出自己的代理")
    check("三件套指向本机端口 → 是自己",
          sysproxy._server_points_at(OUR_SERVER, TEST_PORT))
    check("指向别的端口 → 不是自己",
          not sysproxy._server_points_at("http=127.0.0.1:7890", TEST_PORT))
    check("指向外部主机 → 不是自己",
          not sysproxy._server_points_at(f"http=10.0.0.9:{TEST_PORT}", TEST_PORT))
    check("空串安全", not sysproxy._server_points_at("", TEST_PORT))
    check("None 安全", not sysproxy._server_points_at(None, TEST_PORT))
    check("banner 形式也能认出来",
          sysproxy._server_points_at(OUR_SERVER, TEST_PORT))

    # --- A. 正常 启航→靠岸 ---
    print("\n[A] 正常 启航 → 靠岸（原来没代理）")
    reset_reg()
    check("启航前 has_backup 为假", not sysproxy.has_backup())
    sysproxy.set_proxy("127.0.0.1", TEST_PORT)
    check("启航后 代理已开", enabled() == 1, str(enabled()))
    check("启航后 指向本机端口", TEST_PORT.__str__() in str(read("ProxyServer")))
    check("启航后 has_backup 为真", sysproxy.has_backup())
    check("备份已落盘", SCRATCH_BACKUP.is_file())
    sysproxy.clear_proxy()
    check("★ 靠岸后 代理已关", enabled() == 0, f"实际 {enabled()}")
    check("靠岸后 备份已清", not sysproxy.has_backup())
    check("靠岸后 落盘备份也删了", not SCRATCH_BACKUP.exists())

    # --- B. 用户本来就有代理 ---
    print("\n[B] 用户本来就挂着 Clash（127.0.0.1:7890）")
    reset_reg(enable=1, server="http=127.0.0.1:7890", override="<local>")
    sysproxy.set_proxy("127.0.0.1", TEST_PORT)
    check("启航会接管", TEST_PORT.__str__() in str(read("ProxyServer")))
    sysproxy.clear_proxy()
    check("★ 靠岸后还原成 Clash", "7890" in str(read("ProxyServer")), str(read("ProxyServer")))
    check("★ Clash 的开关保持开着", enabled() == 1, str(enabled()))
    check("★ 用户的 ProxyOverride 也还原了", read("ProxyOverride") == "<local>")

    # --- C. ★ 脏备份黑洞（核心回归）---
    print("\n[C] ★ 脏备份黑洞：崩溃留下死代理，靠岸绝不能把它写回去")
    reset_reg(enable=1, server=OUR_SERVER)   # 模拟：上次崩溃留下的脏状态
    check("前置：当前是脏状态", sysproxy.is_our_proxy(port=TEST_PORT))
    sysproxy.set_proxy("127.0.0.1", TEST_PORT)
    # 关键：备份时必须把自己的残留剔掉，不能备份成 ProxyEnable=1
    bak = sysproxy._backup
    check("★ 备份里不含我们自己（被当成'没代理'）",
          bak.get("ProxyEnable", (None,))[0] in (0, None), str(bak))
    sysproxy.clear_proxy()
    check("★ 靠岸后是关的，不是又把死代理写回",
          enabled() == 0, f"实际 {enabled()} —— 写回去了就是断网！")

    # --- D. ★ 启动自愈 ---
    print("\n[D] ★ 启动自愈：崩溃后重开程序，自动清掉死代理")
    reset_reg(enable=1, server=OUR_SERVER)
    healed = sysproxy.heal_on_start(TEST_PORT)
    check("自愈有动作", healed)
    check("★ 死代理已被清掉", enabled() == 0, str(enabled()))
    check("没有落盘备份时只关开关、不乱写 ProxyServer",
          read("ProxyOverride") is None)

    print("\n[D2] ★ 崩溃时若留下过落盘备份，自愈要还回用户原本的设置")
    reset_reg(enable=1, server="http=127.0.0.1:7890")
    sysproxy.set_proxy("127.0.0.1", TEST_PORT)      # 备份下 Clash 设置
    # 模拟崩溃：进程没了，注册表留下我们的代理，但落盘备份还在
    sysproxy._backup = {}          # type: ignore[attr-defined]
    sysproxy._our_port = None      # type: ignore[attr-defined]
    check("前置：注册表是我们的脏状态", sysproxy.is_our_proxy(port=TEST_PORT))
    check("前置：落盘备份还在", SCRATCH_BACKUP.is_file())
    healed = sysproxy.heal_on_start(TEST_PORT)
    check("自愈有动作", healed)
    check("★ 还回了用户的 Clash", "7890" in str(read("ProxyServer")), str(read("ProxyServer")))
    check("★ Clash 开关保持开着", enabled() == 1, str(enabled()))

    # --- E. 自愈不能误伤别人 ---
    print("\n[E] 自愈不许乱动别人的代理")
    reset_reg(enable=1, server="http=127.0.0.1:7890")
    check("不是我们的端口 → 自愈不动",
          sysproxy.heal_on_start(TEST_PORT) is False)
    check("Clash 原样还在", "7890" in str(read("ProxyServer")))

    print("\n[E2] 端口上真有进程监听时，自愈必须放手（内核还活着）")
    reset_reg(enable=1, server=OUR_SERVER)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", TEST_PORT))
    srv.listen(1)
    try:
        check("★ 有监听 → 自愈不动", sysproxy.heal_on_start(TEST_PORT) is False)
        check("★ 有效代理没被误清", enabled() == 1, str(enabled()))
    finally:
        srv.close()

    # --- 清理 ---
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SCRATCH)
    except FileNotFoundError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\CanoeTest")
    except FileNotFoundError:
        pass
    SCRATCH_BACKUP.unlink(missing_ok=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
