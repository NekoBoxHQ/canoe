"""轻舟客户端 · 与服务端的联调测试。

**要有一个在跑的服务端**（本地起一个即可）：

    cd canoe-server
    .venv/Scripts/python serve.py --no-tls --port 59588 --panel-port 0

    cd canoe-client
    set CANOE_SERVER_URL=http://127.0.0.1:59588
    python tests/test_server.py

没设 CANOE_SERVER_URL 就整体跳过 —— 免得在没服务端的机器上红一片。

覆盖的是"客户端接线"这条链路：
    注册 -> 登录 -> 拿配置 -> 把入口拼成 sing-box 出站 -> 起内核
    -> 服务端能看到在线会话 -> 靠岸 -> 会话结束
    -> 心跳 / 订阅 / 客户端更新
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("CANOE_SERVER_URL", "").rstrip("/")

#: 直接走 requests 的那几处（探活、管理员操作）也要跟着客户端的 TLS 口径走。
#: 联调自签证书时设 CANOE_CA_BUNDLE 指那张 CA；生产留空即真校验。
_CA = (os.environ.get("CANOE_CA_BUNDLE") or "").strip()
VERIFY: str | bool = _CA if _CA and Path(_CA).is_file() else True

passed = failed = skipped = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {extra}")


def skip(label: str, why: str) -> None:
    global skipped
    skipped += 1
    print(f"  [跳过] {label} —— {why}")


def main() -> int:
    print("\n== 轻舟客户端 · 服务端联调 ==\n")

    if not BASE:
        skip("全部", "没设 CANOE_SERVER_URL")
        print(f"\n通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项\n")
        return 0

    # 环境变量必须在 import config 之前设好（config.server_url 会读它）
    os.environ["CANOE_SERVER_URL"] = BASE

    import requests

    from canoe_client.api import CanoeApiError, api
    from canoe_client.config import config
    from canoe_client.entry import build_entry_outbound
    from canoe_client.kernel import kernel
    from canoe_client.options import RunOptions
    from canoe_client import update

    print(f"[0] 环境\n      服务端 {BASE}")
    check("服务端可达", requests.get(f"{BASE}/api/health", timeout=8, verify=VERIFY).status_code == 200)
    check("config.server_url 用的是环境变量覆盖", config.server_url == BASE, config.server_url)

    # 管理员：直接走 HTTP，不经客户端 api（免得共用 token 状态）
    admin = requests.post(f"{BASE}/api/login", json={
        "username": "admin", "password": "canoe-admin-123",
        "device_id": "itest-admin-0001", "device_name": "itest",
    }, timeout=10, verify=VERIFY)
    if admin.status_code != 200:
        skip("全部", f"管理员登录失败（{admin.status_code}）—— 跑过 seed.py 了吗？")
        print(f"\n通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项\n")
        return 0
    ah = {"Authorization": f"Bearer {admin.json()['token']}"}

    # 准备一个启用的节点，否则拿不到配置
    node_body = {
        "name": "联调测试节点", "remark": "", "enabled": True, "sort_order": 1,
        "entry_host": "entry.example.com", "entry_port": 443, "entry_uuid": "",
        "entry_path": "/e/itest", "entry_sni": "entry.example.com",
        "entry_transport": "ws", "entry_tls": True, "entry_insecure": False,
        "real_protocol": "vless", "real_host": "198.51.100.7", "real_port": 8443,
        "real_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "real_flow": "",
        "real_tls": True, "real_sni": "real.invalid", "real_fingerprint": "chrome",
        "real_network": "tcp", "real_ws_path": "", "real_ws_host": "",
        "real_grpc_service": "", "real_insecure": False, "real_extra": {},
    }
    r = requests.post(f"{BASE}/api/admin/nodes", headers=ah, json=node_body, timeout=10, verify=VERIFY)
    node_id = r.json().get("id") if r.status_code == 201 else None
    check("准备了一个启用节点", node_id is not None, r.text[:200])

    username = f"it_{uuid.uuid4().hex[:8]}"
    password = "canoe-pass-123"

    try:
        # --- 1. 注册 ---
        print("\n[1] 注册")
        r = api.register(username, password)
        check("注册成功", r.get("username") == username, str(r)[:160])

        try:
            api.register(username, password)
            check("重复注册应当报错", False)
        except CanoeApiError as exc:
            check("重复注册给出可读提示", "已存在" in exc.message, exc.message)

        # --- 2. 登录 ---
        print("\n[2] 登录")
        try:
            api.login(username, "wrong-password-123")
            check("错密码应当报错", False)
        except CanoeApiError as exc:
            check("错密码被拒", bool(exc.message), exc.message)

        result = api.login(username, password)
        check("登录成功并拿到令牌", bool(api.token))
        check("登录就返回了节点名（主界面启航前要显示）",
              bool(result.user.node_name), str(result.user.node_name))
        check("令牌里有设备号", bool(config.device_id))

        # --- 3. 订阅 ---
        print("\n[3] 订阅更新")
        sub = api.subscription()
        check("拿到订阅", sub.revision != "", str(sub)[:160])
        check("订阅里有入口", sub.entry is not None)
        check("★ 订阅里没有真实节点字段", "real_" not in sub.model_dump_json())
        first_revision = sub.revision

        # --- 4. 更新（客户端更新 + 订阅）---
        print("\n[4] 客户端更新")
        try:
            rel = api.latest_release()
            check("拿到最新版本", bool(rel.version), str(rel)[:120])
        except CanoeApiError as exc:
            check("没发布过版本时给出可读错误", "版本" in exc.message, exc.message)

        from canoe_core import VERSION
        check("版本比较可用", update.compare_versions("1.1.0", VERSION) >= -1)

        # --- 5. 拿配置 ---
        print("\n[5] 拉配置")
        cfg = api.fetch_config("system_proxy")
        check("拿到会话 id", bool(cfg.session_id))
        check("拿到入口", cfg.entry.host == "entry.example.com", str(cfg.entry)[:160])
        check("★ 配置里没有 real_* 字段", "real_" not in cfg.model_dump_json())
        check("★ 配置里不含真实 IP", "198.51.100.7" not in cfg.model_dump_json())
        check("拿到了短期凭证", bool(cfg.token))

        # --- 6. 入口 -> sing-box 出站 ---
        print("\n[6] 入口拼成出站")
        out = build_entry_outbound(cfg.entry)
        check("出站类型是 vless", out["type"] == "vless")
        check("出站指向入口（不是真实节点）",
              out["server"] == "entry.example.com", str(out)[:200])
        check("★ 出站里没有真实节点的任何字段",
              "198.51.100.7" not in str(out) and "real_" not in str(out))
        check("带了 WS 传输", out.get("transport", {}).get("type") == "ws")
        check("WS 的 Host 头是入口域名",
              out.get("transport", {}).get("headers", {}).get("Host") == "entry.example.com")
        check("开了 TLS", out.get("tls", {}).get("enabled") is True)

        # --- 7. 真的把内核拉起来 ---
        print("\n[7] 启航（真起内核）")
        exe = config.find_singbox()
        if exe is None:
            skip("起内核", "bin/sing-box.exe 不在")
        else:
            opts = RunOptions(use_system_proxy=True, use_tun=False,
                              mixed_port=22999, log_level="warn")
            kernel.start(out, opts)
            check("内核在跑", kernel.running)

            # 服务端应当看到一条在线会话
            time.sleep(1)
            sessions = requests.get(f"{BASE}/api/admin/sessions",
                                    params={"online": "true"}, headers=ah, timeout=10, verify=VERIFY).json()
            mine = [s for s in sessions.get("items", []) if s["username"] == username]
            check("★ 服务端看到这个账号在线", len(mine) >= 1, str(sessions)[:200])
            if mine:
                check("会话绑的是我们这台设备",
                      mine[0]["device_id"] == config.device_id, mine[0]["device_id"])

            # --- 8. 心跳 ---
            print("\n[8] 心跳")
            data = api.heartbeat(cfg.session_id)
            check("心跳返回 ok", data.get("ok") is True, str(data)[:160])
            check("心跳没被吊销", not data.get("revoked"))

            # --- 9. 靠岸 ---
            print("\n[9] 靠岸")
            api.stop_session(cfg.session_id)
            kernel.stop()
            check("内核已停", not kernel.running)

            time.sleep(1)
            sessions = requests.get(f"{BASE}/api/admin/sessions",
                                    params={"online": "true"}, headers=ah, timeout=10, verify=VERIFY).json()
            still = [s for s in sessions.get("items", []) if s["username"] == username]
            check("★ 靠岸后服务端不再显示在线", not still, str(still)[:200])

            # 令牌还在（靠岸不该吊销登录令牌）
            me = api.me()
            check("★ 靠岸后令牌仍然有效（不用重新登舟）",
                  me.get("username") == username, str(me)[:160])

    finally:
        # 清理
        api.logout()
        if node_id:
            requests.delete(f"{BASE}/api/admin/nodes/{node_id}", headers=ah, timeout=10, verify=VERIFY)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
