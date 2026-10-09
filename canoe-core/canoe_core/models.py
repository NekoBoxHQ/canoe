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


class ConfigResponse(BaseModel):
    """/api/config 的响应体。

    需求原文：返回「入口地址 + token + 节点显示名」，
    不能包含真实节点地址/端口/密码。这个模型就是那条要求的实现。
    """

    protocol: int = PROTOCOL_VERSION
    session_id: str
    node_name: str = Field(description="节点显示名，客户端唯一能看到的节点信息")
    token: str = Field(description="短期入口凭证 ticket")
    expires_at: int
    heartbeat_interval: int = 30
    config_version: int = 0
    entry: EntryPayload

    def assert_no_real_fields(self) -> None:
        """防御性断言：序列化结果里不能出现 real_* 字段，也不能出现入口之外的东西。"""
        blob = self.model_dump()
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


# --------------------------------------------------------------------------
# 心跳 / 登出 / 个人信息
# --------------------------------------------------------------------------


class HeartbeatRequest(BaseModel):
    session_id: str


class HeartbeatResponse(BaseModel):
    ok: bool = True
    expires_at: int
    config_version: int
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
