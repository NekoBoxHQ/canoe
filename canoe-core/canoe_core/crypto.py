"""订阅载荷的加密信封。

为什么要有这一层：订阅内容（节点链接）走 HTTPS 本来就已经是密文，
但 TLS 会在反向代理那一跳被解开 —— Nginx 日志、抓包工具、以及任何
能接触服务端进程的地方，看到的都是明文订阅。这里再包一层应用层加密，
让这些位置看到的只有密文。

⚠ 说清楚它的边界：**这不能阻止拿到客户端的用户把节点扒出来** ——
   客户端必须能解密，密钥就在它手上。这一层防的是"传输链路上的
   中间环节"，不是防客户端本人。真正决定"能不能分发"的是服务端
   随时可以不发（订阅拉回来是空）。

密钥怎么来：登录时服务端现发一把 sub_key（只存在会话里、只放在内存），
客户端拿它解密。服务端不认这个会话了 —— 密钥就没了，
客户端再拉也是空。这就是"服务端完全可控"的闭合点。

信封里的字段：
    alg      算法标识，留着将来换算法时能认出来
    salt     每次响应随机，配合 sub_key 派生实际密钥（相同明文也密文不同）
    nonce    AES-GCM 的 nonce，每次响应随机，绝不能重用
    data     密文（含 GCM tag）
    revision 明文指纹，供客户端判断"要不要重新解密"（对客户端有信息量，
             但订阅内容本身不出现在这里）

alg/revision 会作为 AAD 参与认证 —— 改这两个字段会导致解密失败，
不会出现"冒充的 revision"。
"""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, Field

#: 算法标识。换算法时一并换这个字符串与下面的 info，两边同时改。
ALG = "AES-256-GCM"

#: HKDF 的 info —— 把密钥限死在"订阅"这一个用途上。
#: 带 v1 是为了将来能平滑换协议，而不是让旧客户端解出新格式。
_INFO = b"canoe/subscription/v1"

SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
SUB_KEY_LEN = 32


class SubCryptoError(Exception):
    """订阅解密失败。

    最常见的原因不是"被攻击"，而是：换了会话（sub_key 变了）、
    或者服务端换了 TICKET_SECRET。这种情况下客户端该做的是
    重新登舟，而不是重试。
    """


class Envelope(BaseModel):
    """/api/subscription 的加密载荷。"""

    alg: str = ALG
    # 三个都必须有默认值：空信封（服务端不给订阅）就是全字段为空，
    # 不给默认值的话 Envelope() 直接校验失败，SubscriptionResponse
    # 的默认值也就建不出来。
    salt: str = Field(default="", description="base64，每次响应随机")
    nonce: str = Field(default="", description="base64，每次响应随机")
    data: str = Field(default="", description="base64 密文（含 GCM tag）")
    revision: str = Field(default="", description="明文指纹，用于判断要不要更新")

    #: 空信封 = 服务端主动不给（被封/到期/订阅被清空）。
    #: 客户端见到它必须销毁本地订阅。
    @property
    def is_empty(self) -> bool:
        return not self.data


def new_sub_key() -> str:
    """生成一把会话级订阅密钥（base64）。"""
    return base64.b64encode(os.urandom(SUB_KEY_LEN)).decode("ascii")


def _b64d(value: str, what: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise SubCryptoError(f"信封里的 {what} 不是合法的 base64") from exc


def _derive(sub_key: str, salt: bytes) -> bytes:
    try:
        raw = base64.b64decode(sub_key, validate=True)
    except (ValueError, TypeError) as exc:
        raise SubCryptoError("sub_key 不是合法的 base64") from exc
    if len(raw) != SUB_KEY_LEN:
        raise SubCryptoError(f"sub_key 长度不对（{len(raw)}，应为 {SUB_KEY_LEN}）")
    return HKDF(algorithm=SHA256(), length=KEY_LEN, salt=salt, info=_INFO).derive(raw)


def _aad(alg: str, revision: str) -> bytes:
    return f"{alg}\x00{revision}".encode("utf-8")


def seal(plaintext: str, sub_key: str, revision: str = "") -> Envelope:
    """把订阅明文装进信封。plaintext 为空字符串时返回空信封。"""
    if not plaintext:
        return Envelope(salt="", nonce="", data="", revision=revision)

    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = _derive(sub_key, salt)
    blob = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _aad(ALG, revision))
    return Envelope(
        salt=base64.b64encode(salt).decode("ascii"),
        nonce=base64.b64encode(nonce).decode("ascii"),
        data=base64.b64encode(blob).decode("ascii"),
        revision=revision,
    )


def unseal(envelope: Envelope, sub_key: str) -> str:
    """拆信封。空信封返回空字符串；解不开抛 SubCryptoError。"""
    if envelope.is_empty:
        return ""
    if envelope.alg != ALG:
        raise SubCryptoError(f"不认识的加密算法：{envelope.alg}")

    salt = _b64d(envelope.salt, "salt")
    nonce = _b64d(envelope.nonce, "nonce")
    blob = _b64d(envelope.data, "data")
    if len(salt) != SALT_LEN:
        raise SubCryptoError("salt 长度不对")
    if len(nonce) != NONCE_LEN:
        raise SubCryptoError("nonce 长度不对")

    key = _derive(sub_key, salt)
    try:
        return AESGCM(key).decrypt(nonce, blob, _aad(envelope.alg, envelope.revision)).decode("utf-8")
    except InvalidTag as exc:
        raise SubCryptoError(
            "订阅解密失败 —— 多半是会话换了（重新登舟即可），也可能是收到的不是本机密钥加密的内容"
        ) from exc
    except UnicodeDecodeError as exc:
        raise SubCryptoError("订阅明文不是合法的 UTF-8") from exc
