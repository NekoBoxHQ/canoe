"""注册 / 登录 / 登出 / 个人信息。

路径严格按需求：POST /api/register、POST /api/login、POST /api/logout
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from canoe_core import (
    Api,
    DeviceInfo,
    ErrorCode,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RegisterRequest,
    RegisterResponse,
    UserInfo,
)

from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_user
from ..models import AuditLog, Token, User, as_aware, epoch, utcnow
from ..security import hash_password, token_hash, verify_password
from ..services.nodes import node_name_for
from ..services.sessions import (
    CanoeError,
    ensure_device_allowed,
    ensure_user_usable,
    issue_login_token,
    revoke_login_token,
    revoke_user_sessions,
    revoke_user_tokens,
    stop_session,
)

router = APIRouter(tags=["auth"])
optional_bearer = HTTPBearer(auto_error=False)


def _audit(db: DBSession, user_id: int | None, action: str, detail: str = "", ip: str = "") -> None:
    db.add(AuditLog(user_id=user_id, action=action, detail=detail, ip=ip))
    db.commit()


# --------------------------------------------------------------------------
# POST /api/register
# --------------------------------------------------------------------------


@router.post(Api.REGISTER, response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: DBSession = Depends(get_db)):
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "username_taken", "detail": "用户名已存在"},
        )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role="user",
        status="active",
        max_devices=settings.default_max_devices,
        expire_at=utcnow() + timedelta(days=settings.default_expire_days),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _audit(db, user.id, "register", user.username, client_ip(request))
    return RegisterResponse(
        id=user.id, username=user.username, created_at=epoch(user.created_at) or 0
    )


# --------------------------------------------------------------------------
# POST /api/login
# --------------------------------------------------------------------------


@router.post(Api.LOGIN, response_model=LoginResponse)
def login(body: LoginRequest, request: Request, db: DBSession = Depends(get_db)):
    ip = client_ip(request)
    user = db.scalars(select(User).where(User.username == body.username)).first()

    if user is None or not verify_password(body.password, user.password_hash):
        _audit(db, user.id if user else None, "login_failed", body.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": ErrorCode.BAD_CREDENTIALS, "detail": "用户名或密码错误"},
        )

    try:
        ensure_user_usable(user)
        ensure_device_allowed(db, user, body.device_id, body.device_name)
    except CanoeError as exc:
        _audit(db, user.id, "login_rejected", f"{exc.code}: {exc.message}", ip)
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "detail": exc.message}
        ) from exc

    raw_token, token_row = issue_login_token(db, user, body.device_id)
    user.last_login_at = utcnow()
    db.commit()

    _audit(db, user.id, "login", body.device_name or body.device_id, ip)

    return LoginResponse(
        token=raw_token,
        expires_in=max(0, int((as_aware(token_row.expire_at) - utcnow()).total_seconds())),
        sub_key=token_row.sub_key,
        user=UserInfo(
            id=user.id,
            username=user.username,
            status=user.status,
            expire_at=epoch(user.expire_at),
            # 客户端主界面要在点启航之前就显示节点名，所以登录时就得给
            node_name=node_name_for(db, user),
        ),
    )


# --------------------------------------------------------------------------
# POST /api/logout
# --------------------------------------------------------------------------


@router.post(Api.LOGOUT)
def logout(
    request: Request,
    body: LogoutRequest | None = None,
    creds: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    db: DBSession = Depends(get_db),
):
    """吊销当前令牌。body.all_devices=true 时吊销该用户全部令牌并结束所有会话。

    幂等：没有令牌 / 令牌已失效都返回 ok，避免客户端卡在登出流程里。
    """
    if creds is None or not creds.credentials:
        return {"ok": True}

    row = db.scalars(select(Token).where(Token.token_hash == token_hash(creds.credentials))).first()
    if row is None:
        return {"ok": True}

    all_devices = bool(body and body.all_devices)

    if all_devices:
        revoke_user_sessions(db, row.user_id)
        revoke_user_tokens(db, row.user_id)
    else:
        if body is not None and body.session_id:
            user = db.get(User, row.user_id)
            if user is not None:
                stop_session(db, body.session_id, user)
        revoke_login_token(db, creds.credentials)

    _audit(db, row.user_id, "logout", "all_devices" if all_devices else "", client_ip(request))
    return {"ok": True}


# --------------------------------------------------------------------------
# GET /api/me
# --------------------------------------------------------------------------


@router.get(Api.ME, response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    rows = db.scalars(
        select(Token)
        .where(Token.user_id == user.id, Token.revoked.is_(False))
        .order_by(Token.created_at.desc())
    ).all()

    seen: dict[str, DeviceInfo] = {}
    for r in rows:
        if r.device_id and r.device_id not in seen:
            seen[r.device_id] = DeviceInfo(
                device_id=r.device_id, name="", last_seen_at=epoch(r.created_at) or 0
            )

    return MeResponse(
        id=user.id,
        username=user.username,
        status=user.status,
        expire_at=epoch(user.expire_at),
        node_name=node_name_for(db, user),
        devices=list(seen.values()),
    )
