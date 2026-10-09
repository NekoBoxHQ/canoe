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

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from canoe_core import (
    Api,
    ConfigResponse,
    ErrorCode,
    HealthResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    PROTOCOL_VERSION,
    VERSION,
)

from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_user
from ..models import AuditLog, Node, User, epoch
from ..services.nodes import get_config_version, pick_node, to_entry_payload
from ..services.sessions import CanoeError, heartbeat as svc_heartbeat, start_session

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
