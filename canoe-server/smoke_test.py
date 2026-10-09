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
import os
from pathlib import Path
import sys
import threading
import time
import uuid
import zipfile

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_core import ENTRY_FIELDS, Api, ConfigResponse, EntryPayload, Envelope  # noqa: E402

def _admin_credentials() -> tuple[str, str]:
    """管理员账号密码。

    不写死 —— 换个环境（.env 里设了别的）就登不进去了。
    顺序：环境变量 -> 服务端的 .env -> 内置默认值。
    """
    user = os.environ.get("ADMIN_USERNAME", "")
    password = os.environ.get("ADMIN_PASSWORD", "")

    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key == "ADMIN_USERNAME" and not user:
                user = value
            elif key == "ADMIN_PASSWORD" and not password:
                password = value

    return user or "admin", password or "canoe-admin-123"


#: 冒烟用的样例订阅（一行 SS2022 链接）
SAMPLE_SUB = (
    "ss://2022-blake3-aes-128-gcm:AAAA:BBBB@one.leycc.com:33222#%E6%97%A5%E6%9C%AC\n"
    "vless://11111111-2222-3333-4444-555555555555@node.example.com:443"
    "?security=tls&type=ws&path=%2Fx#%E5%A4%87%E7%94%A8"
)

_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
_FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}

BASE = (_ARGS[0] if _ARGS else "http://127.0.0.1:8000").rstrip("/")
#: 本地自签证书才需要 --insecure，生产一律严格校验证书
VERIFY = "--insecure" not in _FLAGS


def new_client(**kwargs) -> httpx.Client:
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("base_url", BASE)
    return httpx.Client(verify=VERIFY, **kwargs)

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

    # ★ 契约断言：管理面板靠这两个字段判断"这人能不能进面板"。
    #   面板的 DOM 测试是 mock 的 API，mock 里手写了 role —— 于是真服务端
    #   漏返回 role 时它照样全绿，而实际上面板登录页永远报"不是管理员"。
    #   真正能兜住这种漂移的只有对着**真服务端**跑的测试，就是这里。
    # （这一步登的是普通用户，所以值就该是 user；随后管理员那次登录取 admin）
    check("★ 登录响应含 user.role（面板靠它放行）",
          body["user"].get("role") in ("user", "admin"), str(body["user"])[:200])

    H = {"Authorization": f"Bearer {token}"}

    # 3. 鉴权
    print("\n[3] 鉴权")
    r = client.get(Api.ME)
    check("无 token 401/403", r.status_code in (401, 403), f"got {r.status_code}")
    r = client.get(Api.ME, headers={"Authorization": "Bearer garbage"})
    check("坏 token 401", r.status_code == 401, f"got {r.status_code}")
    r = client.get(Api.ME, headers=H)
    check("有效 token /api/me 200", r.status_code == 200, r.text[:200])
    # 面板刷新页面后用 /api/me 重新校验身份，同样靠 role
    check("★ /api/me 含 role（面板刷新后靠它重新放行）",
          r.json().get("role") in ("user", "admin"), r.text[:200])

    # 4. 权限隔离
    print("\n[4] 权限隔离")
    r = client.get(Api.ADMIN_NODES, headers=H)
    check("普通用户访问 /api/admin/nodes 403", r.status_code == 403, f"got {r.status_code}")

    # 5. 管理员建节点
    print("\n[5] 管理员配置节点")
    admin_user, admin_pass = _admin_credentials()
    r = client.post(
        Api.LOGIN,
        json={"username": admin_user, "password": admin_pass, "device_id": "canoe-admin-dev"},
    )
    check("管理员登录 200", r.status_code == 200, r.text[:250])
    if r.status_code != 200:
        return 1
    admin_h = {"Authorization": f"Bearer {r.json()['token']}"}

    node_body = {
        "name": f"轻舟-{suffix}",
        "enabled": True,
        # 0 = 排最前。必须压过库里已有的节点（示例节点是 10，别人留的是 1），
        # 否则 /api/config 可能挑中另一个，后面"ticket 记录了目标节点"
        # 就会对着一个不是我们建的节点报错。
        "sort_order": 0,
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
    check("返回会话 id", bool(data.get("session_id")))
    check("★ 不再下发任何入口/节点（订阅模式下节点从订阅来）",
          "entry" not in data and "token" not in data, str(sorted(data)))
    check("带上了订阅指纹（客户端靠它发现订阅被改）", bool(data.get("revision")))

    # 7. canoe-core 的类型级校验真的会拦
    print("\n[7] canoe-core 类型级防护")
    resp = ConfigResponse.model_validate(data)
    resp.assert_no_real_fields()
    check("ConfigResponse 可校验且自检通过", True)

    # EntryPayload 现在只服务于中转层（已不在客户端链路上），
    # 但白名单这道防线本身还得是好的 —— 用字面量造一个来验。
    sample_entry = {
        "transport": "ws", "host": "entry.example.com", "port": 443,
        "uuid": "11111111-2222-3333-4444-555555555555",
        "path": "/e/x", "sni": "entry.example.com", "tls": True, "insecure": False,
    }

    # 7.1 extra=forbid：多传一个 real_host 直接构造失败
    try:
        EntryPayload(**{**sample_entry, "real_host": REAL_IP})
        check("EntryPayload 应当拒绝 real_host", False)
    except Exception:
        check("★ EntryPayload 拒绝 real_host（extra=forbid）", True)

    # 7.2 assert_whitelisted：子类偷偷加字段必须被抓出来
    #     （不能直接往实例里塞属性 —— pydantic v2 不会把它带进 model_dump）
    class SneakyEntry(EntryPayload):
        sneaky_extra: str = ""

    try:
        SneakyEntry(**sample_entry).assert_whitelisted()
        check("assert_whitelisted 应当抓出越界字段", False)
    except AssertionError:
        check("★ assert_whitelisted 抓出子类新增字段", True)

    # 7.3 白名单本身没漂移
    check("★ EntryPayload 字段与 constants.ENTRY_FIELDS 一致",
          set(EntryPayload.model_fields.keys()) == set(ENTRY_FIELDS),
          str(sorted(EntryPayload.model_fields.keys())))

    session_id = data["session_id"]

    # 8. 订阅的传输是密文
    print("\n[8] 订阅加密")
    sub_raw = client.get(Api.SUBSCRIPTION, headers=H)
    check(f"GET {Api.SUBSCRIPTION} 200", sub_raw.status_code == 200, sub_raw.text[:200])
    sub_body = sub_raw.text
    check("★ 订阅响应里没有明文链接", "ss://" not in sub_body and "vless://" not in sub_body)
    check("★ 订阅响应里没有节点域名", "leycc" not in sub_body and "example.com" not in sub_body)
    check("响应里是信封", "envelope" in sub_body and "data" in sub_body)

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
    # 版本号必须**确定性地**盖过库里已有的。
    # 以前是随机 9.10.0~9.99.0 —— 只要上一轮留下过一个更大的（比如跑崩了
    # 没走到清理那步），"取最新版本"就取到旧的，这里两条断言必红。
    # 现在读一遍现有列表，取最大主版本 +1。
    existing = client.get(Api.ADMIN_RELEASES, headers=admin_h)
    rows = existing.json().get("items", []) if existing.status_code == 200 else []
    majors = []
    for row in rows:
        try:
            majors.append(int(str(row.get("version", "0")).split(".")[0]))
        except (TypeError, ValueError):
            pass
    release_version = f"{(max(majors) if majors else 0) + 1}.0.0"

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
    # ★ 这里不能再登一次：同一 device_id 重复登录会吊销上一个令牌，
    #   sub_h 会当场失效（踩过一次）。密钥就从这次登录的响应里取。
    sub_key_b64 = r.json().get("sub_key", "") if r.status_code == 200 else ""
    sub_uid = client.get(Api.ME, headers=sub_h).json().get("id") if sub_h else None
    check("★ 登录下发会话级订阅密钥", bool(sub_key_b64), str(r.text)[:160])

    # 还没配订阅时：给空信封，不报错
    r = client.get(Api.SUBSCRIPTION, headers=sub_h)
    check(f"GET {Api.SUBSCRIPTION} 200", r.status_code == 200, r.text[:250])
    sub = r.json() if r.status_code == 200 else {}
    first_revision = sub.get("revision", "")
    check("返回订阅指纹 revision", bool(first_revision), str(sub)[:200])
    check("★ 没配订阅时回空信封（而不是报错）",
          not (sub.get("envelope") or {}).get("data"), str(sub)[:200])

    # 管理员贴上订阅 -> 指纹必须变、内容必须是密文
    r = client.patch(f"{Api.ADMIN_USERS}/{sub_uid}", headers=admin_h,
                     json={"subscription": SAMPLE_SUB})
    check("给账号配置订阅 200", r.status_code == 200, r.text[:200])
    check("★ 服务端报告订阅变了", r.json().get("subscription_changed") is True)

    r = client.get(Api.SUBSCRIPTION, headers=sub_h)
    check(f"配了订阅后 GET {Api.SUBSCRIPTION} 200", r.status_code == 200, r.text[:200])
    sub = r.json() if r.status_code == 200 else {}
    second_revision = sub.get("revision", "")
    check("★ 配了订阅后指纹变了", second_revision != first_revision,
          f"{first_revision} -> {second_revision}")
    check("★ 订阅里没有明文链接", "ss://" not in r.text and "vless://" not in r.text)
    check("★ 订阅里没有节点域名", "leycc" not in r.text and "example.com" not in r.text)
    check("★ 订阅里没有 real_* 字段", not scan_real_keys(sub), str(scan_real_keys(sub)))

    # 用登录拿到的那把密钥真解一次
    from canoe_core import SubCryptoError, unseal  # noqa: E402

    plain = unseal(Envelope.model_validate(sub["envelope"]), sub_key_b64)
    check("★ 用会话密钥能解出原文", plain == SAMPLE_SUB, repr(plain)[:120])
    try:
        unseal(Envelope.model_validate(sub["envelope"]), "A" * 44)
        check("换密钥应当解不开", False)
    except SubCryptoError:
        check("★ 换密钥解不开", True)

    # 管理员清空订阅 -> 空信封，客户端据此销毁
    client.patch(f"{Api.ADMIN_USERS}/{sub_uid}", headers=admin_h, json={"subscription": ""})
    r = client.get(Api.SUBSCRIPTION, headers=sub_h)
    sub3 = r.json() if r.status_code == 200 else {}
    check("★ 清空订阅后回空信封（客户端据此销毁本地订阅）",
          not (sub3.get("envelope") or {}).get("data"), str(sub3)[:200])
    check("★ 清空后指纹也跟着变", sub3.get("revision") != second_revision)

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
    check("hello 带订阅指纹", "revision" in hello, str(hello))
    check("★ hello 里没有订阅内容（推送是广播的，不能带内容）",
          "ss://" not in json.dumps(hello) and "envelope" not in hello, str(hello))
    check("hello 里也没有真实节点字段", not scan_real_keys(hello), str(hello))

    # 管理员改订阅 -> config_changed，且只推给这个人
    client.patch(f"{Api.ADMIN_USERS}/{sub_uid}", headers=admin_h,
                 json={"subscription": SAMPLE_SUB + "\n# 又加了一条"})
    time.sleep(2.5)
    check("★ 订阅变更推来 config_changed",
          any(e.get("type") == "config_changed" for e in events),
          str([e.get("type") for e in events]))
    changed = next((e for e in events if e.get("type") == "config_changed"), {})
    check("★ 广播里不带订阅内容",
          "ss://" not in json.dumps(changed) and "envelope" not in changed, str(changed))

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

    # 面板可以另开一个端口（PANEL_PORT）。另开的话，客户端口不该响应 /panel。
    # 用 CANOE_PANEL_URL 告诉测试面板在哪；不设就当同口。
    panel_base = (os.environ.get("CANOE_PANEL_URL") or BASE).rstrip("/")
    panel_client = client if panel_base == BASE else new_client(base_url=panel_base)
    split_ports = panel_base != BASE

    if split_ports:
        r = client.get("/panel/")
        check("★ 面板另开口时，客户端口不响应 /panel", r.status_code == 404,
              f"实际 {r.status_code}")
        r = client.get("/panel/app.js")
        check("★ 客户端口也不漏面板的静态文件", r.status_code == 404, str(r.status_code))
        check("★ 客户端口本身仍然正常（只是藏了面板）",
              client.get(Api.HEALTH).status_code == 200)
    else:
        r = client.get("/", follow_redirects=False)
        check("根路径跳到面板", r.status_code in (301, 302, 307, 308), str(r.status_code))
        check("跳转目标是 /panel", "/panel" in (r.headers.get("location") or ""),
              str(r.headers.get("location")))

    for path, what in (("/panel/", "面板首页"), ("/panel/index.html", "index.html"),
                       ("/panel/app.js", "app.js"), ("/panel/style.css", "style.css"),
                       ("/panel/logo.png", "logo.png")):
        r = panel_client.get(path)
        check(f"{what} 可访问", r.status_code == 200, f"{path} -> {r.status_code}")

    r = panel_client.get("/panel/")
    check("面板页面带品牌名", "轻舟" in r.text, r.text[:120])
    check("面板引用了 app.js 和 style.css",
          "app.js" in r.text and "style.css" in r.text)

    # ★ 面板不该引用任何外部资源 —— 一个代理服务的后台不该去 ping 第三方
    external = []
    for path in ("/panel/", "/panel/app.js", "/panel/style.css"):
        page = panel_client.get(path).text
        for token in ("http://", "https://", "//cdn", "//unpkg", "//fonts."):
            for chunk in page.split(token)[1:]:
                host = chunk.split("/")[0].split('"')[0].split("'")[0].split(")")[0]
                if host and not host.startswith("127.0.0.1") and not host.startswith("localhost"):
                    external.append(f"{path}: {token}{host}")
    check("★ 面板不引用任何外部资源（无 CDN）", not external, str(external[:5]))

    # 面板本身不需要令牌就能下载（它只是个前端），
    # 真正的权限在 /api/admin/* —— 这一条确认"藏 HTML"不是我们的防护手段
    check("★ 面板静态文件不需要登录（防护在 API 层，不是藏页面）",
          panel_client.get("/panel/app.js").status_code == 200)
    check("面板口也能调 API（同源，面板要用）",
          panel_client.get(Api.HEALTH).status_code == 200)

    if split_ports:
        # ★ 这条是"面板另开口"这个功能的核心不变式：
        #   两个端口必须是**同一个进程**在听（共享推送中心），
        #   否则你在面板上点的「踢下线」永远传不到客户端的长连接上。
        #   拆成两个进程的话，下面这条会红。
        cross_name = f"xp_{uuid.uuid4().hex[:8]}"
        client.post(Api.ADMIN_USERS, headers=admin_h,
                    json={"username": cross_name, "password": "canoe-pass-123",
                          "expire_days": 3})
        r = client.post(Api.LOGIN, json={
            "username": cross_name, "password": "canoe-pass-123",
            "device_id": "smoke-cross-0001", "device_name": "smoke"})
        cross_h = {"Authorization": f"Bearer {r.json()['token']}"} if r.status_code == 200 else {}
        cross_uid = client.get(Api.ME, headers=cross_h).json().get("id") if cross_h else None

        cross_events: list[dict] = []
        cross_stop = threading.Event()

        def _cross_reader() -> None:
            try:
                with httpx.stream("GET", f"{BASE}{Api.EVENTS}", headers=cross_h,
                                  timeout=httpx.Timeout(30, read=30), verify=VERIFY) as resp:
                    for line in resp.iter_lines():
                        if cross_stop.is_set():
                            break
                        if line.startswith("data: "):
                            cross_events.append(json.loads(line[6:]))
            except Exception:  # noqa: BLE001
                pass

        cross_thread = threading.Thread(target=_cross_reader, daemon=True)
        cross_thread.start()
        time.sleep(2.5)

        # 用**面板口**建个节点：广播应当打到客户端口的这条连接上
        r = panel_client.post(
            Api.ADMIN_NODES, headers=admin_h,
            json={
                "name": "跨端口测试", "remark": "", "enabled": True, "sort_order": 900,
                "entry_host": "cross.example.com", "entry_port": 443, "entry_uuid": "",
                "entry_path": "/e/cross", "entry_sni": "cross.example.com",
                "entry_transport": "ws", "entry_tls": True, "entry_insecure": False,
                "real_protocol": "vless", "real_host": "198.51.100.9", "real_port": 8443,
                "real_uuid": "11111111-2222-3333-4444-555555555555", "real_flow": "",
                "real_tls": True, "real_sni": "real.invalid", "real_fingerprint": "chrome",
                "real_network": "tcp", "real_ws_path": "", "real_ws_host": "",
                "real_grpc_service": "", "real_insecure": False, "real_extra": {},
            })
        check("面板口能建节点", r.status_code == 201, r.text[:200])
        cross_node = r.json().get("id") if r.status_code == 201 else None
        time.sleep(2.5)
        check("★ 面板口建的节点 -> 客户端口收到 config_changed",
              any(e.get("type") == "config_changed" for e in cross_events),
              str([e.get("type") for e in cross_events]))

        if cross_node:
            panel_client.delete(f"{Api.ADMIN_NODES}/{cross_node}", headers=admin_h)
        cross_stop.set()
        if cross_uid:
            client.delete(f"{Api.ADMIN_USERS}/{cross_uid}", headers=admin_h)

    if panel_client is not client:
        panel_client.close()

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
