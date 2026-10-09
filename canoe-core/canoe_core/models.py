"""轻舟 / Canoe —— 客户端与服务端共享的契约模型。

★ 这个文件是"客户端拿不到真实节点"这条约束的类型级落点 ★

    EntryPayload   客户端能拿到的全部入口信息（白名单，逐字段显式列出）
    ConfigResponse /api/config 的响应体，里面只有 EntryPayload，没有别的

    真实节点（地址/端口/协议/密钥）**没有对应的模型**，它只以数据库行的形式
    存在于服务端；客户端代码根本 import 不到这样的类型。

这样即使将来有人往节点表加了字段，也不会顺着类型定义漏到客户端——
因为客户端拿数据的返回值类型就是 ConfigResponse.entry。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .constants import ENTRY_FIELDS, PROTOCOL_VERSION
from .crypto import Envelope

# --------------------------------------------------------------------------
# 公共
# --------------------------------------------------------------------------


class ApiError(BaseModel):
    """/api/* 出错时的统一响应体。"""

    code: str
    detail: str
    protocol: int = PROTOCOL_VERSION


# --------------------------------------------------------------------------
# 注册 / 登录
# --------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)


class RegisterResponse(BaseModel):
    id: int
    username: str
    created_at: int


class LoginRequest(BaseModel):
    username: str
    password: str
    device_id: str = Field(min_length=8, max_length=64)
    device_name: str = Field(default="", max_length=64)


class UserInfo(BaseModel):
    """只含账号信息，不含任何节点信息。"""

    id: int
    username: str
    status: str = "active"
    expire_at: int | None = None
    node_name: str | None = None


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    user: UserInfo
    sub_key: str = Field(
        default="",
        description=(
            "会话级订阅密钥。只在这个会话有效，客户端只放在内存里。"
            "订阅响应用它解密；服务端不再认这个会话，客户端就什么都拉不到。"
        ),
    )


# --------------------------------------------------------------------------
# 入口参数（客户端可见的唯一节点相关信息）
# --------------------------------------------------------------------------


class EntryPayload(BaseModel):
    """客户端能拿到的全部节点信息 —— 中转层入口。

    ⚠️ 往这里加字段前，先读 docs/04-security.md。
       这里**不允许**出现真实节点的地址/端口/协议/密钥。
    """

    model_config = ConfigDict(extra="forbid")

    transport: Literal["ws", "grpc", "tcp"] = "ws"
    host: str = Field(description="中转层入口域名/IP，不是真实节点")
    port: int
    uuid: str = Field(description="中转层入口 UUID（短期凭证的主体）")
    path: str = ""
    sni: str = ""
    tls: bool = True
    insecure: bool = False

    def assert_whitelisted(self) -> None:
        """自我校验：字段集合必须与 ENTRY_FIELDS 完全一致。"""
        actual = set(self.model_dump().keys())
        if actual != set(ENTRY_FIELDS):
            raise AssertionError(
                f"EntryPayload 字段集合偏离白名单。\n"
                f"  多出: {actual - ENTRY_FIELDS}\n"
                f"  缺少: {ENTRY_FIELDS - actual}\n"
                f"如果你是要新增入口字段，请同步更新 constants.ENTRY_FIELDS 和文档。"
            )


# --------------------------------------------------------------------------
# /api/config
# --------------------------------------------------------------------------


def _assert_no_real_fields(blob: Any) -> None:
    """递归找 real_* 键。任何下发模型的出网自检都走这里。"""
    stack: list[Any] = [blob]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.startswith("real_"):
                    raise AssertionError(f"响应体泄漏真实节点字段: {k}")
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)


class ConfigResponse(BaseModel):
    """/api/config 的响应体 —— 启航时建一条会话。

    订阅模式下这个接口**不再下发任何节点信息**：节点由客户端从
    自己的订阅里挑。这里只负责"记一笔你上线了"，供管理端看在线的
    人和做踢下线。

    节点信息一律走 /api/subscription 的加密信封（见 crypto.py）。
    """

    protocol: int = PROTOCOL_VERSION
    session_id: str
    node_name: str | None = Field(default=None, description="预留显示位；订阅模式下由客户端自己解析")
    expires_at: int
    heartbeat_interval: int = 30
    revision: str = Field(default="", description="订阅指纹，用于判断要不要重新拉订阅")

    def assert_no_real_fields(self) -> None:
        """防御性断言：序列化结果里不能出现 real_* 字段。"""
        _assert_no_real_fields(self.model_dump())


# --------------------------------------------------------------------------
# /api/subscription  —— 订阅更新
# --------------------------------------------------------------------------


class SubscriptionResponse(BaseModel):
    """/api/subscription 的响应体 —— 「订阅更新」用的。

    订阅内容（节点链接）**不以明文出现**，只有密文信封。
    明文由客户端用登录时拿到的 sub_key 在内存里解开。

    空信封（`envelope.is_empty`）= 服务端主动不给：账号被封、已到期、
    或管理员把订阅栏清空了。客户端见到它必须销毁本地订阅 ——
    这是"服务端完全可控"的落点。
    """

    protocol: int = PROTOCOL_VERSION
    node_name: str | None = Field(default=None, description="当前会分配到的节点显示名")
    expires_at: int | None = Field(default=None, description="账号到期时间")
    heartbeat_interval: int = 30
    revision: str = Field(default="", description="订阅指纹，客户端拿它判断要不要更新")
    envelope: Envelope = Field(
        default_factory=Envelope,
        description="加密后的订阅载荷；为空表示服务端不给",
    )

    def assert_no_real_fields(self) -> None:
        _assert_no_real_fields(self.model_dump())


# --------------------------------------------------------------------------
# /api/client/latest  —— 客户端更新
# --------------------------------------------------------------------------


class ClientReleaseResponse(BaseModel):
    """/api/client/latest 的响应体 —— 「客户端更新」用的。

    注意：这条**不要求登录**（客户端得先能检查更新，才谈得上登录），
    所以里面不能有任何与账号相关的东西。
    """

    version: str = Field(description="最新版本号，如 1.1.0")
    url: str = Field(default="", description="安装包下载地址（服务端自托管）")
    notes: str = Field(default="", description="更新说明，纯文本")
    published_at: int = 0
    size: int = Field(default=0, description="安装包字节数")
    sha256: str = Field(default="", description="安装包摘要，客户端可校验")
    min_version: str = Field(
        default="",
        description="低于这个版本必须升级（服务端可强制）。空表示不强制",
    )

    @property
    def notes_lines(self) -> list[str]:
        return [ln.strip() for ln in (self.notes or "").splitlines() if ln.strip()]


# --------------------------------------------------------------------------
# 心跳 / 登出 / 个人信息
# --------------------------------------------------------------------------


class HeartbeatRequest(BaseModel):
    session_id: str


class HeartbeatResponse(BaseModel):
    ok: bool = True
    expires_at: int
    #: 服务端当前的订阅指纹。和客户端手里那份不一样，客户端就该重新拉订阅。
    revision: str = ""
    revoked: bool = False
    node_name: str | None = None


class LogoutRequest(BaseModel):
    session_id: str | None = None
    all_devices: bool = False


class DeviceInfo(BaseModel):
    device_id: str
    name: str
    last_seen_at: int


class MeResponse(BaseModel):
    id: int
    username: str
    status: str
    expire_at: int | None
    node_name: str | None
    devices: list[DeviceInfo] = []


class HealthResponse(BaseModel):
    ok: bool = True
    app: str
    version: str
    protocol: int = PROTOCOL_VERSION
