"""客户端接口：/api/config、/api/subscription、/api/heartbeat、/api/events

★★ 节点信息只有一条出口：/api/subscription 的加密信封 ★★

    · /api/config 只建会话，不下发任何节点信息；
    · /api/subscription 回的是密文，客户端拿登录时那把 sub_key 才解得开；
    · 服务端随时可以回空信封 —— 客户端据此销毁本地订阅。

    也就是说"客户端能不能用"完全由服务端说了算，而且订阅内容在
    传输链路的中间环节（反向代理、日志）上也是密文。
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DBSession

from canoe_core import (
    Api,
    ClientReleaseResponse,
    ConfigResponse,
    ErrorCode,
    HealthResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    PROTOCOL_VERSION,
    SubscriptionResponse,
    VERSION,
)

from ..config import settings
from ..database import SessionLocal, get_db
from ..deps import (
    authenticate,
    bearer_token,
    client_ip,
    current_sub_key,
    get_current_user,
)
from ..models import AuditLog, User, epoch
from ..services.broadcast import hub
from ..services.sessions import CanoeError, heartbeat as svc_heartbeat, start_session
from ..services.updates import (
    latest_release,
    subscription_for,
    subscription_revision,
    to_release_payload,
)

router = APIRouter(tags=["client"])


def _raise(exc: CanoeError) -> None:
    raise HTTPException(
        status_code=exc.http_status, detail={"code": exc.code, "detail": exc.message}
    )


@router.get(Api.HEALTH, response_model=HealthResponse)
def health():
    return HealthResponse(ok=True, app=settings.app_name, version=VERSION, protocol=PROTOCOL_VERSION)


# --------------------------------------------------------------------------
# GET /api/config   —— 需求：拉取配置（入口地址 + token + 节点名）
# --------------------------------------------------------------------------


@router.get(Api.CONFIG, response_model=ConfigResponse)
def get_config(
    request: Request,
    device_id: str,
    mode: str = "system_proxy",
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """启航：建一条会话。

    **不下发任何节点信息** —— 节点由客户端从自己的订阅里挑。
    这里只记"这人上线了"，供管理端看在线的人和踢下线。
    """
    ip = client_ip(request)
    try:
        row = start_session(db, user, device_id, mode, ip)
    except CanoeError as exc:
        _raise(exc)
        raise  # pragma: no cover

    db.add(
        AuditLog(user_id=user.id, action="session_started", detail=f"mode={mode}", ip=ip)
    )
    db.commit()

    resp = ConfigResponse(
        protocol=PROTOCOL_VERSION,
        session_id=row.id,
        node_name=None,
        expires_at=epoch(row.expire_at) or 0,
        heartbeat_interval=settings.heartbeat_interval,
        revision=subscription_revision(user, user.subscription or ""),
    )
    # 出网前的最后一道自检：万一将来有人改了模型，这里会直接 500 而不是静默泄漏
    resp.assert_no_real_fields()
    return resp


# --------------------------------------------------------------------------
# POST /api/session/stop   —— 靠岸：结束会话，保留登录令牌
# --------------------------------------------------------------------------


@router.post(Api.SESSION_STOP)
def session_stop(
    body: HeartbeatRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """点「靠岸」时调用。

    只结束这次代理会话（sessions.ended_at），**不动登录令牌** ——
    否则用户每次靠岸都得重新登舟。真正的登出走 /api/logout。
    """
    from ..services.sessions import stop_session

    stop_session(db, body.session_id, user)
    return {"ok": True}


# --------------------------------------------------------------------------
# POST /api/heartbeat
# --------------------------------------------------------------------------


@router.post(Api.HEARTBEAT, response_model=HeartbeatResponse)
def heartbeat(
    body: HeartbeatRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    try:
        row = svc_heartbeat(db, body.session_id, user)
    except CanoeError as exc:
        # 被吊销/封禁 -> 客户端收到 403 后必须立即靠岸
        _raise(exc)
        raise  # pragma: no cover

    # 心跳顺便把订阅指纹带回去：管理员改了或清空了订阅，
    # 在航的客户端最迟下一次心跳就会发现并重新拉（拉了是空就靠岸）。
    return HeartbeatResponse(
        ok=True,
        expires_at=epoch(row.expire_at) or 0,
        revision=subscription_revision(user, user.subscription or ""),
        revoked=row.revoked,
        node_name=None,
    )


# --------------------------------------------------------------------------
# GET /api/client/latest   —— 「更新」按钮之一：客户端更新
# --------------------------------------------------------------------------


def _public_base(request: Request) -> str:
    """安装包下载地址的前缀。

    配了 public_base_url 就用配置（生产推荐 —— 免得被 Host 头带偏），
    否则按请求现算（本地调试不用配）。
    """
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get(Api.CLIENT_LATEST, response_model=ClientReleaseResponse)
def client_latest(request: Request, db: DBSession = Depends(get_db)):
    """最新客户端版本。

    **不要求登录** —— 客户端得先能检查更新，才谈得上登舟。
    所以响应里只有版本号和安装包元信息，没有半点与账号相关的东西。
    """
    release = latest_release(db)
    if release is None:
        raise HTTPException(
            status_code=404,
            detail={"code": ErrorCode.NOT_FOUND, "detail": "服务端还没有发布任何客户端版本"},
        )
    return to_release_payload(release, _public_base(request))


# --------------------------------------------------------------------------
# GET /api/subscription   —— 「更新」按钮之二：订阅更新
# --------------------------------------------------------------------------


@router.get(Api.SUBSCRIPTION, response_model=SubscriptionResponse)
def subscription(
    user: User = Depends(get_current_user),
    sub_key: str = Depends(current_sub_key),
    db: DBSession = Depends(get_db),
):
    """我的订阅 —— 内容是加密的。

    客户端用登录时拿到的 sub_key 在内存里解开。服务端不给的时候
    （封禁 / 到期 / 订阅栏被清空）回空信封，客户端据此销毁本地订阅。

    「更新」按钮走的就是这里：每次都要带令牌证明身份，没有令牌
    什么也拿不到。
    """
    return subscription_for(db, user, sub_key)


# --------------------------------------------------------------------------
# GET /api/events   —— 推送（SSE 长连接）
# --------------------------------------------------------------------------


def _sse_frame(event: dict) -> str:
    """一条 SSE 帧。注释帧（: 开头）只用来保活，不带数据。"""
    if event.get("type") == "ping":
        return ": ping\n\n"
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _hello_payload(user_id: int) -> dict:
    """连上就发的第一条，让客户端立刻和当前配置对齐。

    这里自己开一个短会话读完就关 —— 不能用 Depends(get_db)，
    否则这条会话会被这条长连接一直占着。
    """
    with SessionLocal() as db:
        user = db.get(User, user_id)
        # hello 里**不带**任何订阅内容 —— 只给个指纹，客户端自己去拉。
        # 推送是广播的，塞内容就等于把别人的订阅发给所有人。
        return {
            "revision": subscription_revision(user, user.subscription or "") if user else "",
            "server_time": int(time.time()),
        }


@router.get(Api.EVENTS)
async def events(request: Request):
    """SSE 长连接。客户端启航后挂着它收推送。

    鉴权走 Authorization 头（客户端是 Python 不是浏览器，能设头）。
    这里**不用 Depends(get_current_user)** —— 它依赖的 get_db 要等响应结束
    才回收，一条挂几小时的长连接会把数据库会话池耗干。
    """
    raw = bearer_token(request)
    with SessionLocal() as db:
        user = authenticate(db, raw)      # 令牌无效/被封禁/已到期都会在这里抛
        user_id = user.id

    ok, why = hub.can_accept(user_id)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail={"code": ErrorCode.INTERNAL, "detail": why},
        )

    hello = _hello_payload(user_id)

    async def generate():
        try:
            async for event in hub.stream(user_id, hello):
                yield _sse_frame(event)
        except asyncio.CancelledError:
            # 客户端断开 / 服务端关闭，属正常路径，交给 finally 清理
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # 让 Nginx 不要缓冲这条流，否则推送会攒到最后一起发
            "X-Accel-Buffering": "no",
        },
    )
