"""轻舟 / Canoe 服务端 —— ORM 模型。

需求建议的 5 张表：users / tokens / nodes / user_node / sessions。
本文件是它们的实现（外加 audit_logs / config_meta 两张辅助表）。

★ 安全约定（改前先读 docs/04-security.md）★
    nodes.link 是**节点链接原文**，属于机密：只能进加密订阅（/api/subscription
    的信封）和管理端接口，绝不能出现在其他客户端可见的明文响应里。
    序列化一律走 services/nodes.py 里的函数，不要随手 model_dump()。
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

from canoe_core import Route

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
    #: 线路方向：out=出国（大陆直连，默认）/ in=回国（国外直连，国内走代理）。
    #: ★ 这个值只有管理员能改，客户端拉回去只显示、不给改 —— 跟订阅一样，
    #:   属于"服务端完全可控"那一层。见 canoe_core.constants.Route。
    route_mode: Mapped[str] = mapped_column(String(8), default=Route.OUT)
    #: 【已废弃】上一版是"每个客户手打订阅文本"，现在是"节点 + 分配给谁"。
    #: 列留着是为了不破坏老库；代码里已经不再读写它。
    subscription: Mapped[str] = mapped_column(Text, default="")
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
    #: 会话级订阅密钥（base64）。登录时下发给客户端，之后服务端用它加密
    #: 订阅响应。吊销令牌 = 这把钥匙一起作废，客户端再也解不开新订阅。
    sub_key: Mapped[str] = mapped_column(String(64), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="tokens")

    @property
    def is_valid(self) -> bool:
        return not self.revoked and as_aware(self.expire_at) > utcnow()


# ==========================================================================
# nodes  —— 节点（一行链接）
# ==========================================================================


class Node(Base):
    """一行 = 一个节点。**核心就是一行链接**：

        ss://2022-blake3-aes-128-gcm:<server_key>:<user_key>@host:port#名称
        vmess://... vless://... trojan://...

    管理员在面板上贴这一行、再加个备注（备注是给自己看的），
    然后把节点勾给需要的客户 —— 客户的订阅就是他所绑节点的链接拼起来。
    """

    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- 展示与状态（需求：name, status）---
    name: Mapped[str] = mapped_column(String(64))          # 客户端唯一能看到的节点信息
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)  # 需求: status
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    remark: Mapped[str] = mapped_column(String(255), default="")

    # --- ★ 节点本体：一行链接 ---
    #: 管理员贴进来的原始链接（ss:// vmess:// vless:// trojan://）。
    #: 这就是发给客户端的东西，原样存、原样发，不做改写。
    link: Mapped[str] = mapped_column(Text, default="")

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
    #: 可空 —— 订阅模式下服务端不分配节点，客户端从自己的订阅里挑。
    #: 留着这一列只是为了兼容老数据和"以后可能再关联节点"。
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), nullable=True
    )
    device_id: Mapped[str] = mapped_column(String(64), default="")

    # 需求: online / last_seen。online 不做成独立字段，而是由 last_seen 推算，
    # 避免"进程被 kill 后 online 永远是 true"的经典问题。见 is_online()。
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

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
