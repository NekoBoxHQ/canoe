"""FastAPI 依赖：令牌鉴权、当前用户、管理员校验。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from canoe_core import ErrorCode

from .config import settings
from .database import get_db
from .models import Token, User
from .security import decrypt_sub_key, token_hash

bearer = HTTPBearer(auto_error=False)

# 统一用 {"code": ..., "detail": ...} 的响应体，与 canoe_core.ApiError 对齐
def _err(http_status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "detail": detail})


UNAUTHORIZED = _err(status.HTTP_401_UNAUTHORIZED, ErrorCode.UNAUTHORIZED, "令牌无效或已过期")


def authenticate(db: DBSession, raw_token: str | None) -> User:
    """把原始 Bearer 令牌换成用户，顺带做完所有账号级校验。

    单独抽出来是因为 SSE 那条长连接**不能用 Depends(get_db)** ——
    FastAPI 的 yield 依赖要等响应结束才回收，一条挂几小时的长连接
    就会一直占着一个数据库会话，把连接池耗干。
    长连接那边自己开一个短会话调这个函数即可。
    """
    if not raw_token:
        raise UNAUTHORIZED

    record = db.scalars(
        select(Token).where(Token.token_hash == token_hash(raw_token))
    ).first()

    # 顺序很重要：先查令牌是否存在，再看账号状态，最后才判令牌有效性。
    #
    # 因为封禁用户时我们会顺手吊销其令牌，如果先判 is_valid 就直接返回 401，
    # 用户只会看到"登录已失效"，看不到"账号已被封禁"这个真正的原因。
    # 把账号状态放在前面，安全性和之前完全一样（令牌照样是废的），
    # 但客户端能给出准确的提示。
    if record is None:
        raise UNAUTHORIZED

    user = db.get(User, record.user_id)
    if user is None:
        raise UNAUTHORIZED

    if user.status == "banned":
        raise _err(status.HTTP_403_FORBIDDEN, ErrorCode.BANNED, "账号已被封禁")

    if user.is_expired:
        raise _err(status.HTTP_403_FORBIDDEN, ErrorCode.EXPIRED, "账号已到期")

    if not record.is_valid:
        raise UNAUTHORIZED

    # 顺手记一下最后活跃时间，管理后台要用
    from .models import utcnow

    user.last_login_at = user.last_login_at or utcnow()
    return user


def bearer_token(request: Request) -> str | None:
    """从 Authorization 头里取原始令牌。"""
    raw = request.headers.get("authorization") or ""
    if not raw.lower().startswith("bearer "):
        return None
    return raw[7:].strip() or None


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: DBSession = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise UNAUTHORIZED
    return authenticate(db, creds.credentials)


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise _err(status.HTTP_403_FORBIDDEN, ErrorCode.UNAUTHORIZED, "需要管理员权限")
    return user


def current_sub_key(
    request: Request,
    db: DBSession = Depends(get_db),
) -> str:
    """当前会话的订阅密钥。

    从令牌行里取 —— 令牌和密钥是**同一个生命周期**：令牌废了，
    密钥也就取不到了，客户端拉回来的订阅解不开。

    取不到就返回空串，由调用方决定怎么办（订阅接口会回空信封，
    客户端据此销毁本地订阅，而不是报错重试）。
    """
    raw = bearer_token(request)
    if not raw:
        return ""
    row = db.scalars(select(Token).where(Token.token_hash == token_hash(raw))).first()
    if row is None or not row.is_valid:
        return ""
    if row.sub_key_enc:
        return decrypt_sub_key(row.sub_key_enc)
    # 迁移前的老行（正常路径下 init_db 已经改写过了）
    return decrypt_sub_key(row.sub_key)


def client_ip(request: Request) -> str:
    """请求来源 IP。**默认只认真实对端，不认 X-Forwarded-For。**

    这条改过：原来无条件读 XFF 的第一段。本部署是 uvicorn 直接对公网、
    前面没有反代，那个头完全是客户端说了算 —— 于是审计日志里的 IP 想写
    什么写什么，按 IP 的登录/注册限速也能"一个请求换一个假 IP"绕过去。

    放到 Nginx 后面时再打开 trust_proxy（那时只有反代能连到本进程）。
    """
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""
