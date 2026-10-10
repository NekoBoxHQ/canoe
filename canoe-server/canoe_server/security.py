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
import os
import secrets
import threading

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import DATA_DIR, settings

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


_dummy_hash: str | None = None


def dummy_verify(password: str) -> None:
    """用户名不存在时"白跑一遍"同样开销的校验，把耗时拉平。

    为什么：`verify_password` 短路写在 `user is None or ...` 里的话，
    不存在的用户名会在几毫秒内返回，存在的要等 24 万次 PBKDF2（约 100ms）。
    光看响应时间就能把"哪些用户名是真的"问出来 —— 错误文案统一也挡不住。
    这里用一个随机口令的假哈希走一遍同样代价的路径，让两边的耗时同量级。
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _dummy_hash)


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


# --------------------------------------------------------------------------
# 订阅密钥的静态加密
# --------------------------------------------------------------------------
#
# 令牌存哈希（上面）让"拿到库"解不开登录态；但同一张 tokens 表里的
# sub_key 原本是**明文**的 —— 它正是用来解订阅信封的钥匙。于是"拖库"
# 这一条路又通了：库 + 任何一份密文（反代日志里记的响应体、抓包、
# 客服工单里贴过的接口返回）= 该会话的全部节点口令。
#
# 这里把它加密了再落库。密文带 "v1:" 前缀，好跟迁移前的老明文行区分开
# （老行由 init_db 一次性就地重写，见 database._migrate_sub_keys）。

_AT_REST_KEY_FILE = DATA_DIR / "subkey.key"
_at_rest_key: bytes | None = None
_at_rest_lock = threading.Lock()


def _resolve_at_rest_key() -> bytes:
    """主密钥。来源按优先级：SECRET_KEY → TICKET_SECRET → data/subkey.key。

    ⚠ 说实话：如果钥匙跟数据库都在 data/ 里被一起端走，第三条来源就白搭。
      第三种只是为了"开箱能用"；生产上配 SECRET_KEY（放在 .env，跟库分开）。
      对**只丢数据库**的常见情形（备份文件、拖库、SQL 注入），三种都挡得住。

    ⚠ 换掉这把钥匙 = 已存的 sub_key 全部解不开，客户端会收到空信封、
      销毁本地订阅（下次登录就恢复）。所以有值之后别再动。
    """
    for candidate in (settings.secret_key, settings.ticket_secret):
        if candidate and candidate.strip():
            # 不直接拿字符串当密钥：HKDF 一把，长度和分布都规整
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF

            return HKDF(
                algorithm=hashes.SHA256(), length=32, salt=None, info=b"canoe/sub-key-at-rest"
            ).derive(candidate.strip().encode("utf-8"))

    if _AT_REST_KEY_FILE.exists():
        raw = _AT_REST_KEY_FILE.read_bytes().strip()
        if raw:
            return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))

    key = os.urandom(32)
    _AT_REST_KEY_FILE.write_bytes(base64.urlsafe_b64encode(key))
    try:
        os.chmod(_AT_REST_KEY_FILE, 0o600)
    except OSError:
        pass  # Windows 上 chmod 只改只读位；ACL 本来就只有本用户
    return key


def _at_rest() -> bytes:
    global _at_rest_key
    if _at_rest_key is None:
        with _at_rest_lock:
            if _at_rest_key is None:
                _at_rest_key = _resolve_at_rest_key()
    return _at_rest_key


def encrypt_sub_key(raw: str) -> str:
    """加密待落库的 sub_key。空串原样返回（省得存一坨密文表示"没有"）。"""
    if not raw:
        return ""
    nonce = os.urandom(12)
    blob = AESGCM(_at_rest()).encrypt(nonce, raw.encode("utf-8"), None)
    return "v1:" + base64.urlsafe_b64encode(nonce + blob).decode().rstrip("=")


def decrypt_sub_key(stored: str) -> str:
    """解出 sub_key。

    解不开一律返回空串 —— 调用方按"这个会话没有密钥"处理（回空信封）。
    换过主密钥、数据被改过、或本来就是空，都会走到这条路上；**不能抛异常**，
    否则一条坏记录会让整个订阅接口 500。
    """
    if not stored:
        return ""
    if not stored.startswith("v1:"):
        # 迁移之前写进去的老明文行（正常路径下 init_db 已经改写过了）
        return stored
    try:
        blob = base64.urlsafe_b64decode(stored[3:] + "=" * (-len(stored[3:]) % 4))
        return AESGCM(_at_rest()).decrypt(blob[:12], blob[12:], None).decode("utf-8")
    except Exception:
        return ""
