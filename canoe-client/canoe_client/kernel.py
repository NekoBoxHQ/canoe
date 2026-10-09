"""sing-box 内核管理：生成配置 -> 拉起进程 -> 靠岸时关闭。

配置长这样：

    inbounds : 系统代理 -> 本机 127.0.0.1:20818 的 mixed 入站（SOCKS + HTTP）
               TUN      -> tun 网卡，同时接管 IPv4 与 IPv6（两者可并存）
    outbounds: proxy  -> 从订阅里解析出来的那个节点
               direct -> 直连，用于局域网与大陆流量
    route    : 局域网/大陆 -> direct，其余 -> proxy

⚠ 出站来自**加密订阅**：服务端在传输途中发的是密文，客户端用登录时
   拿到的那把会话密钥在内存里解开，才得到这个节点的参数。
   服务端把客户的订阅清空 -> 客户端下次更新就销毁本地副本并断开。

配置只写到临时文件，内核读完后立刻删除；不提供任何导出入口。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .config import BIN_DIR, config
from .logbus import bus
from .options import RunOptions

# 内核启动后等多久删临时配置。sing-box 启动瞬间就把配置读完了，
# 2 秒足够，同时避免"还没读文件就被删"的竞争。
CONFIG_CLEANUP_DELAY = 2.0

# TUN 靠岸后，等 wintun 把虚拟网卡收回去再允许下次启动。
# 实测：不等的话，紧接着再启航会卡在 "open interface take too much time to finish!"。
TUN_TEARDOWN_GRACE = 2.0

#: TUN 虚拟网卡的名字。自己定，见 _inbounds 里的说明。
TUN_INTERFACE_NAME = "canoe"

#: 匹配 ANSI 转义序列（内核输出里的颜色码）
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

RULESET_DIR = BIN_DIR / "ruleset"

#: 找不到本地规则集时的兜底下载地址（macOS/Linux 上首次运行会用到）
REMOTE_RULESETS = {
    "geosite-cn": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs",
    "geoip-cn": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs",
}

#: 局域网 / 保留地址。IPv4 与 IPv6 都要列，否则某些网络下 v6 流量会绕过代理。
LAN_CIDRS = [
    # IPv4
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",      # CGNAT
    "127.0.0.0/8",
    "169.254.0.0/16",     # link-local
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",        # multicast
    "255.255.255.255/32",
    # IPv6
    "::1/128",
    "fc00::/7",           # ULA
    "fe80::/10",          # link-local
    "ff00::/8",           # multicast
]


class KernelError(Exception):
    pass


# --------------------------------------------------------------------------
# 配置生成
# --------------------------------------------------------------------------


#: 收拾 TUN 残局用的 PowerShell。按顺序做三件事：杀掉上次留下的**孤儿**
#: sing-box、删掉残留的名为 canoe 的网卡、删掉 Wintun 幽灵设备。
#:
#: ⚠⚠ 用 pnputil，**不要用 Remove-NetAdapter**。踩过：那台机器上根本没有
#:     `Remove-NetAdapter` 这个 cmdlet（`Get-NetAdapter` 有，`Remove-` 没
#:    有），而我给它挂了 `-ErrorAction SilentlyContinue`，于是每次"清理"
#:    都一声不吭地什么也没干 —— 用户那边表现就是"TUN 怎么都开不起来"。
#:    pnputil 是系统自带的，按设备实例 ID 删，稳。
#:
#: ⚠ 三道保险：
#:
#:   1. **只杀孤儿**。进程必须是从 %TEMP%\_MEI*（打包解压出来的那份）或者
#:      仓库 bin/ 目录起的，**而且父进程已经没了**。少了后半句就会误杀
#:      另一个还在跑的轻舟实例的内核 —— 那是把人家正在用的代理掐了。
#:   2. **还有内核活着就一个设备都不动**。不然会把正在跑的实例的网卡
#:      从底下抽走。
#:   3. 网卡和设备都只认自己的：网卡名字必须是 canoe，设备实例 ID 必须
#:      以 SWD\WINTUN 开头。绝不能按"描述里带 Wintun"去挑 —— 那会连带
#:      删掉用户装的 WireGuard。
#:
#: 真正的元凶是 **Status 不是 OK 的 Wintun 幽灵设备**：进程没了，设备实例
#: 还挂在系统里，sing-box 再去建同名网卡就撞：
#:     Failed to create TUN: Cannot create a file when that file already exists
_HEAL_PS = r"""
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='sing-box.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $_.ExecutablePath -and (
      $_.ExecutablePath -like "$env:TEMP\_MEI*\bin\sing-box.exe" -or
      $_.ExecutablePath -like "*\canoe-client\bin\sing-box.exe"
    )
  } |
  ForEach-Object {
    $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $_.ParentProcessId) -ErrorAction SilentlyContinue
    if (-not $parent) {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed++
    }
  }
if ($killed -gt 0) { Start-Sleep -Milliseconds 700 }

$removed = 0

# 1) Phantom Wintun devices - Status is anything but OK, i.e. the process is
#    long gone but the device instance is still registered. These CANNOT be
#    in use, so they are always safe to remove, even while another instance
#    is running. That asymmetry matters: this is the thing that actually
#    blocks TUN, so it must not be skipped.
Get-PnpDevice -Class Net -ErrorAction SilentlyContinue |
  Where-Object { $_.InstanceId -like 'SWD\WINTUN*' -and $_.Status -ne 'OK' } |
  ForEach-Object {
    pnputil /remove-device "$($_.InstanceId)" | Out-Null
    $removed++
  }

# 2) The adapter still named canoe. Could be ours from last time - but if any
#    kernel is alive it might be in use, so only touch it when none is.
$alive = @(Get-CimInstance Win32_Process -Filter "Name='sing-box.exe'" -ErrorAction SilentlyContinue).Count
if ($alive -eq 0) {
  $ad = Get-NetAdapter -Name 'canoe' -ErrorAction SilentlyContinue
  if ($ad -and $ad.PNPDeviceID) {
    pnputil /remove-device "$($ad.PNPDeviceID)" | Out-Null
    $removed++
  }
}

if ($removed -gt 0) { Start-Sleep -Milliseconds 700 }
$killed
"""


def heal_leftovers() -> int:
    """清掉上一次没收干净留下的 TUN 残局，返回杀掉的孤儿进程数。

    为什么需要它：崩溃、被任务管理器强杀、更新时来不及收尾 —— 这些情况下
    sing-box 会变成**孤儿进程**活下来，wintun 网卡也跟着留在系统里。之后
    每次开 TUN 都会撞上：

        configure tun interface: set ipv4 address: The object already exists

    界面上只表现为"TUN 不生效"，那行 FATAL 没人看得懂，也没有任何提示告诉
    用户该怎么办。（系统代理早就有 heal_on_start 了，TUN 这边一直漏着。）

    只在两条路上调：启动时（且配了 TUN）、以及 TUN 启动失败之后重试前。
    平时不调 —— 起一次 PowerShell 要小一秒，不值当。
    """
    if os.name != "nt":
        return 0
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _HEAL_PS],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    lines = (proc.stdout or "").strip().splitlines()
    try:
        return int(lines[-1])
    except (ValueError, IndexError):
        return 0


def _tun_unavailable(detail: str) -> bool:
    """内核这几行报错，是不是"网卡还占着"这一类？"""
    low = detail.lower()
    return ("already exists" in low) or ("take too much time" in low)


def _live_kernel_count() -> int:
    """现在还有几个 sing-box 在跑。

    清理过残局之后 TUN 还是起不来，就该看看是不是**别人正占着** ——
    最常见的场景是用户开了两个轻舟窗口。知道个数才好把话说清楚。
    """
    if os.name != "nt":
        return 0
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='sing-box.exe'\" "
             "-ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return int((out or "0").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 0


def _port_taken(port: int) -> bool:
    """127.0.0.1:port 上是不是已经有人了。

    拿一个**真的 bind** 去问 —— 我们关心的本来就是"这个端口还绑不绑得上"，
    比去查进程表又便宜又准（一次 socket 调用，几微秒）。
    """
    import socket

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", int(port)))
        return False
    except OSError:
        return True
    finally:
        probe.close()


def _ruleset_defs(opts: RunOptions) -> list[dict[str, Any]]:
    """大陆分流用的规则集。

    优先用 bin/ruleset/ 下的本地 .srs（随客户端分发，启动时不需要联网）；
    没有就回退成远程下载 —— raw.githubusercontent.com 在国内经常连不上，
    所以正式发布请务必把规则集一起打包。
    """
    defs: list[dict[str, Any]] = []
    for tag in ("geosite-cn", "geoip-cn"):
        local = RULESET_DIR / f"{tag}.srs"
        if local.is_file():
            defs.append(
                {"type": "local", "tag": tag, "format": "binary", "path": str(local)}
            )
        else:
            defs.append(
                {
                    "type": "remote",
                    "tag": tag,
                    "format": "binary",
                    "url": REMOTE_RULESETS[tag],
                    "download_detour": "direct",
                    "update_interval": "7d",
                }
            )
    return defs


def _dns_config(opts: RunOptions, proxy_is_direct: bool = False) -> dict[str, Any]:
    """分流 DNS。

    大陆域名用国内 DNS 解析（走直连），其余走代理解析，
    避免"用国外 DNS 解析国内站"导致的 CDN 就近失效和解析污染。

    ⚠ 必须用 sing-box 1.12+ 的**新** DNS 格式（type + server）。
       旧格式（address 字段）在 1.12 废弃、1.14 已彻底移除，
       写了会直接 decode 失败。这是 `sing-box check` 抓出来的。

    proxy_is_direct：proxy 出站是不是 direct。
        是的话**不能**给 remote 写 detour —— sing-box 认为
        "让 DNS 绕一个 direct 出站"毫无意义，会直接拒绝启动：
            FATAL start dns/https[remote]: detour to an empty
            direct outbound makes no sense
        生产里 proxy 恒为 vless，走不到这条分支；但本机联调、
        以及任何把出站换成 direct 的场景都会踩到，所以这里判一下。
    """
    remote: dict[str, Any] = {
        "type": "https",
        "tag": "remote",
        "server": "1.1.1.1",
    }
    if not proxy_is_direct:
        remote["detour"] = "proxy"

    dns: dict[str, Any] = {
        "servers": [
            {
                # 不写 detour —— 不指定就是直连。
                # 显式写 "detour": "direct" 同样会让 sing-box 报
                # "detour to an empty direct outbound makes no sense"。
                "type": "udp",
                "tag": "local",
                "server": "223.5.5.5",
            },
            remote,
        ],
        "final": "remote",
    }
    if opts.bypass_china:
        dns["rules"] = [{"rule_set": "geosite-cn", "server": "local"}]
    return dns


def _route_config(opts: RunOptions) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []

    # 1) 先嗅探协议/域名，后面按域名分流的规则才有东西可匹配
    rules.append({"action": "sniff"})
    # 2) DNS 查询本身不要走代理
    rules.append({"protocol": "dns", "action": "hijack-dns"})

    # 3) 局域网直连
    if opts.bypass_lan:
        rules.append({"ip_cidr": LAN_CIDRS, "outbound": "direct"})

    # 4) 大陆直连
    rule_sets: list[dict[str, Any]] = []
    if opts.bypass_china:
        rule_sets = _ruleset_defs(opts)
        rules.append({"rule_set": ["geosite-cn"], "outbound": "direct"})
        rules.append({"rule_set": ["geoip-cn"], "outbound": "direct"})

    route: dict[str, Any] = {
        "rules": rules,
        "final": "proxy",
        # 出站服务器是域名（中转入口），必须指定用哪个 DNS 解析它。
        # 这里用 local（直连的国内 DNS）—— 不能用代理解析代理自己的地址，
        # 那是死循环。sing-box 1.12+ 强制要求这个字段。
        "default_domain_resolver": {"server": "local"},
    }
    if rule_sets:
        route["rule_set"] = rule_sets
    if opts.use_tun:
        # Windows 上必须开，否则 TUN 路由可能被物理网卡覆盖
        route["auto_detect_interface"] = True
    return route


def _inbounds(opts: RunOptions) -> list[dict[str, Any]]:
    """按勾选拼装入站。

    系统代理和 TUN 可以**同时**开，所以这里是一个"有就加"的列表，
    不是二选一。两个都关的话兜底给一个 mixed 入站 —— 否则内核起来了却
    没有任何入口，等于白开。
    """
    inbounds: list[dict[str, Any]] = []

    if opts.use_system_proxy:
        inbounds.append(
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": int(opts.mixed_port),
            }
        )

    if opts.use_tun:
        address = [opts.tun_address_v4]
        if opts.tun_ipv6:
            address.append(opts.tun_address_v6)
        inbounds.append(
            {
                "type": "tun",
                "tag": "tun-in",
                # ★ 网卡名字必须自己定。不写的话 sing-box 用它的默认名，
                #   我们就没法在收拾残局时把**自己那张**网卡挑出来 ——
                #   只能按"描述里带 Wintun"去猜，那会连带删掉别人家的
                #   WireGuard。名字写死，清理才敢下手。
                "interface_name": TUN_INTERFACE_NAME,
                "address": address,
                "mtu": 9000,
                "auto_route": True,
                "strict_route": True,
                "stack": "system",
            }
        )

    if not inbounds:
        inbounds.append(
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": int(opts.mixed_port),
            }
        )

    return inbounds


def build_config(proxy_outbound: dict[str, Any], opts: RunOptions) -> dict[str, Any]:
    """生成完整的 sing-box 配置。"""
    return {
        # timestamp 关掉：日志总线自己会加 [HH:MM:SS]，
        # 开着的话每行会顶两个时间，面板里很挤。
        "log": {"level": opts.log_level, "timestamp": False},
        "dns": _dns_config(opts, proxy_is_direct=proxy_outbound.get("type") == "direct"),
        "inbounds": _inbounds(opts),
        "outbounds": [
            {**proxy_outbound, "tag": "proxy"},
            {"type": "direct", "tag": "direct"},
        ],
        "route": _route_config(opts),
    }


# --------------------------------------------------------------------------
# 进程管理
# --------------------------------------------------------------------------


class SingBoxKernel:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._config_path: Path | None = None
        self._cleaner: threading.Timer | None = None
        self._had_tun = False
        #: 这个进程里**成功跑起来过**内核没有。用来区分"首次启航"和
        #: "切换/重启"—— 后者要等一下系统的残局，前者不用白等。
        self._ever_ran = False
        self._reader: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- 启动 -----------------------------------------------------------
    def start(
        self,
        proxy_outbound: dict[str, Any],
        opts: RunOptions,
        _healed: bool = False,
    ) -> None:
        if self.running:
            raise KernelError("内核已在运行")

        exe = config.find_singbox()
        if exe is None:
            raise KernelError(
                "找不到 sing-box。请把 sing-box.exe 放到 canoe-client/bin/ 下"
            )

        cfg = build_config(proxy_outbound, opts)
        self._had_tun = any(i.get("type") == "tun" for i in cfg.get("inbounds", []))
        text = json.dumps(cfg, indent=2, ensure_ascii=False)

        # 起内核之前先清一遍残局。
        #
        # 不清的话会出现一种很阴的状态：同名网卡还占着，新内核**把网卡建出来
        # 却配不上** —— 控制面板里看得见这张卡、状态是空的、一条路由都没有。
        # 表面看"TUN 开起来了"，实际什么都没接管，用户那边就是"网页打不开"。
        # 用户报的"先系统代理、再切 TUN 就不行，整个退出重来才行"就是它：
        # 重启之后残局才被清掉。
        #
        # ⚠ **不能只在 TUN 时才清**。用户报过另一种：纯系统代理模式点启航，
        #   直接失败 —— 上次被强杀留下的**孤儿内核**还占着 20818，新内核当然
        #   绑不上：
        #       FATAL start inbound/mixed[mixed-in]: listen tcp 127.0.0.1:20818:
        #       bind: Only one usage of each socket address ... is normally permitted
        #   更坑的是"控制面板里 canoe 网卡还在" —— 因为那个孤儿内核还活着，
        #   网卡是它的。以前这句写成 `if self._had_tun`，于是系统代理模式下
        #   这个孤儿永远没人管，启航一直失败，而界面上只表现为"点了没反应"。
        #
        # 清理要起一次 PowerShell（WMI + PnP 查询，一两秒），不能每次启航都白花。
        # 所以非 TUN 的情况先用一次 bind **几微秒**问一句"端口还占着吗" ——
        # 没占着就说明系统是干净的，直接跳过。
        if not _healed and (self._had_tun or _port_taken(opts.mixed_port)):
            cleared = heal_leftovers()
            if cleared:
                bus.system(f"启航前清掉了 {cleared} 个上次没退干净的内核进程")
        del cfg

        # 起 TUN 之前再等一拍。
        #
        # 用户的原话是"靠岸、再启航就正常了" —— 差别只有中间那几秒。上一版
        # 只在**停 TUN 之后**等（stop 里的 _had_tun），可切换这条路是
        # "停一个没开 TUN 的内核 → 马上起 TUN"，那会儿根本没等过。
        # 这里补上：这个进程里只要跑过内核，起 TUN 前就先让系统喘口气。
        # 首次启航不受影响（没跑过内核），不白等。
        if self._had_tun and self._ever_ran and not _healed:
            time.sleep(TUN_TEARDOWN_GRACE)

        # 内核只能从文件读配置，所以写临时文件；读完立刻删
        fd, tmp_name = tempfile.mkstemp(prefix="canoe-", suffix=".json")
        self._config_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        del text

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self._proc = subprocess.Popen(
                [str(exe), "run", "-c", str(self._config_path)],
                cwd=str(exe.parent),      # 让内核能找到同目录的 wintun.dll 与规则集
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._remove_config()
            raise KernelError(f"启动 sing-box 失败：{exc}") from exc

        time.sleep(1.2)
        if self._proc.poll() is not None:
            code = self._proc.returncode
            # ★ 把内核的报错捞出来再抛。
            #   读取线程是在这之后才起的，不主动读一次的话，sing-box
            #   说的"配置哪里不对"会被整个丢掉 —— 只剩一个退出码，没法排查。
            output = ""
            try:
                output = (self._proc.stdout.read() or "").strip()
            except (OSError, ValueError):
                pass
            self._proc = None
            self._remove_config()
            detail = _ANSI_RE.sub("", output)[-800:]

            # TUN 网卡还占着 —— 上次崩溃 / 被强杀 / 更新来不及收尾留下的。
            # 清掉残局再来一次。用户报的"TUN 不生效"十有八九是这条：
            # 界面上只看到一句 FATAL，重启程序也不管用，因为残局是留在
            # 系统里的，不在我们这个进程里。
            #
            # 只重试**一次**。清完还起不来就是别的问题（没管理员权限、
            # 缺 wintun.dll），再试只是让用户多等两秒。
            if self._had_tun and not _healed and _tun_unavailable(detail):
                bus.system("TUN 网卡被上次的残留占着，清理后重试…")
                killed = heal_leftovers()
                if killed:
                    bus.system(f"已清掉 {killed} 个上次没退干净的内核进程")
                return self.start(proxy_outbound, opts, _healed=True)

            # 清过一遍还是撞 —— 那就不是"残渣"，是**别人正占着**。
            # 把那句 "Cannot create a file when that file already exists"
            # 原样抛给用户等于没说，这里翻成人话并给出下一步。
            if self._had_tun and _tun_unavailable(detail):
                other = _live_kernel_count()
                raise KernelError(
                    "TUN 网卡被占着，起不来。\n"
                    + (
                        f"现在还有 {other} 个轻舟内核在跑 —— 多半是你开了两个轻舟窗口，"
                        "把另一个关掉再试。\n"
                        if other
                        else "已经清理过残留还是不行，重启一次电脑就能好。\n"
                    )
                    + f"\n内核原话：{detail}"
                )

            raise KernelError(
                f"sing-box 启动后立即退出（退出码 {code}）。"
                + (f"\n内核输出：\n{detail}" if detail else "通常是配置有问题或内核版本不匹配。")
            )

        self._cleaner = threading.Timer(CONFIG_CLEANUP_DELAY, self._remove_config)
        self._cleaner.daemon = True
        self._cleaner.start()

        # 起来了才算数 —— 起失败的那些不该让下次启航白等
        self._ever_ran = True

        # 把内核输出接进日志总线，界面上能看到它在干什么、为什么失败
        self._reader = threading.Thread(target=self._pump_output, args=(self._proc,), daemon=True)
        self._reader.start()

    # -- 停止 -----------------------------------------------------------
    def stop(self, timeout: float = 5.0) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

            # TUN 模式下，wintun 虚拟网卡的销毁是异步的。
            # 如果用户刚靠岸就立刻再启航，新网卡会和还没销毁完的旧网卡抢，
            # sing-box 会卡在 "open interface take too much time to finish!"，
            # 整个启动挂住（连本地的 mixed 入站都起不来）。
            # 等一小会儿让上层把网卡收回去，代价远小于卡死。
            # 这是实测出来的：连续快速起停时会复现，间隔几秒就不会。
            if self._had_tun:
                time.sleep(TUN_TEARDOWN_GRACE)

        reader, self._reader = self._reader, None
        if reader is not None and reader.is_alive():
            reader.join(timeout=2)

        self._remove_config()

    def _pump_output(self, proc: subprocess.Popen) -> None:
        """在工作线程里逐行读内核输出，塞进日志总线。

        读管道必须在单独线程里做（否则会把主线程读死），
        界面那边用定时器从总线取新增行，两边不用互相等。
        """
        try:
            for line in iter(proc.stdout.readline, ""):
                if line:
                    # sing-box 即使输出到管道也会带 ANSI 颜色码，
                    # 直接进日志面板会显示成 [36mINFO[0m 这种鬼东西，去掉。
                    bus.kernel(_ANSI_RE.sub("", line).rstrip())
        except (ValueError, OSError):
            pass  # 进程退出时管道关闭，属正常
        finally:
            try:
                proc.stdout.close()
            except (OSError, AttributeError):
                pass

    def _remove_config(self) -> None:
        if self._cleaner is not None:
            self._cleaner.cancel()
            self._cleaner = None
        path, self._config_path = self._config_path, None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


kernel = SingBoxKernel()
