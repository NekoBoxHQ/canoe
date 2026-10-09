"""轻舟 · 订阅链接解析测试。

订阅栏里贴什么，客户端就得认什么。这里覆盖：

    1. ss://（含 SS2022 多用户 server_key:user_key）
    2. vmess://（base64 JSON）
    3. vless:// / trojan://
    4. 整体 base64 的订阅
    5. 一行认不出来时只跳过那一行，不拖垮整份订阅
    6. 选节点 = 第一个能解析的

不需要联网，也不需要内核。

用法：
    python tests/test_links.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_client import links  # noqa: E402

#: 用户给的那条（真格式：SS2022 多用户，密码里带 %3A 转义的冒号）
SS_2022 = (
    "ss://2022-blake3-aes-128-gcm:hlKPbKuiXS9LaEmUOq5HYA%3D%3D"
    "%3AJ8DQT4ZqDl%2Br3DQtWhI%2BZg%3D%3D@one.leycc.com:33222"
    "#%F0%9F%8C%8D%E6%97%A5%E6%9C%AC%E5%AE%B6%E5%AE%BD%F0%9F%8C%8D"
)

VMESS = "vmess://" + base64.b64encode(json.dumps({
    "v": "2", "ps": "香港-01", "add": "hk.example.com", "port": "443",
    "id": "11111111-2222-3333-4444-555555555555", "aid": "0",
    "net": "ws", "path": "/ws", "host": "hk.example.com",
    "tls": "tls", "sni": "hk.example.com", "scy": "auto",
}).encode()).decode()

VLESS = ("vless://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee@jp.example.com:443"
         "?encryption=none&security=tls&sni=jp.example.com&type=ws&path=%2Fv#东京")

TROJAN = ("trojan://secret-pass@us.example.com:8443"
          "?security=tls&sni=us.example.com&type=grpc&serviceName=gs#美西")

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def main() -> int:
    print("\n== 轻舟 · 订阅链接解析 ==\n")

    # --- 1. ss:// ---
    print("[1] ss://")
    r = links.parse(SS_2022)
    check("能解析", len(r.links) == 1, str(r.skipped))
    ss = r.links[0].outbound
    check("类型是 shadowsocks", ss["type"] == "shadowsocks", ss.get("type"))
    check("method 对", ss["method"] == "2022-blake3-aes-128-gcm", ss.get("method"))
    check("★ 密码是 server_key:user_key（SS2022 多用户）",
          ss["password"] == "hlKPbKuiXS9LaEmUOq5HYA==:J8DQT4ZqDl+r3DQtWhI+Zg==",
          ss.get("password", "")[:60])
    check("主机对", ss["server"] == "one.leycc.com", ss.get("server"))
    check("端口对", ss["server_port"] == 33222, str(ss.get("server_port")))
    check("★ 名称解码了 emoji", r.links[0].name == "🌍日本家宽🌍", repr(r.links[0].name))

    # 老格式：整段 userinfo 是 base64
    legacy = "ss://" + base64.b64encode(b"aes-256-gcm:pw123").decode() + "@h.example.com:8388#旧格式"
    old = links.parse(legacy)
    check("老格式（userinfo 是 base64）也能解析", len(old.links) == 1, str(old.skipped))
    if old.links:
        check("老格式 method/password 对",
              old.links[0].outbound["method"] == "aes-256-gcm"
              and old.links[0].outbound["password"] == "pw123",
              str(old.links[0].outbound))

    # 纯 base64 字符集但带冒号 -> 必须按明文处理（不能"先试 base64"）
    tricky = "ss://YWJjZGVmZ2hpamtsbW5vcA==:cGFzc3dvcmQ=@t.example.com:443#tricky"
    t = links.parse(tricky)
    check("★ 带冒号时按明文解析（不误判成 base64）", len(t.links) == 1, str(t.skipped))
    if t.links:
        check("method 没被解成乱码",
              t.links[0].outbound["method"] == "YWJjZGVmZ2hpamtsbW5vcA==",
              t.links[0].outbound["method"])

    # --- 2. vmess:// ---
    print("\n[2] vmess://")
    v = links.parse(VMESS)
    check("能解析", len(v.links) == 1, str(v.skipped))
    if v.links:
        ob = v.links[0].outbound
        check("类型是 vmess", ob["type"] == "vmess")
        check("uuid 对", ob["uuid"] == "11111111-2222-3333-4444-555555555555")
        check("alter_id 是数字", ob["alter_id"] == 0, str(ob.get("alter_id")))
        check("WS 传输带上", ob.get("transport", {}).get("type") == "ws")
        check("WS path 对", ob["transport"]["path"] == "/ws")
        check("Host 头对", ob["transport"]["headers"]["Host"] == "hk.example.com")
        check("开了 TLS", ob.get("tls", {}).get("enabled") is True)

    # --- 3. vless:// / trojan:// ---
    print("\n[3] vless:// 与 trojan://")
    l = links.parse(VLESS)
    check("vless 能解析", len(l.links) == 1, str(l.skipped))
    if l.links:
        ob = l.links[0].outbound
        check("类型 vless", ob["type"] == "vless")
        check("uuid 对", ob["uuid"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        check("WS 传输", ob.get("transport", {}).get("type") == "ws")
        check("名称解码", l.links[0].name == "东京", l.links[0].name)

    t2 = links.parse(TROJAN)
    check("trojan 能解析", len(t2.links) == 1, str(t2.skipped))
    if t2.links:
        ob = t2.links[0].outbound
        check("类型 trojan", ob["type"] == "trojan")
        check("密码对", ob["password"] == "secret-pass", ob.get("password"))
        check("gRPC 传输", ob.get("transport", {}).get("type") == "grpc")
        check("gRPC service_name 对", ob["transport"]["service_name"] == "gs",
              str(ob.get("transport")))

    # --- 4. 整份 base64 ---
    print("\n[4] 整体 base64 的订阅")
    blob = "\n".join([SS_2022, VMESS, VLESS])
    encoded = base64.b64encode(blob.encode()).decode()
    b = links.parse(encoded)
    check("★ 整份 base64 能解开并逐行解析", len(b.links) == 3, str(len(b.links)))
    check("解出来的和明文版一致",
          [x.name for x in b.links] == [x.name for x in links.parse(blob).links])

    # --- 5. 坏行不拖垮整份 ---
    print("\n[5] 混了垃圾行")
    mixed = "\n".join([SS_2022, "这是一行说明文字", "ss://坏的", "", VMESS])
    m = links.parse(mixed)
    check("★ 好的照收（2 个）", len(m.links) == 2, str(len(m.links)))
    check("★ 坏的只记录，不抛异常", len(m.skipped) == 2, str(m.skipped))

    # --- 6. 选节点 ---
    print("\n[6] 自动选一个")
    check("★ 选中的是第一个", links.pick(mixed).name == "🌍日本家宽🌍")
    check("空订阅返回 None", links.pick("") is None)
    check("纯垃圾返回 None", links.pick("没有链接\n也没有") is None)
    check("全是坏行时 ok=False", not links.parse("ss://坏的").ok)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
