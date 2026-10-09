"""安全原语：密码哈希、登录令牌。

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
import secrets

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
# 其它
# --------------------------------------------------------------------------


def gen_session_id() -> str:
    return secrets.token_hex(16)
