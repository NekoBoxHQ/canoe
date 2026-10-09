"""客户端接口：/api/config、/api/heartbeat、/api/health

★★ /api/config 是整条安全链路上最关键的一个端点 ★★

    它的响应类型是 canoe_core.ConfigResponse，里面只有：
        node_name     节点显示名
        entry         EntryPayload（中转层入口，白名单字段）
        token         短期入口凭证
    没有任何真实节点的地址/端口/协议/密钥。

    需求原文："/api/config 返回里不能包含真实节点地址/端口/密码"。
    这条要求由 ConfigResponse 的类型定义保证，而不是靠这里的代码自觉。
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
from ..deps import authenticate, bearer_token, client_ip, get_current_user
from ..models import AuditLog, Node, User, epoch
from ..services.broadcast import hub
from ..services.nodes import get_config_version, pick_node, to_entry_payload
from ..services.sessions import CanoeError, heartbeat as svc_heartbeat, start_session
from ..services.updates import (
    latest_release,
    revision_of,
    subscription_for,
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
    node_id: int | None = None,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """下发入口配置。

    客户端登录后自动调用一次；心跳发现 config_version 变化时会重新调用。
    """
    ip = client_ip(request)
    try:
        node = pick_node(db, user, node_id)
    except CanoeError as exc:
        _raise(exc)
        raise  # pragma: no cover

    if node is None:
        raise HTTPException(
            status_code=503,
            detail={"code": ErrorCode.NO_NODE, "detail": "当前没有可用节点，请联系管理员"},
        )

    try:
        row, ticket = start_session(db, user, node.id, device_id, mode, ip)
    except CanoeError as exc:
        _raise(exc)
        raise  # pragma: no cover

    db.add(
        AuditLog(
            user_id=user.id,
            action="config_issued",
            detail=f"node={node.name} mode={mode}",
            ip=ip,
        )
    )
    db.commit()

    resp = ConfigResponse(
        protocol=PROTOCOL_VERSION,
        session_id=row.id,
        node_name=node.name,               # 客户端唯一能看到的节点信息
        token=ticket,                      # 短期入口凭证
        expires_at=epoch(row.expire_at) or 0,
        heartbeat_interval=settings.heartbeat_interval,
        config_version=get_config_version(db),
        entry=to_entry_payload(node),      # ← 白名单：只有 entry_* 字段
    )
    # 出网前的最后一道自检：万一将来有人改了模型，这里会直接 500 而不是静默泄漏
    resp.assert_no_real_fields()
    resp.entry.assert_whitelisted()
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

    node = db.get(Node, row.node_id)
    return HeartbeatResponse(
        ok=True,
        expires_at=epoch(row.expire_at) or 0,
        config_version=get_config_version(db),
        revoked=row.revoked,
        node_name=node.name if node else None,
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
    db: DBSession = Depends(get_db),
):
    """我这条订阅现在长什么样、变了没有。

    和 /api/config 的区别：**不建会话、不发凭证**，只读。
    客户端「更新」按钮拿 revision 和本地记住的比一比就知道要不要更新。

    出于同样的白名单约束，这里也只回 EntryPayload，没有真实节点。
    """
    return subscription_for(db, user)


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
        node = pick_node(db, user) if user is not None else None
        entry = to_entry_payload(node) if node is not None else None
        version = get_config_version(db)
        return {
            "config_version": version,
            "revision": revision_of(version, node, entry),
            "node_name": node.name if node is not None else "",
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
