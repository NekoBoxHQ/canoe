"""安全原语：密码哈希、登录令牌、入口凭证 ticket。

关于令牌方案：需求建议用 tokens 表存 `token`。
这里用**不透明随机令牌 + 存哈希**，而不是 JWT，理由：

  1. JWT 是无状态的，签发后无法撤销。"管理员点了封禁，用户还能用到过期"
     对代理工具是致命的——封禁必须立即生效。
  2. 存哈希让数据库泄漏不等于账号沦陷。
  3. 需求里本来就有 tokens 表，用它比引 JWT 更贴合。

代价：每次请求多一次索引查询。对这个量级的项目可以忽略。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from .config import settings

# --------------------------------------------------------------------------
# 密码哈希
# --------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 240_000
_ALGO = "pbkdf2_sha256"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_ALGO}${_PBKDF2_ITERATIONS}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _b64d(salt_b64), int(iterations)
        )
        return hmac.compare_digest(dk, _b64d(hash_b64))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# 登录令牌
# --------------------------------------------------------------------------


def new_token() -> str:
    """生成一个不可猜测的登录令牌。返回明文，只在登录响应里出现这一次。"""
    return secrets.token_urlsafe(settings.token_bytes)


def token_hash(token: str) -> str:
    """入库用。数据库里永远只有哈希。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 入口凭证 ticket（短期、绑定用户+节点+设备）
# --------------------------------------------------------------------------


def issue_ticket(user_id: int, node_id: int, device_id: str, ttl: int | None = None) -> tuple[str, int]:
    """签发入口凭证，返回 (ticket, 过期时间戳)。"""
    now = int(time.time())
    exp = now + (ttl or settings.entry_ticket_ttl)
    payload = {
        "u": user_id,
        "n": node_id,
        # 只放设备号的哈希前缀，ticket 泄漏也不直接暴露原始 device_id
        "d": hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:16],
        "iat": now,
        "exp": exp,
        "jti": secrets.token_hex(8),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(
        settings.ticket_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{body}.{_b64e(sig)}", exp


def verify_ticket(ticket: str, device_id: str | None = None) -> dict[str, Any]:
    """校验 ticket，失败抛 ValueError。"""
    try:
        body, sig_b64 = ticket.split(".", 1)
    except ValueError as exc:
        raise ValueError("ticket 格式错误") from exc

    expected = hmac.new(
        settings.ticket_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, _b64d(sig_b64)):
        raise ValueError("ticket 签名无效")

    try:
        payload = json.loads(_b64d(body))
    except (ValueError, TypeError) as exc:
        raise ValueError("ticket 内容损坏") from exc

    if payload.get("exp", 0) < int(time.time()):
        raise ValueError("ticket 已过期")

    if device_id is not None:
        expect = hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:16]
        if payload.get("d") != expect:
            raise ValueError("ticket 与设备不匹配")

    return payload


def ticket_digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 其它
# --------------------------------------------------------------------------


def gen_entry_uuid() -> str:
    import uuid

    return str(uuid.uuid4())


def gen_session_id() -> str:
    return secrets.token_hex(16)
