"""轻舟 / Canoe 服务端 —— ORM 模型。

需求建议的 5 张表：users / tokens / nodes / user_node / sessions。
本文件是它们的实现（外加 audit_logs / config_meta 两张辅助表）。

★ 安全约定（改前先读 docs/04-security.md）★
    nodes 表分两组字段：
        entry_*  -> 允许下发给客户端（中转层入口）
        real_*   -> 绝不下发（真实节点，只用于生成中转层配置）
    序列化一律走 services/nodes.py 的白名单函数，不要用 model_dump()。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import settings
from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(dt: datetime | None) -> datetime | None:
    """SQLite 取回来的可能是 naive datetime，统一补上 UTC。

    不加这一步会踩两个坑：
      naive - aware          -> TypeError
      naive.timestamp()      -> 按本地时区解释，时间戳偏移一个时区
    """
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def epoch(dt: datetime | None) -> int | None:
    """转 Unix 秒。None 安全，且不受 naive/aware 影响。"""
    d = as_aware(dt)
    return int(d.timestamp()) if d is not None else None


# ==========================================================================
# users
# ==========================================================================


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|banned
    role: Mapped[str] = mapped_column(String(16), default="user")  # user|admin
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_devices: Mapped[int] = mapped_column(Integer, default=3)
    remark: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tokens: Mapped[list["Token"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    node_links: Mapped[list["UserNode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_expired(self) -> bool:
        exp = as_aware(self.expire_at)
        return exp is not None and exp < utcnow()


# ==========================================================================
# tokens  —— 需求指定的表
# ==========================================================================


class Token(Base):
    """登录令牌。

    ⚠️ 与需求建议的表有一处**刻意不同**：
        需求写的是列名 `token`，这里用 `token_hash`。
        因为数据库里存 token 明文，等于库一泄漏所有人就能直接登录。
        存 SHA-256 后，校验时对请求里的 token 做同样的哈希再比对，
        功能完全一致，但拿到库也没用。见 docs/03-database.md。
    """

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="tokens")

    @property
    def is_valid(self) -> bool:
        return not self.revoked and as_aware(self.expire_at) > utcnow()


# ==========================================================================
# nodes  —— 真实节点 + 中转入口
# ==========================================================================


class Node(Base):
    """一行 = 一个「真实节点 + 对应的中转层入口」。

    需求建议的 address/port/protocol/secret/status 对应这里的
    real_host / real_port / real_protocol / real_uuid / enabled。
    入口部分（entry_*）是本项目为了"客户端拿不到真实节点"而增加的中转层参数。
    """

    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- 展示与状态（需求：name, status）---
    name: Mapped[str] = mapped_column(String(64))          # 客户端唯一能看到的节点信息
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)  # 需求: status
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    remark: Mapped[str] = mapped_column(String(255), default="")

    # --- 中转层入口 entry_*（允许下发）---
    entry_host: Mapped[str] = mapped_column(String(255), default="")
    entry_port: Mapped[int] = mapped_column(Integer, default=443)
    entry_uuid: Mapped[str] = mapped_column(String(64), default="")
    entry_path: Mapped[str] = mapped_column(String(255), default="")
    entry_sni: Mapped[str] = mapped_column(String(255), default="")
    entry_transport: Mapped[str] = mapped_column(String(16), default="ws")
    entry_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    entry_insecure: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- 真实节点 real_*（绝不下发）---
    real_protocol: Mapped[str] = mapped_column(String(16), default="vless")   # 需求: protocol
    real_host: Mapped[str] = mapped_column(String(255), default="")           # 需求: address
    real_port: Mapped[int] = mapped_column(Integer, default=443)              # 需求: port
    real_uuid: Mapped[str] = mapped_column(String(128), default="")           # 需求: secret
    real_flow: Mapped[str] = mapped_column(String(64), default="")
    real_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    real_sni: Mapped[str] = mapped_column(String(255), default="")
    real_fingerprint: Mapped[str] = mapped_column(String(32), default="chrome")
    real_network: Mapped[str] = mapped_column(String(16), default="tcp")
    real_ws_path: Mapped[str] = mapped_column(String(255), default="")
    real_ws_host: Mapped[str] = mapped_column(String(255), default="")
    real_grpc_service: Mapped[str] = mapped_column(String(255), default="")
    real_insecure: Mapped[bool] = mapped_column(Boolean, default=False)
    real_extra: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user_links: Mapped[list["UserNode"]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )


# ==========================================================================
# user_node  —— 需求指定的绑定表
# ==========================================================================


class UserNode(Base):
    """用户与节点的绑定。

    一个用户可以绑多个节点；实际用哪个由 sessions.node_id 决定。
    没绑任何节点的用户走自动分配（按 sort_order 取第一个可用节点）。
    """

    __tablename__ = "user_node"
    __table_args__ = (UniqueConstraint("user_id", "node_id", name="uq_user_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="node_links")
    node: Mapped[Node] = relationship(back_populates="user_links")


# ==========================================================================
# sessions  —— 需求指定的表
# ==========================================================================


class Session(Base):
    """一次代理会话。承载在线状态 + 凭证审计 + 踢下线。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"))
    device_id: Mapped[str] = mapped_column(String(64), default="")

    # 需求: online / last_seen。online 不做成独立字段，而是由 last_seen 推算，
    # 避免"进程被 kill 后 online 永远是 true"的经典问题。见 is_online()。
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    ticket_hash: Mapped[str] = mapped_column(String(64), default="")
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    mode: Mapped[str] = mapped_column(String(16), default="system_proxy")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def online(self) -> bool:
        """在线与否由 last_seen 推算，而不是存一个 online 布尔字段。

        存布尔字段的经典坑：客户端崩溃/被强杀时来不及置 false，
        那条记录会永远显示"在线"。按时间戳推算就不会有这个问题。
        """
        if self.revoked or self.ended_at is not None:
            return False
        return as_aware(self.last_seen) >= utcnow() - timedelta(seconds=settings.online_timeout)


# ==========================================================================
# 辅助表
# ==========================================================================


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConfigMeta(Base):
    """配置版本号。节点变更时自增，客户端心跳据此判断要不要重新拉配置。"""

    __tablename__ = "config_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ClientRelease(Base):
    """客户端版本发布记录 —— 「客户端更新」的数据源。

    一个 version 一行。安装包本体放在 release_dir 下，由 StaticFiles 提供下载；
    这里只存元数据（版本 / 文件名 / 摘要 / 说明）。

    `enabled=False` 可以临时把某个版本摘下来（比如发错包了），
    /api/client/latest 只会返回 enabled 里版本号最大的那个。
    """

    __tablename__ = "client_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    #: 低于这个版本必须升级。空表示不强制。
    min_version: Mapped[str] = mapped_column(String(32), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
