"""阶段1 · sing-box 配置生成测试。

不需要图形界面，需要 bin/sing-box.exe。

覆盖：
    1. 生成的配置能被 `sing-box check` 接受（两种模式）
    2. TUN 模式同时接管 IPv4 与 IPv6
    3. 绕过局域网：IPv4 与 IPv6 的私有段都在规则里
    4. 绕过大陆：规则集被正确引用
    5. 关掉绕过选项后，对应规则消失
    6. ★ 出站只指向中转入口，真实节点一个字都不许出现

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

from canoe_client.config import config  # noqa: E402
from canoe_client.kernel import LAN_CIDRS, build_config  # noqa: E402
from canoe_client.options import (  # noqa: E402
    PROFILE_GLOBAL,
    PROFILE_SPLIT,
    RunOptions,
)
from canoe_client.entry import build_entry_outbound  # noqa: E402

#: 样例入口。真实运行时它是服务端下发的（ConfigResponse.entry），
#: 这里只是造一个形状一样的，用来测配置生成。
from canoe_core import EntryPayload  # noqa: E402

SAMPLE_ENTRY = EntryPayload(
    transport="ws", host="entry.example.com", port=443,
    uuid="11111111-2222-3333-4444-555555555555",
    path="/e/test", sni="entry.example.com", tls=True, insecure=False,
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

    outbound = build_entry_outbound(SAMPLE_ENTRY)

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

    # --- 6. 出站指向中转入口（不是真实节点）---
    #
    # 这一段是安全底线：客户端内核配置里只能出现**中转层入口**，
    # 真实节点的主机/端口/UUID 一律不许出现在这里。
    print("\n[6] 出站指向中转入口")
    ob = cfg["outbounds"][0] if cfg["outbounds"][0]["tag"] == "proxy" else None
    check("第一个出站就是 proxy", ob is not None, str(cfg["outbounds"][0]))
    if ob is not None:
        check("出站类型是 vless", ob["type"] == "vless", str(ob.get("type")))
        check("出站服务器是中转入口", ob["server"] == SAMPLE_ENTRY.host, str(ob.get("server")))
        check("出站端口是中转入口端口", ob["server_port"] == SAMPLE_ENTRY.port, str(ob.get("server_port")))
        check("用了 WS 传输", ob.get("transport", {}).get("type") == "ws")
        check("WS 的 Host 头是入口域名",
              ob.get("transport", {}).get("headers", {}).get("Host") == SAMPLE_ENTRY.sni)
        check("开了 TLS", ob.get("tls", {}).get("enabled") is True)

        blob = json.dumps(cfg, ensure_ascii=False)
        check("★ 整份配置里没有任何 real_ 字段", "real_" not in blob)
        check("★ 整份配置里不含真实节点标识",
              "198.51.100.7" not in blob and "aaaaaaaa-bbbb" not in blob)

    check("★ 出站只有 proxy 与 direct 两个",
          [o["tag"] for o in cfg["outbounds"]] == ["proxy", "direct"],
          str([o["tag"] for o in cfg["outbounds"]]))
    check("★ 没有可导出的节点列表（客户端不落任何节点）",
          "outbounds_dump" not in cfg and "nodes" not in cfg)

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

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
