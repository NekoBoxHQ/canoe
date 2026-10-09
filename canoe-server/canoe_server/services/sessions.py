"""令牌签发与撤销、会话生命周期。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as DBSession

from canoe_core import ErrorCode

from ..config import settings
from ..models import Session as SessionRow
from ..models import Token, User, utcnow
from ..security import gen_session_id, issue_ticket, new_token, ticket_digest, token_hash


class CanoeError(Exception):
    """带错误码的业务异常，路由层转成对应 HTTP 状态。"""

    def __init__(self, code: str, message: str, http_status: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


# --------------------------------------------------------------------------
# 登录令牌
# --------------------------------------------------------------------------


def issue_login_token(db: DBSession, user: User, device_id: str) -> tuple[str, Token]:
    """签发登录令牌。返回 (明文 token, Token 行)。

    同一设备重复登录时吊销旧令牌，避免令牌无限堆积。
    """
    db.execute(
        update(Token)
        .where(
            Token.user_id == user.id,
            Token.device_id == device_id,
            Token.revoked.is_(False),
        )
        .values(revoked=True)
    )

    raw = new_token()
    row = Token(
        user_id=user.id,
        token_hash=token_hash(raw),
        device_id=device_id,
        expire_at=utcnow() + timedelta(seconds=settings.token_ttl),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return raw, row


def revoke_login_token(db: DBSession, raw_token: str) -> None:
    db.execute(
        update(Token).where(Token.token_hash == token_hash(raw_token)).values(revoked=True)
    )
    db.commit()


def revoke_user_tokens(db: DBSession, user_id: int) -> int:
    """封禁/删除用户时调用：吊销其全部令牌，立即踢下线。"""
    result = db.execute(
        update(Token).where(Token.user_id == user_id, Token.revoked.is_(False)).values(revoked=True)
    )
    db.commit()
    return result.rowcount or 0


def purge_expired_tokens(db: DBSession) -> int:
    """清理过期令牌（可挂到定时任务上）。"""
    result = db.execute(delete(Token).where(Token.expire_at < utcnow()))
    db.commit()
    return result.rowcount or 0


# --------------------------------------------------------------------------
# 设备
# --------------------------------------------------------------------------


def ensure_device_allowed(db: DBSession, user: User, device_id: str, device_name: str = "") -> None:
    """检查设备数上限。设备信息本身记在 tokens 表里（device_id 列）。"""
    active = db.scalars(
        select(Token.device_id)
        .where(
            Token.user_id == user.id,
            Token.revoked.is_(False),
            Token.expire_at > utcnow(),
        )
        .distinct()
    ).all()

    if device_id in active:
        return
    if len(active) >= user.max_devices:
        raise CanoeError(
            ErrorCode.DEVICE_LIMIT,
            f"设备数已达上限（{user.max_devices}），请在后台移除旧设备",
            http_status=409,
        )
    _ = device_name


def ensure_user_usable(user: User) -> None:
    if user.status == "banned":
        raise CanoeError(ErrorCode.BANNED, "账号已被封禁")
    if user.is_expired:
        raise CanoeError(ErrorCode.EXPIRED, "账号已到期")


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------


def start_session(
    db: DBSession, user: User, node_id: int, device_id: str, mode: str, client_ip: str = ""
) -> tuple[SessionRow, str]:
    """创建会话并签发入口凭证，返回 (会话行, ticket)。"""
    ensure_user_usable(user)

    # 同一设备的旧会话先结束，避免僵尸会话堆积
    db.execute(
        update(SessionRow)
        .where(
            SessionRow.user_id == user.id,
            SessionRow.device_id == device_id,
            SessionRow.ended_at.is_(None),
        )
        .values(ended_at=utcnow())
    )

    session_id = gen_session_id()
    ticket, exp_epoch = issue_ticket(user.id, node_id, device_id)

    row = SessionRow(
        id=session_id,
        user_id=user.id,
        node_id=node_id,
        device_id=device_id,
        ticket_hash=ticket_digest(ticket),
        client_ip=client_ip,
        mode=mode,
        created_at=utcnow(),
        expire_at=datetime.fromtimestamp(exp_epoch, tz=timezone.utc),
        last_seen=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, ticket


def heartbeat(db: DBSession, session_id: str, user: User) -> SessionRow:
    row = db.get(SessionRow, session_id)
    if row is None or row.user_id != user.id:
        raise CanoeError(ErrorCode.NOT_FOUND, "会话不存在", http_status=404)
    if row.ended_at is not None:
        raise CanoeError(ErrorCode.NOT_FOUND, "会话已结束", http_status=409)

    # 心跳时重新校验账号状态 —— 管理员封禁/到期能在这里把客户端踢下线
    ensure_user_usable(user)

    if row.revoked:
        raise CanoeError(ErrorCode.UNAUTHORIZED, "会话已被服务端吊销")

    row.last_seen = utcnow()
    row.expire_at = utcnow() + timedelta(seconds=settings.entry_ticket_ttl)
    db.commit()
    db.refresh(row)
    return row


def stop_session(db: DBSession, session_id: str, user: User) -> None:
    row = db.get(SessionRow, session_id)
    if row is None or row.user_id != user.id:
        return
    if row.ended_at is None:
        row.ended_at = utcnow()
        db.commit()


def revoke_user_sessions(db: DBSession, user_id: int) -> int:
    result = db.execute(
        update(SessionRow)
        .where(SessionRow.user_id == user_id, SessionRow.ended_at.is_(None))
        .values(revoked=True, ended_at=utcnow())
    )
    db.commit()
    return result.rowcount or 0


def online_sessions(db: DBSession) -> list[SessionRow]:
    cutoff = utcnow() - timedelta(seconds=settings.online_timeout)
    return list(
        db.scalars(
            select(SessionRow)
            .where(
                SessionRow.revoked.is_(False),
                SessionRow.ended_at.is_(None),
                SessionRow.last_seen >= cutoff,
            )
            .order_by(SessionRow.last_seen.desc())
        ).all()
    )
