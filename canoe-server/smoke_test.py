"""轻舟 / Canoe Server —— 端到端冒烟测试。

用法（先启动服务端：python run.py 或 python run_local_https.py）：
    python smoke_test.py [base_url] [--insecure]

    --insecure  : 本地自签证书时跳过证书校验（只给本地调试用）

覆盖：注册 -> 登录 -> /api/config -> 心跳 -> 登出 -> 封禁踢下线 -> 中转层配置
      -> 客户端更新 -> 订阅更新 -> SSE 推送 -> 鉴权，
并断言所有客户端可见的响应里**绝对不出现**真实节点信息。

顺带验证 canoe-core 的两个自校验方法真的会拦截越界字段。
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
import time
import uuid
import zipfile

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_core import ENTRY_FIELDS, Api, ConfigResponse, EntryPayload  # noqa: E402

_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
_FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}

BASE = (_ARGS[0] if _ARGS else "http://127.0.0.1:8000").rstrip("/")
#: 本地自签证书才需要 --insecure，生产一律严格校验证书
VERIFY = "--insecure" not in _FLAGS


def new_client(**kwargs) -> httpx.Client:
    kwargs.setdefault("timeout", 15)
    return httpx.Client(base_url=BASE, verify=VERIFY, **kwargs)

# 管理员建节点时用的真实节点值 —— 绝不能出现在任何客户端可见的响应里
REAL_IP = "203.0.113.77"
REAL_UUID = "deadbeef-0000-1111-2222-333344445555"
REAL_SNI = "classified-node.invalid"

REAL_FIELD_PREFIX = "real_"

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def scan_real_keys(payload, path: str = "$") -> list[str]:
    """递归找出所有以 real_ 开头的键。"""
    hits: list[str] = []
    stack = [(payload, path)]
    while stack:
        cur, p = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if str(k).startswith(REAL_FIELD_PREFIX):
                    hits.append(f"{p}.{k}")
                stack.append((v, f"{p}.{k}"))
        elif isinstance(cur, list):
            stack.extend((v, f"{p}[{i}]") for i, v in enumerate(cur))
    return hits


def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    username = f"canoe_{suffix}"
    password = "canoe-pass-12345"
    device_id = f"dev-{uuid.uuid4()}"
    client = new_client()

    print(f"\n== 轻舟 / Canoe 服务端冒烟测试 @ {BASE} ==\n")

    # 0. 健康检查
    r = client.get(Api.HEALTH)
    check(f"GET {Api.HEALTH} 存活", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        print("  服务端没起来？先执行 python run.py")
        return 1
    check("健康检查返回协议版本", r.json().get("protocol") == 1, r.text[:200])

    # 1. 注册
    print("\n[1] 注册")
    r = client.post(Api.REGISTER, json={"username": username, "password": password})
    check(f"POST {Api.REGISTER} 201", r.status_code == 201, r.text[:200])

    r = client.post(Api.REGISTER, json={"username": username, "password": password})
    check("重复注册 409", r.status_code == 409, f"got {r.status_code}")

    r = client.post(Api.REGISTER, json={"username": "ab", "password": password})
    check("用户名过短 422", r.status_code == 422, f"got {r.status_code}")

    r = client.post(Api.REGISTER, json={"username": f"x{suffix}", "password": "short"})
    check("密码过短 422", r.status_code == 422, f"got {r.status_code}")

    # 2. 登录
    print("\n[2] 登录")
    r = client.post(
        Api.LOGIN,
        json={"username": username, "password": "wrong", "device_id": device_id},
    )
    check("错误密码 401", r.status_code == 401, f"got {r.status_code}")

    r = client.post(
        Api.LOGIN,
        json={
            "username": username,
            "password": password,
            "device_id": device_id,
            "device_name": "CANOE-TEST",
        },
    )
    check(f"POST {Api.LOGIN} 200", r.status_code == 200, r.text[:250])
    if r.status_code != 200:
        return 1
    body = r.json()
    token = body["token"]
    check("响应含 token", bool(token))
    check("响应含 expires_in", body.get("expires_in", 0) > 0)
    check("★ 登录响应无 real_* 字段", not scan_real_keys(body))
    check("登录响应含 user.node_name", "node_name" in body["user"])

    H = {"Authorization": f"Bearer {token}"}

    # 3. 鉴权
    print("\n[3] 鉴权")
    r = client.get(Api.ME)
    check("无 token 401/403", r.status_code in (401, 403), f"got {r.status_code}")
    r = client.get(Api.ME, headers={"Authorization": "Bearer garbage"})
    check("坏 token 401", r.status_code == 401, f"got {r.status_code}")
    r = client.get(Api.ME, headers=H)
    check("有效 token /api/me 200", r.status_code == 200, r.text[:200])

    # 4. 权限隔离
    print("\n[4] 权限隔离")
    r = client.get(Api.ADMIN_NODES, headers=H)
    check("普通用户访问 /api/admin/nodes 403", r.status_code == 403, f"got {r.status_code}")

    # 5. 管理员建节点
    print("\n[5] 管理员配置节点")
    r = client.post(
        Api.LOGIN,
        json={"username": "admin", "password": "canoe-admin-123", "device_id": "canoe-admin-dev"},
    )
    check("管理员登录 200", r.status_code == 200, r.text[:250])
    if r.status_code != 200:
        return 1
    admin_h = {"Authorization": f"Bearer {r.json()['token']}"}

    node_body = {
        "name": f"轻舟-{suffix}",
        "enabled": True,
        "sort_order": 1,
        "entry_host": "canoe.example.com",
        "entry_port": 443,
        "entry_path": f"/e/canoe{suffix}",
        "entry_sni": "canoe.example.com",
        "entry_transport": "ws",
        "real_protocol": "vless",
        "real_host": REAL_IP,
        "real_port": 8443,
        "real_uuid": REAL_UUID,
        "real_flow": "xtls-rprx-vision",
        "real_tls": True,
        "real_sni": REAL_SNI,
        "real_network": "tcp",
    }
    r = client.post(Api.ADMIN_NODES, json=node_body, headers=admin_h)
    check("创建节点 201", r.status_code == 201, r.text[:250])
    node_id = r.json().get("id") if r.status_code == 201 else None

    r = client.get(Api.ADMIN_NODES, headers=admin_h)
    check("admin 节点列表含 real（应当如此）", "real" in r.text)

    # 6. /api/config —— 核心安全断言
    print("\n[6] /api/config（核心安全断言）")
    r = client.get(Api.CONFIG, params={"device_id": device_id, "mode": "system_proxy"}, headers=H)
    check(f"GET {Api.CONFIG} 200", r.status_code == 200, r.text[:300])
    if r.status_code != 200:
        return 1
    raw = r.text
    data = r.json()

    check("★ 响应不含 real_* 字段", not scan_real_keys(data), str(scan_real_keys(data)))
    check("★ 响应不含真实 IP", REAL_IP not in raw)
    check("★ 响应不含真实 UUID", REAL_UUID not in raw)
    check("★ 响应不含真实 SNI", REAL_SNI not in raw)
    check("★ 响应不含 'real_' 字样", "real_" not in raw)
    check("返回节点显示名", bool(data.get("node_name")))
    check("返回入口地址（中转层）", data["entry"]["host"] == "canoe.example.com")
    check("返回短期凭证 token", bool(data.get("token")))
    check("entry 恰好是白名单 8 个字段", set(data["entry"]) == {
        "transport", "host", "port", "uuid", "path", "sni", "tls", "insecure"
    }, str(sorted(data["entry"])))

    # 7. canoe-core 的类型级校验真的会拦
    print("\n[7] canoe-core 类型级防护")
    resp = ConfigResponse.model_validate(data)
    resp.assert_no_real_fields()
    check("ConfigResponse 可校验且自检通过", True)

    # 7.1 extra=forbid：多传一个 real_host 直接构造失败
    try:
        EntryPayload(**{**data["entry"], "real_host": REAL_IP})
        check("EntryPayload 应当拒绝 real_host", False)
    except Exception:
        check("★ EntryPayload 拒绝 real_host（extra=forbid）", True)

    # 7.2 assert_whitelisted：子类偷偷加字段必须被抓出来
    #     （不能直接往实例里塞属性 —— pydantic v2 不会把它带进 model_dump）
    class SneakyEntry(EntryPayload):
        sneaky_extra: str = ""

    try:
        SneakyEntry(**data["entry"]).assert_whitelisted()
        check("assert_whitelisted 应当抓出越界字段", False)
    except AssertionError:
        check("★ assert_whitelisted 抓出子类新增字段", True)

    # 7.3 白名单本身没漂移
    check("★ EntryPayload 字段与 constants.ENTRY_FIELDS 一致",
          set(EntryPayload.model_fields.keys()) == set(ENTRY_FIELDS),
          str(sorted(EntryPayload.model_fields.keys())))

    session_id = data["session_id"]
    entry_ticket = data["token"]

    # 8. ticket 校验
    print("\n[8] 入口凭证校验")
    sys.path.insert(0, ".")
    from canoe_server.security import verify_ticket  # noqa: E402

    payload = verify_ticket(entry_ticket, device_id)
    check("ticket 验签通过", payload.get("u") is not None)
    check("ticket 记录了目标节点", payload.get("n") == node_id)
    try:
        verify_ticket(entry_ticket, "some-other-device-id")
        check("ticket 换设备应当失败", False)
    except ValueError:
        check("★ ticket 绑定设备", True)

    # 9. 心跳
    print("\n[9] 心跳")
    r = client.post(Api.HEARTBEAT, json={"session_id": session_id}, headers=H)
    check(f"POST {Api.HEARTBEAT} 200", r.status_code == 200, r.text[:200])
    check("心跳响应无 real_* 泄漏", r.status_code == 200 and not scan_real_keys(r.json()))

    r = client.get(Api.ADMIN_SESSIONS, params={"online": "true"}, headers=admin_h)
    check("管理员看到在线会话", r.status_code == 200 and len(r.json()["items"]) >= 1)

    # 10. 封禁 -> 立即踢下线
    print("\n[10] 封禁 -> 立即踢下线")
    me = client.get(Api.ME, headers=H).json()
    my_id = me["id"]

    r = client.post(f"{Api.ADMIN_USERS}/{my_id}/ban", headers=admin_h)
    check("封禁成功", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        check("封禁时吊销了令牌", r.json().get("revoked_tokens", 0) >= 1, r.text[:200])

    r = client.get(Api.ME, headers=H)
    check("★ 封禁后令牌立即失效（403 banned）", r.status_code == 403, f"got {r.status_code}")
    if r.status_code == 403:
        check("错误码为 banned", r.json()["detail"]["code"] == "banned", r.text[:200])

    r = client.get(Api.CONFIG, params={"device_id": device_id}, headers=H)
    check("★ 封禁后拿不到入口参数", r.status_code == 403, f"got {r.status_code}")

    r = client.post(f"{Api.ADMIN_USERS}/{my_id}/unban", headers=admin_h)
    check("解封成功", r.status_code == 200)

    # 11. 登出
    print("\n[11] 登出")
    r = client.post(Api.LOGOUT, json={"session_id": session_id}, headers=H)
    check(f"POST {Api.LOGOUT} 200", r.status_code == 200, r.text[:200])
    r = client.get(Api.ME, headers=H)
    check("★ 登出后令牌失效", r.status_code in (401, 403), f"got {r.status_code}")

    # 12. 中转层配置
    print("\n[12] 中转层配置")
    r = client.get(Api.ADMIN_RELAY_CONFIG, headers=admin_h)
    check("渲染 sing-box 配置 200", r.status_code == 200, r.text[:250])
    if r.status_code == 200:
        cfg = r.json()
        check("含 inbounds", len(cfg.get("inbounds", [])) >= 1)
        check("含 inbound→outbound 路由绑定", len(cfg.get("route", {}).get("rules", [])) >= 1)
        check("★ 真实 IP 出现在中转层配置里（应当如此）", REAL_IP in json.dumps(cfg))
        check("入口路径出现在中转层配置里", f"/e/canoe{suffix}" in json.dumps(cfg))
        check("behind_nginx 下只监听 127.0.0.1",
              all(i["listen"] == "127.0.0.1" for i in cfg["inbounds"]))

    r = client.get(Api.ADMIN_RELAY_CONFIG, params={"fmt": "nginx"}, headers=admin_h)
    check("渲染 nginx 配置 200", r.status_code == 200)
    if r.status_code == 200:
        check("nginx 配置含入口域名与端口映射",
              "canoe.example.com" in r.text and "127.0.0.1:" in r.text)

    r = client.get(Api.ADMIN_STATS, headers=admin_h)
    check("统计接口 200", r.status_code == 200, r.text[:250])

    # ======================================================================
    # 13. 客户端更新（/api/client/latest + 管理端发布 + 自托管下载）
    # ======================================================================
    print("\n[13] 客户端更新")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Canoe.exe", b"P" * (48 * 1024))
    package = buf.getvalue()
    digest = hashlib.sha256(package).hexdigest()
    release_version = f"9.{uuid.uuid4().int % 90 + 10}.0"

    r = client.post(
        f"{Api.ADMIN_RELEASES}/upload",
        headers=admin_h,
        data={"version": release_version, "notes": "冒烟测试用版本", "min_version": "1.0.0"},
        files={"file": ("Canoe-smoke.zip", package, "application/zip")},
    )
    check("上传发布安装包 201", r.status_code == 201, r.text[:250])
    release_id = r.json().get("id") if r.status_code == 201 else None
    check("登记了 sha256", r.status_code == 201 and r.json().get("sha256") == digest)

    # 匿名取最新版本 —— 客户端得先能检查更新，才谈得上登舟
    anon = new_client()
    r = anon.get(Api.CLIENT_LATEST)
    check(f"匿名 GET {Api.CLIENT_LATEST} 200", r.status_code == 200, r.text[:250])
    latest = r.json() if r.status_code == 200 else {}
    check("返回版本号", latest.get("version") == release_version, str(latest)[:200])
    check("返回下载地址", str(latest.get("url", "")).endswith("Canoe-smoke.zip"))
    check("返回 sha256", latest.get("sha256") == digest)
    check("★ 更新响应里没有任何节点信息", not scan_real_keys(latest), str(latest)[:200])
    check("★ 更新响应里没有 real_ 字样", "real_" not in json.dumps(latest))

    # 静态托管：下载回来字节必须一致
    if latest.get("url"):
        r = anon.get(str(latest["url"]).replace(BASE, ""))
        check("安装包可从 /downloads 下载", r.status_code == 200, str(r.status_code))
        check("★ 下载内容 sha256 与登记一致",
              hashlib.sha256(r.content).hexdigest() == digest)

    # ======================================================================
    # 14. 订阅更新（/api/subscription）
    # ======================================================================
    print("\n[14] 订阅更新")
    # 上面那个账号已经登出/被封过，重新开一个干净账号
    sub_name = f"sub_{uuid.uuid4().hex[:8]}"
    client.post(
        f"{Api.ADMIN_USERS}",
        headers=admin_h,
        json={"username": sub_name, "password": "canoe-pass-123", "expire_days": 3},
    )
    r = client.post(
        f"{Api.LOGIN}",
        json={
            "username": sub_name,
            "password": "canoe-pass-123",
            "device_id": "smoke-sub-0001",
            "device_name": "smoke",
        },
    )
    sub_h = {"Authorization": f"Bearer {r.json()['token']}"} if r.status_code == 200 else {}
    sub_uid = client.get(Api.ME, headers=sub_h).json().get("id") if sub_h else None

    r = client.get(Api.SUBSCRIPTION, headers=sub_h)
    check(f"GET {Api.SUBSCRIPTION} 200", r.status_code == 200, r.text[:250])
    sub = r.json() if r.status_code == 200 else {}
    first_revision = sub.get("revision", "")
    check("返回订阅指纹 revision", bool(first_revision), str(sub)[:200])
    check("返回 config_version", "config_version" in sub)
    check("★ 订阅里没有 real_* 字段", not scan_real_keys(sub), str(scan_real_keys(sub)))
    check("★ 订阅里没有 real_ 字样", "real_" not in json.dumps(sub))
    check("订阅的 entry 也恰好是白名单 8 个字段",
          sub.get("entry") is None or set(sub["entry"]) == set(ENTRY_FIELDS),
          str(sorted(sub.get("entry") or {})))

    # 改入口域名 -> 指纹必须变
    r = client.get(f"{Api.ADMIN_NODES}", headers=admin_h)
    nodes_now = r.json()["items"]
    target = next((n for n in nodes_now if n["id"] == node_id), nodes_now[0])
    patched = {
        "name": target["name"], "remark": target["remark"], "enabled": True,
        "sort_order": target["sort_order"],
        "entry_host": "changed.example.com", "entry_port": target["entry"]["port"],
        "entry_uuid": target["entry"]["uuid"], "entry_path": target["entry"]["path"],
        "entry_sni": "changed.example.com",
        "entry_transport": target["entry"]["transport"],
        "entry_tls": target["entry"]["tls"],
        "entry_insecure": target["entry"]["insecure"],
        **{f"real_{k}": v for k, v in target["real"].items()},
    }
    r = client.patch(f"{Api.ADMIN_NODES}/{target['id']}", json=patched, headers=admin_h)
    check("改节点入口 200", r.status_code == 200, r.text[:200])

    r = client.get(Api.SUBSCRIPTION, headers=sub_h)
    sub2 = r.json() if r.status_code == 200 else {}
    check("★ 入口变了 -> 订阅指纹也变",
          sub2.get("revision") != first_revision,
          f"{first_revision} -> {sub2.get('revision')}")

    # ======================================================================
    # 15. 推送（SSE /api/events）
    # ======================================================================
    print("\n[15] 推送 SSE")
    events: list[dict] = []
    stop = threading.Event()

    def _reader() -> None:
        try:
            with httpx.stream(
                "GET", f"{BASE}{Api.EVENTS}", headers=sub_h,
                timeout=httpx.Timeout(30, read=30), verify=VERIFY,
            ) as resp:
                if resp.status_code != 200:
                    events.append({"type": "__http", "code": resp.status_code})
                    return
                for line in resp.iter_lines():
                    if stop.is_set():
                        break
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
        except Exception as exc:  # noqa: BLE001
            events.append({"type": "__error", "detail": str(exc)})

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    time.sleep(2.5)
    check("★ 连上就收到 hello", any(e.get("type") == "hello" for e in events),
          str([e.get("type") for e in events]))
    hello = next((e for e in events if e.get("type") == "hello"), {})
    check("hello 带 config_version", "config_version" in hello, str(hello))
    check("hello 里也没有真实节点字段", not scan_real_keys(hello), str(hello))

    # 再改一次节点 -> config_changed
    patched["entry_host"] = "changed2.example.com"
    patched["entry_sni"] = "changed2.example.com"
    client.patch(f"{Api.ADMIN_NODES}/{target['id']}", json=patched, headers=admin_h)
    time.sleep(2.5)
    check("★ 节点变更推来 config_changed",
          any(e.get("type") == "config_changed" for e in events),
          str([e.get("type") for e in events]))
    changed = next((e for e in events if e.get("type") == "config_changed"), {})
    check("★ 广播里不带节点名（免得把别人的节点泄露给所有人）",
          "node_name" not in changed or not changed["node_name"], str(changed))

    # 封禁 -> kick
    if sub_uid:
        client.post(f"{Api.ADMIN_USERS}/{sub_uid}/ban", headers=admin_h)
        time.sleep(2.5)
        check("★ 封禁推来 kick", any(e.get("type") == "kick" for e in events),
              str([e.get("type") for e in events]))
        client.post(f"{Api.ADMIN_USERS}/{sub_uid}/unban", headers=admin_h)

    stop.set()

    # ======================================================================
    # 16. 推送与订阅的鉴权
    # ======================================================================
    print("\n[16] 推送 / 订阅的鉴权")
    check("无令牌订阅 -> 401", anon.get(Api.SUBSCRIPTION).status_code == 401)
    check("坏令牌订阅 -> 401",
          anon.get(Api.SUBSCRIPTION, headers={"Authorization": "Bearer nope"}).status_code == 401)
    check("无令牌连推送 -> 401", anon.get(Api.EVENTS).status_code == 401)
    check("坏令牌连推送 -> 401",
          anon.get(Api.EVENTS, headers={"Authorization": "Bearer nope"}).status_code == 401)
    check("推送统计进了 /api/admin/stats",
          "push" in client.get(Api.ADMIN_STATS, headers=admin_h).json())

    # ======================================================================
    # 17. Web 管理面板
    # ======================================================================
    print("\n[17] Web 管理面板")

    r = client.get("/", follow_redirects=False)
    check("根路径跳到面板", r.status_code in (301, 302, 307, 308), str(r.status_code))
    check("跳转目标是 /panel", "/panel" in (r.headers.get("location") or ""),
          str(r.headers.get("location")))

    for path, what in (("/panel/", "面板首页"), ("/panel/index.html", "index.html"),
                       ("/panel/app.js", "app.js"), ("/panel/style.css", "style.css"),
                       ("/panel/logo.png", "logo.png")):
        r = client.get(path)
        check(f"{what} 可访问", r.status_code == 200, f"{path} -> {r.status_code}")

    r = client.get("/panel/")
    check("面板页面带品牌名", "轻舟" in r.text, r.text[:120])
    check("面板引用了 app.js 和 style.css",
          "app.js" in r.text and "style.css" in r.text)

    # ★ 面板不该引用任何外部资源 —— 一个代理服务的后台不该去 ping 第三方
    external = []
    for path in ("/panel/", "/panel/app.js", "/panel/style.css"):
        page = client.get(path).text
        for token in ("http://", "https://", "//cdn", "//unpkg", "//fonts."):
            for chunk in page.split(token)[1:]:
                host = chunk.split("/")[0].split('"')[0].split("'")[0].split(")")[0]
                if host and not host.startswith("127.0.0.1") and not host.startswith("localhost"):
                    external.append(f"{path}: {token}{host}")
    check("★ 面板不引用任何外部资源（无 CDN）", not external, str(external[:5]))

    # 面板本身不需要令牌就能下载（它只是个前端），
    # 真正的权限在 /api/admin/* —— 这一条确认"藏 HTML"不是我们的防护手段
    check("★ 面板静态文件不需要登录（防护在 API 层，不是藏页面）",
          client.get("/panel/app.js").status_code == 200)

    # 清理
    if release_id:
        client.delete(f"{Api.ADMIN_RELEASES}/{release_id}", params={"delete_file": "true"},
                      headers=admin_h)
    if sub_uid:
        client.delete(f"{Api.ADMIN_USERS}/{sub_uid}", headers=admin_h)
    if node_id:
        client.delete(f"{Api.ADMIN_NODES}/{node_id}", headers=admin_h)
    client.delete(f"{Api.ADMIN_USERS}/{my_id}", headers=admin_h)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
