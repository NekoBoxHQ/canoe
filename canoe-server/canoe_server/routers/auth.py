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
from ..security import dummy_verify, hash_password, token_hash, verify_password
from ..services.ratelimit import (
    ip_key,
    login_failed,
    login_ok,
    login_retry_after,
    register_limiter,
)
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
    # 注册不要任何凭据，所以它是"谁能建号"的唯一闸门。按 IP 限速 ——
    # 放在最前面，连查重和 PBKDF2 都别跑，省得被拿来当 CPU 开关。
    ip = client_ip(request)
    wait = register_limiter.retry_after(ip_key(ip))
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": ErrorCode.RATE_LIMITED,
                "detail": f"注册太频繁了，请 {wait} 秒后再试",
            },
        )

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

    # 记一次（记的是**成功**的，不是失败的）—— 窗口内建满就歇着
    register_limiter.hit(ip_key(ip))

    _audit(db, user.id, "register", user.username, ip)
    return RegisterResponse(
        id=user.id, username=user.username, created_at=epoch(user.created_at) or 0
    )


# --------------------------------------------------------------------------
# POST /api/login
# --------------------------------------------------------------------------


@router.post(Api.LOGIN, response_model=LoginResponse)
def login(body: LoginRequest, request: Request, db: DBSession = Depends(get_db)):
    ip = client_ip(request)

    # ★ 限速查在**跑 PBKDF2 之前**。放到校验后面就等于没限 —— 攻击者的
    #   每次尝试照样要服务端烧 24 万次哈希，这里正是要挡的就是这个。
    wait = login_retry_after(body.username, ip)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": ErrorCode.RATE_LIMITED,
                "detail": f"尝试太频繁了，请 {wait} 秒后再试",
            },
        )

    user = db.scalars(select(User).where(User.username == body.username)).first()

    if user is None:
        # 用户名不存在也走一遍同样开销的校验：否则"查得到的人慢、查不到的人快"，
        # 光看响应时间就能把有效用户名枚举出来（错误文案统一也挡不住）。
        dummy_verify(body.password)
        login_failed(body.username, ip)
        _audit(db, None, "login_failed", body.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": ErrorCode.BAD_CREDENTIALS, "detail": "用户名或密码错误"},
        )

    if not verify_password(body.password, user.password_hash):
        login_failed(body.username, ip)
        _audit(db, user.id, "login_failed", body.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": ErrorCode.BAD_CREDENTIALS, "detail": "用户名或密码错误"},
        )

    # 验过了就清零 —— 正常用户偶尔手滑几次不该被累积到锁住
    login_ok(body.username, ip)

    try:
        ensure_user_usable(user)
        ensure_device_allowed(db, user, body.device_id, body.device_name)
    except CanoeError as exc:
        _audit(db, user.id, "login_rejected", f"{exc.code}: {exc.message}", ip)
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "detail": exc.message}
        ) from exc

    raw_token, token_row, sub_key = issue_login_token(db, user, body.device_id)
    user.last_login_at = utcnow()
    db.commit()

    _audit(db, user.id, "login", body.device_name or body.device_id, ip)

    return LoginResponse(
        token=raw_token,
        expires_in=max(0, int((as_aware(token_row.expire_at) - utcnow()).total_seconds())),
        # ★ 明文 sub_key 只在这次响应里出现一次；库里存的是密文，
        #   所以这里不能用 token_row.sub_key（那已经是空的了）。
        sub_key=sub_key,
        user=UserInfo(
            id=user.id,
            username=user.username,
            # ★ 面板登录后要靠它判断能不能进 —— 漏了面板就永远进不去
            role=user.role,
            status=user.status,
            expire_at=epoch(user.expire_at),
            # 客户端主界面要在点启航之前就显示节点名，所以登录时就得给
            node_name=None,
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
        # ★ 面板刷新页面后用这个重新校验身份，同样不能漏
        role=user.role,
        status=user.status,
        expire_at=epoch(user.expire_at),
        node_name=None,
        devices=list(seen.values()),
    )
