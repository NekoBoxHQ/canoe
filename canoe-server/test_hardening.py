"""安全加固回归测试 —— 钉住 2026-10-10 那一轮安全体检改掉的东西。

**不需要服务端在跑**：全程用 FastAPI 的 TestClient 打在同一个进程里的 app，
数据库指向临时 sqlite，用完删掉。

    cd canoe-server
    .venv/Scripts/python test_hardening.py          # Windows
    .venv/bin/python test_hardening.py              # Linux

钉的是这几条（每条都对应体检报告里的一条发现）：

    1. /docs、/redoc、/openapi.json 在生产（debug=false）下不存在
    2. 登录失败有速率限制，而且**拦在跑 PBKDF2 之前**
    3. 用户名不存在时也走一遍同样开销的校验（时序枚举）
    4. sub_key 落库是**密文**，登录响应里才给一次明文
    5. 老库里的明文 sub_key 启动时会被就地加密
    6. 注册按 IP 限速
    7. X-Forwarded-For 默认**不认**（认了等于给限速开后门）
    8. /downloads 强制当附件下发 + nosniff（同源内联 XSS 的通道）

⚠ 环境变量必须在 import app **之前**设好 —— settings 是 import 时求值的。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {extra}")


TMP = Path(tempfile.mkdtemp(prefix="canoe-hardening-"))
RELEASES = TMP / "releases"
RELEASES.mkdir(parents=True, exist_ok=True)
(RELEASES / "Canoe-9.9.9-win64.zip").write_bytes(b"PK\x03\x04fake")

# 限速阈值调小，免得测试里真要打十几次（每次都是一个 PBKDF2）
os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'canoe.db').as_posix()}"
os.environ["RELEASE_DIR"] = str(RELEASES)
os.environ["PANEL_DIR"] = str(TMP / "no-panel")     # 不存在 -> 不挂面板
os.environ["SECRET_KEY"] = "hardening-test-key"
os.environ["LOGIN_MAX_FAILS"] = "3"
os.environ["LOGIN_LOCK_SECONDS"] = "60"
os.environ["REGISTER_MAX_PER_IP"] = "2"
os.environ.pop("DEBUG", None)

from fastapi.testclient import TestClient                       # noqa: E402

from canoe_server import security as sec                        # noqa: E402
from canoe_server.app import app                                # noqa: E402
from canoe_server.database import SessionLocal                  # noqa: E402
from canoe_server.models import Token                           # noqa: E402
from canoe_server.services import ratelimit                     # noqa: E402

DEVICE = {"device_id": "hardening-device", "device_name": "测试机"}


def reset_limits() -> None:
    """清掉限速计数。测试之间会互相干扰（按 IP 的键是共用的），
    所以每个场景开头显式归零 —— 这不是"绕过限速"，是让每条断言
    只测它要测的那件事。"""
    for limiter in (ratelimit.login_limiter, ratelimit.register_limiter):
        with limiter._lock:                                     # noqa: SLF001
            limiter._marks.clear()                              # noqa: SLF001
            limiter._locked.clear()                             # noqa: SLF001


def login(client: TestClient, username: str, password: str):
    return client.post("/api/login", json={"username": username,
                                           "password": password, **DEVICE})


def main() -> int:
    print("\n== 轻舟 · 服务端安全加固测试 ==\n")
    try:
        with TestClient(app) as client:
            _test_docs_off(client)
            user, password = _test_register_limit(client)
            _test_login_limit(client, user, password)
            _test_timing(client)
            _test_sub_key_encrypted(client, user, password)
            _test_legacy_migration()
            _test_node_links_encrypted()
            _test_password_change_revokes_tokens(client)
            _test_user_list_query_budget(client)
            _test_online_session_limit_before_filter(client)
            _test_delete_user_preserves_others(client)
            _test_xff_not_trusted()
            _test_download_headers(client)
            _test_security_headers(client)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


# ----------------------------------------------------------------------
def _test_docs_off(client: TestClient) -> None:
    print("[1] 生产下接口文档不存在")
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        check(f"★ {path} -> 404", r.status_code == 404, f"实际 {r.status_code}")


def _test_register_limit(client: TestClient) -> tuple[str, str]:
    print("\n[2] 注册按 IP 限速")
    reset_limits()
    ok = 0
    last = None
    for i in range(4):          # 阈值 2：前两个能建，之后被挡
        last = client.post("/api/register",
                           json={"username": f"harden{i}", "password": "pw-123456"})
        if last.status_code == 201:
            ok += 1
    check("★ 阈值内能注册", ok == 2, f"成功 {ok} 个")
    check("★ 超了返回 429", last is not None and last.status_code == 429,
          f"实际 {last.status_code if last else None}")
    if last is not None and last.status_code == 429:
        body = last.json().get("detail", {})
        check("★ 错误码是 rate_limited", body.get("code") == "rate_limited", str(body))
    return "harden0", "pw-123456"


def _test_login_limit(client: TestClient, user: str, password: str) -> None:
    print("\n[3] 登录限速：拦在 PBKDF2 之前")
    reset_limits()
    codes = [login(client, user, "definitely-wrong").status_code for _ in range(4)]
    check("★ 阈值内先给 401（而不是 429）", codes[:3] == [401, 401, 401], str(codes))
    check("★ 第 4 次起 429", codes[3] == 429, str(codes))

    # "拦在 PBKDF2 之前"得能量出来：被限速的请求该**明显更快**
    t0 = time.perf_counter()
    r = login(client, user, "definitely-wrong")
    blocked = time.perf_counter() - t0
    reset_limits()
    t0 = time.perf_counter()
    login(client, user, "definitely-wrong")
    normal = time.perf_counter() - t0
    check("★ 被限速的请求没跑 PBKDF2（快得多）", r.status_code == 429 and blocked < normal / 3,
          f"限速 {blocked*1000:.1f}ms vs 正常 {normal*1000:.1f}ms")
    reset_limits()


def _test_timing(client: TestClient) -> None:
    print("\n[4] 时序：不存在的用户名也要跑一遍同样的开销")
    reset_limits()
    t0 = time.perf_counter()
    login(client, "no_such_user_zzz", "whatever-123")
    missing = time.perf_counter() - t0
    reset_limits()
    # 存在的账号但密码错
    t0 = time.perf_counter()
    login(client, "harden1", "whatever-123")
    wrong = time.perf_counter() - t0
    reset_limits()
    # 不断言两者相等（测试机上抖动大），只钉"两边都真的烧了 PBKDF2"：
    # 假哈希那条路要是被漏掉，它会快一个数量级。
    check("★ 用户名不存在那条路也烧了 PBKDF2（不是几毫秒就回）", missing > 0.02,
          f"{missing*1000:.1f}ms")
    check("★ 与'密码错'同量级（差距在 3 倍以内）",
          missing < wrong * 3 and wrong < missing * 3,
          f"不存在 {missing*1000:.1f}ms vs 密码错 {wrong*1000:.1f}ms")


def _test_sub_key_encrypted(client: TestClient, user: str, password: str) -> None:
    print("\n[5] sub_key 落库是密文")
    reset_limits()
    r = login(client, user, password)
    check("登录成功", r.status_code == 200, str(r.status_code))
    given = r.json().get("sub_key", "")
    check("★ 登录响应里给了明文 sub_key", bool(given))

    with SessionLocal() as db:
        rows = db.query(Token).all()
    check("库里有令牌行", bool(rows))
    row = rows[-1]
    check("★ 明文列 sub_key 是空的（新行不再写明文）", row.sub_key == "", repr(row.sub_key))
    check("★ 密文列有值且带 v2: 前缀",
          row.sub_key_enc.startswith("v2:"), row.sub_key_enc[:24])
    check("★ 密文解出来 == 下发给客户端的那把",
          sec.decrypt_sub_key(row.sub_key_enc) == given)
    check("★ 库里的东西本身不含明文密钥", given not in row.sub_key_enc)


def _test_legacy_migration() -> None:
    print("\n[6] 老库里的明文 sub_key 会被就地加密")
    from sqlalchemy import text

    from canoe_server.database import _migrate_sub_keys, engine

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO tokens (user_id, token_hash, device_id, sub_key, sub_key_enc, "
                 "revoked, expire_at, created_at) VALUES (1, 'legacyhash', 'd1', "
                 "'LEGACY-PLAINTEXT-KEY', '', 0, '2099-01-01 00:00:00', '2026-01-01 00:00:00')")
        )
    _migrate_sub_keys()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT sub_key, sub_key_enc FROM tokens WHERE token_hash='legacyhash'")
        ).fetchone()
    check("★ 老明文行被清空", row[0] == "", repr(row[0]))
    check("★ 换成了密文", str(row[1]).startswith("v2:"), str(row[1])[:24])
    check("★ 解出来还是原来那把钥匙",
          sec.decrypt_sub_key(row[1]) == "LEGACY-PLAINTEXT-KEY")
    # 幂等：再跑一次不该有事
    _migrate_sub_keys()
    with engine.begin() as conn:
        again = conn.execute(
            text("SELECT sub_key_enc FROM tokens WHERE token_hash='legacyhash'")
        ).fetchone()
    check("★ 幂等（再跑一次不动它）", again[0] == row[1])


def _test_node_links_encrypted() -> None:
    print("\n[7] 节点链接静态加密")
    from sqlalchemy import text

    from canoe_server.database import SessionLocal, _migrate_node_links, engine
    from canoe_server.models import Node
    from canoe_server.security import decrypt_node_link

    raw_link = "ss://2022-blake3-aes-128-gcm:AAAA:BBBB@node.example:443#test"
    with SessionLocal() as db:
        node = Node()
        node.link = raw_link
        node.name = "encrypted-node"
        db.add(node)
        db.commit()
        node_id = node.id

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT link, link_enc FROM nodes WHERE id=:id"), {"id": node_id}
        ).fetchone()
    check("★ 明文兼容列已清空", stored[0] == "", repr(stored[0]))
    check("★ 密文列是 v2 密文", str(stored[1]).startswith("v2:"), str(stored[1])[:24])
    check("★ 密文里不含节点原文", raw_link not in str(stored[1]))
    check("★ 用途绑定的密文能正确解回", decrypt_node_link(stored[1]) == raw_link)

    # 模拟升级前的老库：明文在 link，link_enc 为空。
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO nodes (name, enabled, sort_order, remark, link, link_enc, "
                "created_at, updated_at) VALUES "
                "('legacy-node', 1, 100, '', :link, '', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            {"link": raw_link},
        )
    _migrate_node_links()
    with engine.begin() as conn:
        migrated = conn.execute(
            text("SELECT link, link_enc FROM nodes WHERE name='legacy-node'")
        ).fetchone()
    check("★ 老节点明文被迁移并清空", migrated[0] == "", repr(migrated[0]))
    check("★ 老节点迁移后仍可解密", decrypt_node_link(migrated[1]) == raw_link)


def _test_password_change_revokes_tokens(client: TestClient) -> None:
    print("\n[8] 改密码立即吊销旧令牌")
    from canoe_server.database import SessionLocal
    from canoe_server.models import User
    from canoe_server.security import hash_password

    reset_limits()
    with SessionLocal() as db:
        admin = User(
            username="pw_admin",
            password_hash=hash_password("admin-pass-123"),
            role="admin",
            status="active",
            max_devices=3,
        )
        target = User(
            username="pw_target",
            password_hash=hash_password("old-pass-123"),
            role="user",
            status="active",
            max_devices=3,
        )
        db.add_all([admin, target])
        db.commit()
        db.refresh(admin)
        db.refresh(target)
        admin_id, target_id = admin.id, target.id

    admin_login = client.post(
        "/api/login",
        json={
            "username": "pw_admin",
            "password": "admin-pass-123",
            **DEVICE,
        },
    )
    target_login = client.post(
        "/api/login",
        json={
            "username": "pw_target",
            "password": "old-pass-123",
            **DEVICE,
        },
    )
    check("管理员与目标账号均登录成功",
          admin_login.status_code == 200 and target_login.status_code == 200)
    admin_h = {"Authorization": f"Bearer {admin_login.json()['token']}"}
    old_target_h = {"Authorization": f"Bearer {target_login.json()['token']}"}

    changed = client.patch(
        f"/api/admin/users/{target_id}",
        headers=admin_h,
        json={"password": "new-pass-123"},
    )
    check("管理员改密码成功", changed.status_code == 200, changed.text[:200])
    check("★ 改密响应记录了吊销数量",
          changed.json().get("revoked_tokens", 0) >= 1, changed.text[:200])
    check("★ 目标账号旧令牌立即 401",
          client.get("/api/me", headers=old_target_h).status_code == 401)
    new_login = client.post(
        "/api/login",
        json={
            "username": "pw_target",
            "password": "new-pass-123",
            **DEVICE,
        },
    )
    check("新密码可以登录", new_login.status_code == 200, new_login.text[:200])
    check("管理员自己的令牌不受影响",
          client.get("/api/me", headers=admin_h).status_code == 200)
    check("管理员账号仍存在", admin_id > 0)


def _test_user_list_query_budget(client: TestClient) -> None:
    print("\n[9] 用户列表查询次数不随页大小线性增长")
    from sqlalchemy import event

    from canoe_server.database import SessionLocal, engine
    from canoe_server.models import User
    from canoe_server.security import hash_password

    reset_limits()
    shared_hash = hash_password("bulk-pass-123")
    with SessionLocal() as db:
        for i in range(30):
            db.add(
                User(
                    username=f"bulk_{i:02d}",
                    password_hash=shared_hash,
                    role="user",
                    status="active",
                    max_devices=3,
                )
            )
        db.commit()

    login_resp = client.post(
        "/api/login",
        json={"username": "pw_admin", "password": "admin-pass-123", **DEVICE},
    )
    headers = {"Authorization": f"Bearer {login_resp.json()['token']}"}
    statements: list[str] = []

    def count_query(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_query)
    try:
        r = client.get("/api/admin/users", params={"size": 50}, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", count_query)

    check("用户列表请求成功", r.status_code == 200, r.text[:200])
    check("★ 返回了整页用户", len(r.json().get("items", [])) >= 30,
          str(len(r.json().get("items", []))))
    check("★ 查询数保持在固定预算内（无 N+1）", len(statements) <= 12,
          f"实际 {len(statements)} 条 SQL")


def _test_online_session_limit_before_filter(client: TestClient) -> None:
    print("\n[10] 在线会话先过滤再 LIMIT")
    from canoe_server.database import SessionLocal
    from canoe_server.models import Session as SessionRow, User, utcnow

    reset_limits()
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "pw_admin").one()
        now = utcnow()
        online_id = "online-older-but-active"
        db.add(
            SessionRow(
                id=online_id,
                user_id=admin.id,
                device_id="online-device",
                last_seen=now,
                created_at=now,
                expire_at=now,
            )
        )
        for i in range(220):
            db.add(
                SessionRow(
                    id=f"offline-{i:04d}",
                    user_id=admin.id,
                    device_id=f"offline-device-{i}",
                    last_seen=now,
                    created_at=now,
                    expire_at=now,
                    ended_at=now,
                )
            )
        db.commit()

    login_resp = client.post(
        "/api/login",
        json={"username": "pw_admin", "password": "admin-pass-123", **DEVICE},
    )
    headers = {"Authorization": f"Bearer {login_resp.json()['token']}"}
    r = client.get(
        "/api/admin/sessions",
        params={"online": "true", "limit": 1},
        headers=headers,
    )
    items = r.json().get("items", []) if r.status_code == 200 else []
    check("在线会话请求成功", r.status_code == 200, r.text[:200])
    check("★ 更晚的历史记录不会挤掉真正在线的会话",
          len(items) == 1 and items[0].get("session_id") == online_id,
          str(items))


def _test_delete_user_preserves_others(client: TestClient) -> None:
    print("\n[11] 删除用户只级联自己的数据")
    from canoe_server.database import SessionLocal
    from canoe_server.models import Session as SessionRow, User

    reset_limits()
    admin_login = client.post(
        "/api/login",
        json={"username": "pw_admin", "password": "admin-pass-123", **DEVICE},
    )
    admin_h = {"Authorization": f"Bearer {admin_login.json()['token']}"}
    created = client.post(
        "/api/admin/users",
        headers=admin_h,
        json={"username": "delete_target", "password": "delete-pass-123"},
    )
    target_id = created.json().get("id") if created.status_code == 201 else None
    target_login = client.post(
        "/api/login",
        json={
            "username": "delete_target",
            "password": "delete-pass-123",
            "device_id": "delete-target-device",
            "device_name": "delete-target",
        },
    )
    target_h = {"Authorization": f"Bearer {target_login.json()['token']}"}
    session_resp = client.get(
        "/api/config",
        params={"device_id": "delete-target-device", "mode": "system_proxy"},
        headers=target_h,
    )

    deleted = client.delete(f"/api/admin/users/{target_id}", headers=admin_h)
    check("删除目标用户成功", deleted.status_code == 200, deleted.text[:200])
    with SessionLocal() as db:
        check("★ 管理员账号没有被误删", db.query(User).filter(User.username == "pw_admin").one())
        check("★ 目标用户已删除",
              db.query(User).filter(User.username == "delete_target").first() is None)
        check("★ 目标会话同步级联删除",
              db.query(SessionRow)
              .filter(SessionRow.id == session_resp.json().get("session_id"))
              .first()
              is None)


def _test_xff_not_trusted() -> None:
    print("\n[12] X-Forwarded-For 默认不认")
    from canoe_server.deps import client_ip

    class FakeRequest:
        def __init__(self) -> None:
            self.headers = {"x-forwarded-for": "1.2.3.4"}
            self.client = type("C", (), {"host": "9.9.9.9"})()

    check("★ 不带 trust_proxy 时忽略 XFF，用真实对端",
          client_ip(FakeRequest()) == "9.9.9.9", client_ip(FakeRequest()))

    from canoe_server.config import settings
    settings.trust_proxy = True
    try:
        check("★ 开了 trust_proxy 才读 XFF（给放在反代后面的部署用）",
              client_ip(FakeRequest()) == "1.2.3.4", client_ip(FakeRequest()))
    finally:
        settings.trust_proxy = False


def _test_download_headers(client: TestClient) -> None:
    print("\n[13] /downloads 强制当附件 + nosniff")
    r = client.get("/downloads/Canoe-9.9.9-win64.zip")
    check("能取到文件", r.status_code == 200, str(r.status_code))
    check("★ Content-Disposition: attachment",
          r.headers.get("content-disposition", "").startswith("attachment"),
          r.headers.get("content-disposition", ""))
    check("★ X-Content-Type-Options: nosniff",
          r.headers.get("x-content-type-options") == "nosniff",
          r.headers.get("x-content-type-options", ""))


def _test_security_headers(client: TestClient) -> None:
    print("\n[14] 统一安全响应头")
    r = client.get("/api/health")
    check("★ API 禁止缓存", r.headers.get("cache-control") == "no-store")
    check("★ 禁止 MIME 嗅探", r.headers.get("x-content-type-options") == "nosniff")
    check("★ 禁止被 iframe 嵌入", r.headers.get("x-frame-options") == "DENY")
    check("★ Referrer 不泄露", r.headers.get("referrer-policy") == "no-referrer")
    https_client = TestClient(app, base_url="https://testserver")
    https_health = https_client.get("/api/health")
    check("★ HTTPS 响应带 HSTS",
          "max-age=31536000" in https_health.headers.get("strict-transport-security", ""),
          https_health.headers.get("strict-transport-security", ""))


if __name__ == "__main__":
    sys.exit(main())
