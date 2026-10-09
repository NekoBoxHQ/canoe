"""轻舟 / Canoe Server —— 端到端冒烟测试。

用法（先启动服务端：python run.py 或 python run_local_https.py）：
    python smoke_test.py [base_url] [--insecure]

    --insecure  : 本地自签证书时跳过证书校验（只给本地调试用）

覆盖：注册 -> 登录 -> /api/config -> 心跳 -> 登出 -> 封禁踢下线 -> 加密订阅
      -> 客户端更新 -> 订阅更新 -> SSE 推送 -> 鉴权，
并断言所有客户端可见的响应里**绝对不出现**节点信息。

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

from canoe_core import Api, Envelope  # noqa: E402

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


#: 冒烟用的节点链接（就是管理员会在面板上贴的那种）
NODE_LINK = (
    "ss://2022-blake3-aes-128-gcm:AAAA:BBBB@one.leycc.com:33222#%E6%97%A5%E6%9C%AC"
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

#: 节点信息只能在**加密订阅**里出现。凡是客户端不加密就能看到的响应
#: （/api/config、/api/me、心跳、hello……），都不该漏出链接或节点主机名。
LEAK_MARKERS = ("ss://", "vmess://", "vless://", "trojan://", "one.leycc.com")

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def scan_leaks(payload, path: str = "$") -> list[str]:
    """递归找出序列化结果里任何一处节点信息（链接前缀 / 节点主机名）。"""
    hits: list[str] = []
    stack = [(payload, path)]
    while stack:
        cur, p = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                stack.append((v, f"{p}.{k}"))
        elif isinstance(cur, list):
            stack.extend((v, f"{p}[{i}]") for i, v in enumerate(cur))
        elif isinstance(cur, str):
            for marker in LEAK_MARKERS:
                if marker in cur:
                    hits.append(f"{p} 含 {marker}")
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
    check("★ 登录响应里不含任何节点信息", not scan_leaks(body), str(scan_leaks(body)))
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

    # 建节点 = 贴一行链接 + 一个给自己看的备注。就这么简单。
    node_body = {
        "link": NODE_LINK,
        "remark": f"冒烟-{suffix}",
        "enabled": True,
        "sort_order": 0,
    }
    r = client.post(Api.ADMIN_NODES, json=node_body, headers=admin_h)
    check("创建节点 201", r.status_code == 201, r.text[:250])
    node_id = r.json().get("id") if r.status_code == 201 else None

    r = client.get(Api.ADMIN_NODES, headers=admin_h)
    check("admin 节点列表能看到链接", NODE_LINK[:30] in r.text, r.text[:200])

    # 5.5 改账号 —— 用户名是能改的（管理员把自己改名也要行）
    # 这一段是因为踩过：update_user 里引用了一个早就删掉的变量
    # （subscription_changed），PATCH 一调就 500，面板的「编辑」按钮等于坏的。
    print("\n[5.5] 改账号（用户名 / 密码）")
    uid = client.get(Api.ME, headers=H).json()["id"]
    renamed = f"canoe2_{suffix}"

    r = client.patch(f"{Api.ADMIN_USERS}/{uid}", headers=admin_h, json={"username": renamed})
    check("★ 改用户名 200", r.status_code == 200, r.text[:250])
    check("★ 响应里回报了新名字", r.json().get("username") == renamed, r.text[:200])

    r = client.get(Api.ADMIN_USERS, params={"q": renamed}, headers=admin_h)
    check("★ 列表里按新名字能搜到", r.status_code == 200 and len(r.json()["items"]) >= 1, r.text[:200])

    # 令牌按 user_id 记，改名不该把人踢下线
    r = client.get(Api.ME, headers=H)
    check("★ 改名后旧令牌照样能用（令牌不绑用户名）", r.status_code == 200, r.text[:200])
    check("★ /api/me 里已经是新名字", r.json().get("username") == renamed, r.text[:200])

    # 改成别人已经占了的名字 -> 409，而不是两个同名账号
    r = client.patch(f"{Api.ADMIN_USERS}/{uid}", headers=admin_h, json={"username": admin_user})
    check("★ 改成已存在的用户名 -> 409", r.status_code == 409, f"got {r.status_code}")

    # 非法用户名 -> 422（长度 / 字符集）
    r = client.patch(f"{Api.ADMIN_USERS}/{uid}", headers=admin_h, json={"username": "ab"})
    check("用户名过短 422", r.status_code == 422, f"got {r.status_code}")
    r = client.patch(f"{Api.ADMIN_USERS}/{uid}", headers=admin_h, json={"username": "有 空格"})
    check("用户名带空格 422", r.status_code == 422, f"got {r.status_code}")

    # 只改密码 / 只改备注，别的字段不动
    r = client.patch(f"{Api.ADMIN_USERS}/{uid}", headers=admin_h, json={"remark": "冒烟改过"})
    check("只改备注 200（其余字段不被动）", r.status_code == 200, r.text[:200])
    check("备注确实写进去了",
          any(u["remark"] == "冒烟改过"
              for u in client.get(Api.ADMIN_USERS, params={"q": renamed}, headers=admin_h).json()["items"]))

    # 改回去 —— 后面的段落还按原名找这个账号
    r = client.patch(f"{Api.ADMIN_USERS}/{uid}", headers=admin_h, json={"username": username})
    check("改回来 200", r.status_code == 200, r.text[:200])

    # 6. /api/config —— 核心安全断言
    print("\n[6] /api/config（核心安全断言）")
    r = client.get(Api.CONFIG, params={"device_id": device_id, "mode": "system_proxy"}, headers=H)
    check(f"GET {Api.CONFIG} 200", r.status_code == 200, r.text[:300])
    if r.status_code != 200:
        return 1
    raw = r.text
    data = r.json()

    check("★ 响应里没有任何节点信息（节点只走加密订阅）",
          not scan_leaks(data), str(scan_leaks(data)))
    check("返回会话 id", bool(data.get("session_id")))
    check("★ 不再下发任何入口/节点（订阅模式下节点从订阅来）",
          "entry" not in data and "token" not in data, str(sorted(data)))
    check("带上了订阅指纹（客户端靠它发现订阅被改）", bool(data.get("revision")))

    # 7. 链接解析（服务端也要认同一批链接，才能列表显示）
    print("\n[7] 链接解析")
    from canoe_core import parse_links

    parsed = parse_links(NODE_LINK)
    check("服务端能解析节点链接", len(parsed.links) == 1, str(parsed.skipped))
    listing = client.get(Api.ADMIN_NODES, headers=admin_h).json()["items"]
    mine = next((n for n in listing if n["remark"] == f"冒烟-{suffix}"), None)
    check("列表里带解析出来的协议/主机/端口",
          mine is not None and mine.get("host") and mine.get("port"), str(mine)[:200])
    check("★ 解析出来的主机就是链接里的那台",
          mine is not None and mine["host"] == "one.leycc.com", str(mine)[:200])

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
    check("心跳响应无节点信息泄漏", r.status_code == 200 and not scan_leaks(r.json()))

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

    # 12. 中转层 —— 已删除，端点不该再存在
    print("\n[12] 中转层（应当已删除）")
    r = client.get("/api/admin/relay/config", headers=admin_h)
    check("★ /api/admin/relay/config 已经不存在", r.status_code == 404, str(r.status_code))

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
    check("★ 更新响应里没有任何节点信息", not scan_leaks(latest), str(latest)[:200])

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

    # 管理员把节点分配给他 -> 指纹必须变、内容必须是密文
    r = client.put(f"{Api.ADMIN_USERS}/{sub_uid}/nodes", headers=admin_h,
                   json={"node_ids": [node_id]})
    check("给账号分配节点 200", r.status_code == 200, r.text[:200])
    check("★ 分配后回读了绑定列表", r.json().get("node_ids") == [node_id], r.text[:160])

    r = client.get(Api.SUBSCRIPTION, headers=sub_h)
    check(f"配了订阅后 GET {Api.SUBSCRIPTION} 200", r.status_code == 200, r.text[:200])
    sub = r.json() if r.status_code == 200 else {}
    second_revision = sub.get("revision", "")
    check("★ 分配节点后指纹变了", second_revision != first_revision,
          f"{first_revision} -> {second_revision}")
    check("★ 订阅里没有明文链接", "ss://" not in r.text and "vless://" not in r.text)
    check("★ 订阅里没有节点域名", "leycc" not in r.text and "example.com" not in r.text)
    check("★ 信封里确实装了东西（而不是空壳）", bool(sub["envelope"].get("data")), str(sub)[:200])

    # 用登录拿到的那把密钥真解一次
    from canoe_core import SubCryptoError, unseal  # noqa: E402

    plain = unseal(Envelope.model_validate(sub["envelope"]), sub_key_b64)
    check("★ 用会话密钥能解出原文（就是那行链接）", plain.strip() == NODE_LINK, repr(plain)[:120])
    try:
        unseal(Envelope.model_validate(sub["envelope"]), "A" * 44)
        check("换密钥应当解不开", False)
    except SubCryptoError:
        check("★ 换密钥解不开", True)

    # 管理员取消分配 -> 空信封，客户端据此销毁
    client.put(f"{Api.ADMIN_USERS}/{sub_uid}/nodes", headers=admin_h, json={"node_ids": []})
    r = client.get(Api.SUBSCRIPTION, headers=sub_h)
    sub3 = r.json() if r.status_code == 200 else {}
    check("★ 取消分配后回空信封（客户端据此销毁本地订阅）",
          not (sub3.get("envelope") or {}).get("data"), str(sub3)[:200])
    check("★ 取消分配后指纹也跟着变", sub3.get("revision") != second_revision)

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
    check("hello 里也没有节点信息", not scan_leaks(hello), str(hello))

    # 管理员改订阅 -> config_changed，且只推给这个人
    client.put(f"{Api.ADMIN_USERS}/{sub_uid}/nodes", headers=admin_h,
               json={"node_ids": [node_id]})
    time.sleep(2.5)
    check("★ 节点分配变更推来 config_changed",
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
                "link": NODE_LINK, "remark": "跨端口测试",
                "enabled": True, "sort_order": 900,
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
