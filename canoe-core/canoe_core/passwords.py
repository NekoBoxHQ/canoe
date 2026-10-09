"""密码哈希。

放在 canoe-core 里是因为两边都要用：
  · 阶段1 客户端的"本地假注册/假登录"（localauth.py）
  · 阶段3 服务端的真实账号系统

刻意不用 passlib —— 标准库 hashlib.pbkdf2_hmac 就够，依赖少、行为可预期、
不用跟着第三方库的版本兼容问题跑。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """返回 pbkdf2_sha256$迭代次数$salt$hash，**永不存明文**。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较，避免时序侧信道。"""
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


def new_token(nbytes: int = 32) -> str:
    """不可猜测的随机令牌（阶段3 服务端用）。"""
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """入库用。数据库里永远只存哈希，不存令牌原文。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
