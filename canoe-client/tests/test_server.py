"""轻舟客户端 · 与服务端的联调测试。

**要有一个在跑的服务端**（本地起一个即可）：

    cd canoe-server
    .venv/Scripts/python run_local_https.py          # https://127.0.0.1:8443

    cd canoe-client
    set CANOE_SERVER_URL=https://127.0.0.1:8443
    set CANOE_CA_BUNDLE=..\\canoe-server\\data\\certs\\local-cert.pem
    python tests/test_server.py

没设 CANOE_SERVER_URL 就整体跳过 —— 免得在没服务端的机器上红一片。

覆盖的是"客户端接线"这条链路：

    注册 -> 登录（拿到会话级订阅密钥）-> 拉加密订阅 -> 解密 -> 解析出节点
    -> 启航建会话 -> 服务端看到在线 -> 心跳 -> 靠岸 -> 会话结束
    -> 管理员清空订阅 -> 客户端再拉就是空

★ 重点断言：订阅内容在传输中是**密文**，明文一个字都不出现在响应里。
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

#: 样例订阅 —— 用户给的那种 SS2022 链接
SUB_ONE = (
    "ss://2022-blake3-aes-128-gcm:hlKPbKuiXS9LaEmUOq5HYA%3D%3D"
    "%3AJ8DQT4ZqDl%2Br3DQtWhI%2BZg%3D%3D@one.leycc.com:33222"
    "#%F0%9F%8C%8D%E6%97%A5%E6%9C%AC%E5%AE%B6%E5%AE%BD%F0%9F%8C%8D"
)
SUB_TWO = "ss://2022-blake3-aes-128-gcm:AAA:BBB@two.example.com:8443#备用节点"
SUB_BOTH = SUB_ONE + "\n" + SUB_TWO

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

    from canoe_client import links
    from canoe_client.api import CanoeApiError, api
    from canoe_client.config import config
    from canoe_client.kernel import kernel
    from canoe_client.options import RunOptions
    from canoe_client import update
    from canoe_core import SubCryptoError, unseal

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

    username = f"it_{uuid.uuid4().hex[:8]}"
    password = "canoe-pass-123"
    user_id = None

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
        check("★ 登录下发了一把会话级订阅密钥", len(result.sub_key) >= 40, str(len(result.sub_key)))
        check("令牌里有设备号", bool(config.device_id))

        me = api.me()
        user_id = me.get("id")
        check("拿到自己的 user_id（后面要用它改订阅）", bool(user_id))

        # --- 3. 空订阅：还没配置时应当拿到空信封，而不是报错 ---
        print("\n[3] 空订阅")
        resp, text = api.subscription_text()
        check("能拿到订阅响应", resp.revision != "", str(resp)[:120])
        check("★ 没配订阅时明文为空（而不是报错）", text == "", repr(text))
        check("★ 空订阅时信封也是空的", resp.envelope.is_empty)

        # --- 4. 管理员配置订阅 ---
        print("\n[4] 管理员配置订阅")
        r = requests.patch(f"{BASE}/api/admin/users/{user_id}", headers=ah,
                           json={"subscription": SUB_BOTH}, timeout=10, verify=VERIFY)
        check("配置订阅 200", r.status_code == 200, r.text[:200])
        check("★ 服务端报告订阅变了（会触发推送）",
              r.json().get("subscription_changed") is True, r.text[:160])

        # --- 5. 拉加密订阅 ---
        print("\n[5] 拉订阅（加密）")
        raw = requests.get(f"{BASE}/api/subscription", headers=api_headers(api.token),
                           timeout=10, verify=VERIFY)
        check("订阅接口 200", raw.status_code == 200, raw.text[:200])

        body = raw.text
        check("★ 响应里没有订阅明文（域名）", "one.leycc.com" not in body and "leycc" not in body)
        check("★ 响应里没有 ss:// 链接", "ss://" not in body)
        check("★ 响应里没有节点名", "日本家宽" not in body)
        check("★ 响应里只有密文信封", "envelope" in body and "data" in body)

        resp, text = api.subscription_text()
        check("★ 客户端解密后拿到两条链接", text == SUB_BOTH, f"{len(text.splitlines())} 行")
        check("★ 指纹随订阅变化", resp.revision != "rev-empty")

        # 同一条订阅拉两次：密文不同（salt/nonce 随机），明文一样
        resp2, text2 = api.subscription_text()
        check("★ 两次密文不同（随机 salt/nonce）",
              resp2.envelope.data != resp.envelope.data)
        check("两次解出来一样", text2 == text)

        # 换一把密钥解不开
        try:
            unseal(resp.envelope, "A" * 44)
            check("换密钥应当解不开", False)
        except SubCryptoError:
            check("★ 换密钥解不开（密钥确实参与了加密）", True)

        # --- 6. 解析节点 ---
        print("\n[6] 解析订阅里的节点")
        link = links.pick(text)
        check("解析出节点", link is not None)
        if link is not None:
            check("★ 自动选中的是第一个", link.name == "🌍日本家宽🌍", link.name)
            check("出站类型 shadowsocks", link.outbound["type"] == "shadowsocks")
            check("主机端口来自订阅",
                  link.outbound["server"] == "one.leycc.com"
                  and link.outbound["server_port"] == 33222)
            check("★ SS2022 多用户密码是 server_key:user_key",
                  link.outbound["password"].count(":") == 1, link.outbound["password"])
            check("解析结果里带着完整链接（不是服务端给的）", link.raw == SUB_ONE)

        # --- 7. 启航 ---
        print("\n[7] 启航（真起内核）")
        cfg = api.fetch_config("system_proxy")
        check("拿到会话 id", bool(cfg.session_id))
        check("★ 配置响应里没有 entry（节点不再由服务端下发）",
              not hasattr(cfg, "entry") or getattr(cfg, "entry", None) is None)
        check("★ 配置里不含真实 IP", "198.51.100.7" not in cfg.model_dump_json())
        check("配置里带了订阅指纹", cfg.revision != "")

        exe = config.find_singbox()
        if exe is None:
            skip("起内核", "bin/sing-box.exe 不在")
        else:
            opts = RunOptions(use_system_proxy=True, use_tun=False,
                              mixed_port=22999, log_level="warn")
            kernel.start(link.outbound, opts)
            check("内核在跑", kernel.running)

            time.sleep(1)
            sessions = requests.get(f"{BASE}/api/admin/sessions",
                                    params={"online": "true"}, headers=ah, timeout=10,
                                    verify=VERIFY).json()
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
            check("★ 心跳带着订阅指纹（客户端靠它发现订阅被改）",
                  bool(data.get("revision")), str(data)[:160])

            # --- 9. 靠岸 ---
            print("\n[9] 靠岸")
            api.stop_session(cfg.session_id)
            kernel.stop()
            check("内核已停", not kernel.running)

            time.sleep(1)
            sessions = requests.get(f"{BASE}/api/admin/sessions",
                                    params={"online": "true"}, headers=ah, timeout=10,
                                    verify=VERIFY).json()
            still = [s for s in sessions.get("items", []) if s["username"] == username]
            check("★ 靠岸后服务端不再显示在线", not still, str(still)[:200])

            me2 = api.me()
            check("★ 靠岸后令牌仍然有效（不用重新登舟）",
                  me2.get("username") == username, str(me2)[:160])

        # --- 10. 客户端更新（也要先证明身份）---
        print("\n[10] 客户端更新")
        try:
            rel = api.latest_release()
            check("拿到最新版本", bool(rel.version), str(rel)[:120])
        except CanoeApiError as exc:
            check("没发布过版本时给出可读错误", "版本" in exc.message, exc.message)

        from canoe_core import VERSION
        check("版本比较可用", update.compare_versions("1.1.0", VERSION) >= -1)

        # --- 11. 管理员清空订阅 -> 客户端拉回来是空 ---
        print("\n[11] 管理员停止分发")
        r = requests.patch(f"{BASE}/api/admin/users/{user_id}", headers=ah,
                           json={"subscription": ""}, timeout=10, verify=VERIFY)
        check("清空订阅 200", r.status_code == 200, r.text[:160])
        resp3, text3 = api.subscription_text()
        check("★ 清空后客户端拉到的是空", text3 == "", repr(text3))
        check("★ 清空后信封也是空的", resp3.envelope.is_empty)
        check("★ 指纹跟着变了（客户端能察觉）", resp3.revision != resp.revision)

    finally:
        api.logout()
        if user_id:
            requests.delete(f"{BASE}/api/admin/users/{user_id}", headers=ah,
                            timeout=10, verify=VERIFY)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


def api_headers(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


if __name__ == "__main__":
    raise SystemExit(main())
