"""阶段1 · sing-box 配置生成测试。

不需要图形界面，需要 bin/sing-box.exe。

覆盖：
    1. 生成的配置能被 `sing-box check` 接受（两种模式）
    2. TUN 模式同时接管 IPv4 与 IPv6
    3. 绕过局域网：IPv4 与 IPv6 的私有段都在规则里
    4. 绕过大陆：规则集被正确引用
    5. 关掉绕过选项后，对应规则消失
    6. ★ 出站只指向订阅里那个节点，明文里一个字都不许出现节点信息

用法：
    python tests/test_config.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_core import Route  # noqa: E402

from canoe_client.config import config  # noqa: E402
from canoe_client.kernel import LAN_CIDRS, build_config  # noqa: E402
from canoe_client.options import (  # noqa: E402
    PROFILE_GLOBAL,
    PROFILE_SPLIT,
    RunOptions,
)
from canoe_client import links  # noqa: E402

#: 样例订阅。真实运行时它是登舟后从服务端**加密**拉到、在内存里解开的
#: （见 canoe_core.crypto）。这里只是一条形状一样的链接，用来测配置生成。
SAMPLE_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@node.example.com:443"
    "?encryption=none&security=tls&sni=node.example.com&type=ws&path=%2Fe%2Ftest#样例节点"
)

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def singbox_check(cfg: dict) -> tuple[bool, str]:
    """用 sing-box 自己的校验器验证配置。"""
    exe = config.find_singbox()
    if exe is None:
        return False, "找不到 sing-box"
    fd, path = tempfile.mkstemp(prefix="canoe-check-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [str(exe), "check", "-c", path], capture_output=True, text=True, timeout=60
        )
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    finally:
        Path(path).unlink(missing_ok=True)


def main() -> int:
    print("\n== 轻舟 · sing-box 配置生成测试 ==\n")

    exe = config.find_singbox()
    if exe is None:
        print("  [跳过] bin/sing-box.exe 不存在，无法做 check 校验")
        print("         把 sing-box.exe 放进 canoe-client/bin/ 后重跑\n")
        return 0
    print(f"内核: {exe}\n")

    outbound = links.pick(SAMPLE_LINK).outbound

    # --- 1. 两种模式都能通过 check ---
    print("[1] 配置能被 sing-box 接受")
    for mode, label in (("system_proxy", "仅系统代理"), ("tun", "仅 TUN")):
        cfg = build_config(outbound, RunOptions(use_system_proxy=(mode=="system_proxy"), use_tun=(mode=="tun")))
        ok, msg = singbox_check(cfg)
        check(f"{label} 模式通过 sing-box check", ok, msg[:400])
        check(f"{label} 配置里只有一个出站是代理", cfg["outbounds"][0]["tag"] == "proxy")
        check(f"{label} 默认路由走代理", cfg["route"]["final"] == "proxy")

    # --- 2. TUN 双栈 ---
    print("\n[2] TUN 同时接管 IPv4 与 IPv6")
    cfg_tun = build_config(outbound, RunOptions(use_tun=True, tun_ipv6=True))
    # 系统代理和 TUN 可以并存，所以不能假设 [0] 就是 tun —— 按类型找
    tun = next(i for i in cfg_tun["inbounds"] if i["type"] == "tun")
    check("TUN 模式入站类型是 tun", tun["type"] == "tun")
    check("TUN 配了 IPv4 地址", any("." in a for a in tun["address"]), str(tun["address"]))
    check("★ TUN 配了 IPv6 地址", any(":" in a for a in tun["address"]), str(tun["address"]))
    check("TUN 开了 auto_route", tun["auto_route"] is True)
    check("TUN 开了 strict_route", tun["strict_route"] is True)
    check("TUN 开了 auto_detect_interface", cfg_tun["route"].get("auto_detect_interface") is True)

    cfg_tun4 = build_config(outbound, RunOptions(use_tun=True, tun_ipv6=False))
    tun4 = next(i for i in cfg_tun4["inbounds"] if i["type"] == "tun")
    check("关掉 IPv6 后只剩 IPv4 地址",
          all(":" not in a for a in tun4["address"]), str(tun4["address"]))

    # --- 2.5 系统代理与 TUN 可并存 ---
    print("\n[2.5] 系统代理与 TUN 可并存")
    cfg_both = build_config(outbound, RunOptions(use_system_proxy=True, use_tun=True))
    kinds = [i["type"] for i in cfg_both["inbounds"]]
    check("★ 两者都勾时，配置里有 mixed 也有 tun",
          "mixed" in kinds and "tun" in kinds, str(kinds))
    ok, msg = singbox_check(cfg_both)
    check("★ 并存配置能通过 sing-box check", ok, msg[:300])

    cfg_none = build_config(outbound, RunOptions(use_system_proxy=False, use_tun=False))
    check("两个都不勾时兜底给一个 mixed 入站",
          [i["type"] for i in cfg_none["inbounds"]] == ["mixed"],
          str([i["type"] for i in cfg_none["inbounds"]]))

    # --- 3. 绕过局域网 ---
    print("\n[3] 绕过局域网（v4 + v6 私有段）")
    cfg = build_config(outbound, RunOptions(use_system_proxy=True, profile=PROFILE_SPLIT))
    lan_rule = next(
        (r for r in cfg["route"]["rules"] if r.get("ip_cidr") == LAN_CIDRS), None
    )
    check("有局域网直连规则", lan_rule is not None and lan_rule["outbound"] == "direct")
    check("★ 局域网规则含 IPv4 私网段", "192.168.0.0/16" in LAN_CIDRS and "10.0.0.0/8" in LAN_CIDRS)
    check("★ 局域网规则含 IPv6 私网段", "fc00::/7" in LAN_CIDRS and "fe80::/10" in LAN_CIDRS)

    cfg_no_lan = build_config(outbound, RunOptions(use_system_proxy=True, profile=PROFILE_GLOBAL))
    check("切到全局模式后该规则消失",
          not any(r.get("ip_cidr") == LAN_CIDRS for r in cfg_no_lan["route"]["rules"]))

    # --- 4. 绕过大陆 ---
    print("\n[4] 绕过大陆（按规则集分流）")
    cfg = build_config(outbound, RunOptions(use_system_proxy=True, profile=PROFILE_SPLIT))
    route = cfg["route"]
    check("配了规则集", len(route.get("rule_set", [])) == 2, str(route.get("rule_set")))
    tags = {rs["tag"] for rs in route.get("rule_set", [])}
    check("含 geosite-cn 与 geoip-cn", tags == {"geosite-cn", "geoip-cn"}, str(tags))
    used = [r for r in route["rules"] if "rule_set" in r]
    check("有引用规则集的分流规则", len(used) == 2, str(used))
    check("分流目标都是 direct", all(r["outbound"] == "direct" for r in used))

    local = [rs for rs in route["rule_set"] if rs["type"] == "local"]
    check("★ 规则集用本地文件（启动不需要联网）", len(local) == 2,
          f"{len(local)}/2 是本地；缺的会退化成远程下载")
    if local:
        check("本地规则集文件确实存在",
              all(Path(rs["path"]).is_file() for rs in local),
              str([rs["path"] for rs in local]))

    check("DNS 也对大陆域名走国内解析",
          any(r.get("server") == "local" for r in cfg["dns"].get("rules", [])))

    cfg_no_cn = build_config(outbound, RunOptions(use_system_proxy=True, profile=PROFILE_GLOBAL))
    check("切到全局模式后没有规则集", "rule_set" not in cfg_no_cn["route"])

    # --- 5. 系统代理模式 ---
    print("\n[5] 系统代理模式")
    cfg = build_config(outbound, RunOptions(use_system_proxy=True, mixed_port=20818))
    inb = cfg["inbounds"][0]
    check("入站是 mixed（SOCKS + HTTP）", inb["type"] == "mixed")
    check("只监听本机 127.0.0.1", inb["listen"] == "127.0.0.1")
    check("端口取自选项", inb["listen_port"] == 20818)

    # --- 6. 出站来自订阅里的那条链接 ---
    print("\n[6] 出站就是订阅里选中的那个节点")
    ob = cfg["outbounds"][0] if cfg["outbounds"][0]["tag"] == "proxy" else None
    check("第一个出站就是 proxy", ob is not None, str(cfg["outbounds"][0]))
    if ob is not None:
        check("出站类型是 vless", ob["type"] == "vless", str(ob.get("type")))
        check("出站服务器来自订阅", ob["server"] == "node.example.com", str(ob.get("server")))
        check("出站端口来自订阅", ob["server_port"] == 443, str(ob.get("server_port")))
        check("UUID 来自订阅", ob["uuid"] == "11111111-2222-3333-4444-555555555555")
        check("用了 WS 传输", ob.get("transport", {}).get("type") == "ws")
        check("WS 路径来自订阅", ob.get("transport", {}).get("path") == "/e/test")
        check("开了 TLS", ob.get("tls", {}).get("enabled") is True)
        check("SNI 来自订阅", ob.get("tls", {}).get("server_name") == "node.example.com")

    check("★ 出站只有 proxy 与 direct 两个",
          [o["tag"] for o in cfg["outbounds"]] == ["proxy", "direct"],
          str([o["tag"] for o in cfg["outbounds"]]))
    check("★ 没有可导出的节点列表", "outbounds_dump" not in cfg and "nodes" not in cfg)

    # 生成的配置只写临时文件、内核读完即删；这里断言的是"没有留下落盘入口"
    check("★ 配置里没有把整份订阅塞进去",
          "ss://" not in json.dumps(cfg) and "vless://" not in json.dumps(cfg))

    # --- 7. DNS 的 detour 要跟出站对得上（防回归）---
    #
    # 真代理出站：remote DNS 必须绕 proxy 走（否则国外域名会被污染）。
    # direct 出站：**必须不写** detour —— 写了 sing-box 会判定
    # "让 DNS 绕一个 direct 出站毫无意义" 直接拒绝启动。
    # 本机联调和测试都会把出站换成 direct，所以两种都要验。
    print("\n[7] DNS detour 与出站类型一致")
    real = build_config(outbound, RunOptions())
    remote = next(s for s in real["dns"]["servers"] if s["tag"] == "remote")
    check("真代理出站时 remote DNS 绕 proxy", remote.get("detour") == "proxy", str(remote))

    direct_cfg = build_config({"type": "direct", "tag": "proxy"}, RunOptions())
    d_remote = next(s for s in direct_cfg["dns"]["servers"] if s["tag"] == "remote")
    check("★ direct 出站时不写 detour（否则内核拒启）",
          "detour" not in d_remote, str(d_remote))
    ok_d, msg_d = singbox_check(direct_cfg)
    check("★ direct 出站的配置能通过 sing-box check", ok_d, msg_d[:300])

    # --- 8. 回国模式（线路方向由服务端定）---
    #
    # 出国是"大陆直连、其余走代理"；回国**整个反过来**：国外直连、
    # 大陆走代理。规则集是同一份（geosite-cn / geoip-cn），只是出站
    # 反过来指；DNS 也跟着翻。
    print("\n[8] 回国模式：国外直连、国内走代理")
    home = RunOptions(use_system_proxy=True, profile=PROFILE_SPLIT,
                      route_mode=Route.IN)
    hcfg = build_config(outbound, home)
    hroute = hcfg["route"]

    check("兜底出站变成直连", hroute["final"] == "direct", str(hroute["final"]))
    hused = [r for r in hroute["rules"] if "rule_set" in r]
    check("还是那两条规则集规则", len(hused) == 2, str(hused))
    check("★ 大陆规则指向代理（跟出国反着来）",
          all(r["outbound"] == "proxy" for r in hused), str(hused))
    check("规则集没变（还是 geosite-cn / geoip-cn）",
          {rs["tag"] for rs in hroute["rule_set"]} == {"geosite-cn", "geoip-cn"})
    hlan = next((r for r in hroute["rules"] if r.get("ip_cidr") == LAN_CIDRS), None)
    check("★ 局域网仍然直连（两个方向都一样）",
          hlan is not None and hlan["outbound"] == "direct", str(hlan))

    # DNS：大陆域名走**代理**去国内查，其余走**直连**的国外 DNS
    hservers = {s["tag"]: s for s in hcfg["dns"]["servers"]}
    check("多出来一个走代理的国内 DNS", "cn" in hservers, str(list(hservers)))
    check("★ 大陆域名交给那个走代理的国内 DNS",
          any(r.get("server") == "cn" for r in hcfg["dns"].get("rules", [])),
          str(hcfg["dns"].get("rules")))
    check("国外 DNS 在回国模式下不绕代理",
          "detour" not in hservers["remote"], str(hservers["remote"]))
    check("★ 解析入口的那个国内 DNS 仍然直连（绕了就死循环）",
          "detour" not in hservers["local"], str(hservers["local"]))
    check("★ default_domain_resolver 指的是直连的那个 DNS",
          hroute["default_domain_resolver"] == {"server": "local"},
          str(hroute.get("default_domain_resolver")))

    # 全局模式不挂规则 —— 两个方向下都一样，方向对"全局"没有影响
    hglobal = build_config(outbound, RunOptions(profile=PROFILE_GLOBAL,
                                                route_mode=Route.IN))
    check("★ 全局模式下方向不起作用（还是没规则集）",
          "rule_set" not in hglobal["route"] and hglobal["route"]["final"] == "proxy")

    ok_h, msg_h = singbox_check(hcfg)
    check("★ 回国模式的配置能通过 sing-box check", ok_h, msg_h[:300])

    # 认不出来的方向值一律当出国 —— 老配置、老服务端都会走到这儿
    check("★ 认不出的 route_mode 落回出国",
          RunOptions.from_dict({"route_mode": "乱写"}).route_mode == Route.OUT
          and RunOptions.from_dict({}).route_mode == Route.OUT)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
