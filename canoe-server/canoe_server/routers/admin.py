"""管理后台：用户管理、节点管理、在线会话、统计、中转层配置。

这里是**唯一**允许返回 real_* 字段的地方，全部需要 role=admin。
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DBSession

from canoe_core import Api

from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_admin
from ..models import AuditLog, ClientRelease, Node, Session as SessionRow, User, epoch, utcnow
from ..security import gen_entry_uuid, hash_password
from ..services.broadcast import hub, notify_config_changed, notify_kick, notify_release
from ..services.nodes import bump_config_version, get_config_version, to_admin_payload
from ..services.relay import dump_singbox, render_nginx, render_singbox
from ..services.sessions import revoke_user_sessions, revoke_user_tokens
from ..services.updates import (
    latest_release,
    list_releases,
    publish_release,
    release_dir,
    safe_filename,
    sha256_file,
    to_release_payload,
)

# 不设 prefix：Api.* 常量里已经是完整路径（/api/admin/...），
# 再加 prefix 会拼成 /api/admin/api/admin/...
router = APIRouter(tags=["admin"])


# ==========================================================================
# 推送辅助
# ==========================================================================


def _broadcast_config(db: DBSession) -> int:
    """节点/绑定变更后通知在线客户端。

    广播里**不带节点名** —— 每个用户被分配到的节点不一样，带了就会把
    别人的节点泄露给所有人。只说"配置变了"，客户端自己回头拉自己的订阅。
    """
    return notify_config_changed(get_config_version(db))


# ==========================================================================
# 请求模型
# ==========================================================================


class NodeUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    remark: str = ""
    enabled: bool = True
    sort_order: int = 100

    # 中转入口（客户端可见）
    entry_host: str = ""
    entry_port: int = 443
    entry_uuid: str = ""      # 留空自动生成
    entry_path: str = ""
    entry_sni: str = ""
    entry_transport: str = "ws"
    entry_tls: bool = True
    entry_insecure: bool = False

    # 真实节点（绝不下发）
    real_protocol: str = "vless"
    real_host: str = ""
    real_port: int = 443
    real_uuid: str = ""
    real_flow: str = ""
    real_tls: bool = True
    real_sni: str = ""
    real_fingerprint: str = "chrome"
    real_network: str = "tcp"
    real_ws_path: str = ""
    real_ws_host: str = ""
    real_grpc_service: str = ""
    real_insecure: bool = False
    real_extra: dict = {}


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)
    expire_days: int | None = None
    max_devices: int | None = None
    remark: str = ""


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    expire_at: int | None = None      # 0 = 永不过期
    max_devices: int | None = None
    remark: str | None = None
    role: str | None = None


class BindNodes(BaseModel):
    node_ids: list[int] = []


# ==========================================================================
# 用户
# ==========================================================================


def _user_view(db: DBSession, user: User) -> dict:
    online = any(
        s.online for s in db.scalars(select(SessionRow).where(SessionRow.user_id == user.id)).all()
    )
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "status": user.status,
        "expire_at": epoch(user.expire_at),
        "max_devices": user.max_devices,
        "remark": user.remark,
        "created_at": epoch(user.created_at) or 0,
        "last_login_at": epoch(user.last_login_at),
        "online": online,
    }


@router.get(Api.ADMIN_USERS)
def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    q: str | None = None,
    _: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    stmt = select(User)
    if q:
        stmt = stmt.where(or_(User.username.contains(q), User.remark.contains(q)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    users = db.scalars(stmt.order_by(User.id).offset((page - 1) * size).limit(size)).all()
    return {"total": total, "page": page, "size": size, "items": [_user_view(db, u) for u in users]}


@router.post(Api.ADMIN_USERS, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise HTTPException(409, {"code": "username_taken", "detail": "用户名已存在"})

    days = body.expire_days if body.expire_days is not None else settings.default_expire_days
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role="user",
        status="active",
        max_devices=body.max_devices or settings.default_max_devices,
        remark=body.remark,
        expire_at=utcnow() + timedelta(days=days) if days else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(AuditLog(user_id=admin.id, action="user_create", detail=user.username))
    db.commit()
    return {"id": user.id, "username": user.username}


@router.patch(Api.ADMIN_USERS + "/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, {"code": "not_found", "detail": "用户不存在"})

    if body.password:
        user.password_hash = hash_password(body.password)
    if body.expire_at is not None:
        user.expire_at = (
            None if body.expire_at == 0 else datetime.fromtimestamp(body.expire_at, tz=timezone.utc)
        )
    if body.max_devices is not None:
        user.max_devices = body.max_devices
    if body.remark is not None:
        user.remark = body.remark
    if body.role in {"user", "admin"}:
        user.role = body.role

    db.commit()
    db.add(AuditLog(user_id=admin.id, action="user_update", detail=user.username))
    db.commit()
    return {"ok": True}


@router.post(Api.ADMIN_USERS + "/{user_id}/ban")
def ban_user(
    user_id: int,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, {"code": "not_found", "detail": "用户不存在"})
    if user.role == "admin":
        raise HTTPException(400, {"code": "cannot_ban_admin", "detail": "不能封禁管理员"})

    user.status = "banned"
    db.commit()
    # 封禁必须立即生效：令牌失效 + 会话吊销，客户端下一次请求就会被踢回登录页
    tokens = revoke_user_tokens(db, user_id)
    sessions = revoke_user_sessions(db, user_id)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="ban",
            detail=f"{user.username} tokens={tokens} sessions={sessions}",
            ip=client_ip(request),
        )
    )
    db.commit()
    pushed = notify_kick(user_id, "账号已被封禁", permanent=True)
    return {
        "ok": True,
        "revoked_tokens": tokens,
        "revoked_sessions": sessions,
        "pushed": pushed,
    }


@router.post(Api.ADMIN_USERS + "/{user_id}/unban")
def unban_user(
    user_id: int, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, {"code": "not_found", "detail": "用户不存在"})
    user.status = "active"
    db.commit()
    db.add(AuditLog(user_id=admin.id, action="unban", detail=user.username))
    db.commit()
    return {"ok": True}


@router.delete(Api.ADMIN_USERS + "/{user_id}")
def delete_user(
    user_id: int, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, {"code": "not_found", "detail": "用户不存在"})
    if user.id == admin.id:
        raise HTTPException(400, {"code": "cannot_delete_self", "detail": "不能删除自己"})

    revoke_user_tokens(db, user_id)
    revoke_user_sessions(db, user_id)
    db.delete(user)
    db.commit()
    db.add(AuditLog(user_id=admin.id, action="user_delete", detail=str(user_id)))
    db.commit()
    return {"ok": True}


# ==========================================================================
# 节点
# ==========================================================================


def _apply_node(node: Node, body: NodeUpsert) -> Node:
    node.name = body.name
    node.remark = body.remark
    node.enabled = body.enabled
    node.sort_order = body.sort_order

    node.entry_host = body.entry_host
    node.entry_port = body.entry_port
    node.entry_uuid = body.entry_uuid or node.entry_uuid or gen_entry_uuid()
    node.entry_path = body.entry_path
    node.entry_sni = body.entry_sni or body.entry_host
    node.entry_transport = body.entry_transport
    node.entry_tls = body.entry_tls
    node.entry_insecure = body.entry_insecure

    node.real_protocol = body.real_protocol
    node.real_host = body.real_host
    node.real_port = body.real_port
    node.real_uuid = body.real_uuid
    node.real_flow = body.real_flow
    node.real_tls = body.real_tls
    node.real_sni = body.real_sni
    node.real_fingerprint = body.real_fingerprint
    node.real_network = body.real_network
    node.real_ws_path = body.real_ws_path
    node.real_ws_host = body.real_ws_host
    node.real_grpc_service = body.real_grpc_service
    node.real_insecure = body.real_insecure
    node.real_extra = body.real_extra
    return node


@router.get(Api.ADMIN_NODES)
def list_nodes(_: User = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    nodes = db.scalars(select(Node).order_by(Node.sort_order, Node.id)).all()
    return {"items": [to_admin_payload(n) for n in nodes]}


@router.post(Api.ADMIN_NODES, status_code=status.HTTP_201_CREATED)
def create_node(
    body: NodeUpsert, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    node = Node(entry_uuid=gen_entry_uuid())
    _apply_node(node, body)
    db.add(node)
    db.commit()
    db.refresh(node)

    if not node.entry_path:
        node.entry_path = f"/e/n{node.id}"
        db.commit()

    version = bump_config_version(db)
    db.add(AuditLog(user_id=admin.id, action="node_create", detail=node.name))
    db.commit()
    pushed = _broadcast_config(db)
    return {"id": node.id, "config_version": version, "pushed": pushed}


@router.patch(Api.ADMIN_NODES + "/{node_id}")
def update_node(
    node_id: int,
    body: NodeUpsert,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, {"code": "not_found", "detail": "节点不存在"})
    _apply_node(node, body)
    db.commit()
    version = bump_config_version(db)
    db.add(AuditLog(user_id=admin.id, action="node_update", detail=node.name))
    db.commit()
    pushed = _broadcast_config(db)
    return {"ok": True, "config_version": version, "pushed": pushed}


@router.delete(Api.ADMIN_NODES + "/{node_id}")
def delete_node(
    node_id: int, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, {"code": "not_found", "detail": "节点不存在"})
    db.delete(node)
    db.commit()
    bump_config_version(db)
    db.add(AuditLog(user_id=admin.id, action="node_delete", detail=str(node_id)))
    db.commit()
    pushed = _broadcast_config(db)
    return {"ok": True, "pushed": pushed}


@router.post(Api.ADMIN_NODES + "/{node_id}/bind")
def bind_node(
    node_id: int,
    body: BindNodes,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """把节点绑定给一批用户（写 user_node 表）。"""
    from ..models import UserNode

    if db.get(Node, node_id) is None:
        raise HTTPException(404, {"code": "not_found", "detail": "节点不存在"})

    existing = set(
        db.scalars(select(UserNode.user_id).where(UserNode.node_id == node_id)).all()
    )
    added = 0
    for uid in body.node_ids:
        if uid in existing or db.get(User, uid) is None:
            continue
        db.add(UserNode(user_id=uid, node_id=node_id))
        added += 1
    db.commit()
    pushed = _broadcast_config(db) if added else 0
    return {"ok": True, "added": added, "pushed": pushed}


# ==========================================================================
# 会话 / 统计
# ==========================================================================


@router.get(Api.ADMIN_SESSIONS)
def list_sessions(
    online: bool = True,
    limit: int = Query(200, ge=1, le=1000),
    _: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    rows = db.scalars(
        select(SessionRow).order_by(SessionRow.last_seen.desc()).limit(limit)
    ).all()

    items = []
    for r in rows:
        is_on = r.online
        if online and not is_on:
            continue
        user = db.get(User, r.user_id)
        node = db.get(Node, r.node_id)
        items.append(
            {
                "session_id": r.id,
                "user_id": r.user_id,
                "username": user.username if user else "?",
                "node_name": node.name if node else "?",
                "device_id": r.device_id,
                "client_ip": r.client_ip,
                "mode": r.mode,
                "online": is_on,
                "revoked": r.revoked,
                "created_at": epoch(r.created_at) or 0,
                "last_seen": epoch(r.last_seen) or 0,
            }
        )
    return {"items": items}


@router.delete(Api.ADMIN_SESSIONS + "/{session_id}")
def kick_session(
    session_id: str, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    row = db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(404, {"code": "not_found", "detail": "会话不存在"})
    row.revoked = True
    row.ended_at = utcnow()
    db.commit()
    db.add(AuditLog(user_id=admin.id, action="kick", detail=session_id))
    db.commit()
    # 推送一条 kick，客户端不用等下一次心跳就能立刻靠岸
    pushed = notify_kick(row.user_id, "管理员把你踢下线了")
    return {"ok": True, "pushed": pushed}


@router.get(Api.ADMIN_STATS)
def stats(_: User = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(User)) or 0
    active = db.scalar(select(func.count()).select_from(User).where(User.status == "active")) or 0
    n_total = db.scalar(select(func.count()).select_from(Node)) or 0
    n_enabled = db.scalar(select(func.count()).select_from(Node).where(Node.enabled.is_(True))) or 0
    sessions = db.scalars(select(SessionRow)).all()
    return {
        "users_total": total,
        "users_active": active,
        "users_banned": total - active,
        "nodes_total": n_total,
        "nodes_enabled": n_enabled,
        "sessions_total": len(sessions),
        "sessions_online": sum(1 for s in sessions if s.online),
        "config_version": get_config_version(db),
        # 推送连接数：排查"为什么客户端没收到推送"时先看这里
        "push": hub.stats(),
    }


# ==========================================================================
# 中转层
# ==========================================================================


@router.get(Api.ADMIN_RELAY_CONFIG)
def relay_config(
    fmt: str = Query("singbox", pattern="^(singbox|nginx)$"),
    _: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    if fmt == "nginx":
        return PlainTextResponse(render_nginx(db), media_type="text/plain; charset=utf-8")
    return render_singbox(db)


@router.post(Api.ADMIN_RELAY_RELOAD)
def relay_reload(admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    text = dump_singbox(db, settings.relay_config_out)

    hook = settings.relay_reload_hook
    if not hook:
        return {"ok": True, "reloaded": False, "hint": "未配置 RELAY_RELOAD_HOOK", "config": text}

    proc = subprocess.run(hook, shell=True, capture_output=True, text=True, timeout=30)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="relay_reload",
            detail=f"rc={proc.returncode} {proc.stderr[:200]}",
        )
    )
    db.commit()
    return {
        "ok": proc.returncode == 0,
        "reloaded": True,
        "returncode": proc.returncode,
        "stderr": proc.stderr[-2000:],
    }


# ==========================================================================
# 客户端版本发布（「客户端更新」的数据源）
# ==========================================================================


class ReleasePublish(BaseModel):
    version: str = Field(min_length=1, max_length=32)
    notes: str = ""
    min_version: str = ""


def _release_view(row: ClientRelease) -> dict:
    return {
        "id": row.id,
        "version": row.version,
        "filename": row.filename,
        "size": row.size,
        "sha256": row.sha256,
        "notes": row.notes,
        "min_version": row.min_version,
        "enabled": row.enabled,
        "published_at": epoch(row.published_at) or 0,
    }


@router.get(Api.ADMIN_RELEASES)
def admin_list_releases(
    _: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    items = [_release_view(r) for r in list_releases(db)]
    current = latest_release(db)
    return {
        "items": items,
        "latest": current.version if current else None,
        "download_prefix": Api.DOWNLOAD_PREFIX,
    }


@router.post(Api.ADMIN_RELEASES, status_code=status.HTTP_201_CREATED)
def admin_publish_release(
    body: ReleasePublish,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """只登记版本信息 —— 安装包你自己已经放进 releases/ 了。"""
    row = publish_release(
        db, version=body.version, notes=body.notes, min_version=body.min_version
    )
    db.add(AuditLog(user_id=admin.id, action="release_publish", detail=row.version))
    db.commit()
    pushed = notify_release(row.version, notes=row.notes)
    return {**_release_view(row), "pushed": pushed}


@router.post(Api.ADMIN_RELEASES + "/upload", status_code=status.HTTP_201_CREATED)
def admin_upload_release(
    version: str = Form(..., min_length=1, max_length=32),
    notes: str = Form(""),
    min_version: str = Form(""),
    file: UploadFile = File(...),
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """上传安装包并发布。

    包落到 releases/ 下，由 StaticFiles 对外提供下载；
    /api/client/latest 会把地址拼成 <站点>/downloads/<文件名>。
    """
    filename = safe_filename(file.filename or "Canoe.zip")
    dest = release_dir() / filename
    limit = settings.max_release_mb * 1024 * 1024

    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        413,
                        {
                            "code": "too_large",
                            "detail": f"安装包超过 {settings.max_release_mb} MB 上限",
                        },
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)     # 别把半个包留在磁盘上
        raise
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, {"code": "io_error", "detail": f"写文件失败：{exc}"}) from exc
    finally:
        file.file.close()

    digest = sha256_file(dest)
    row = publish_release(
        db,
        version=version,
        filename=filename,
        size=size,
        sha256=digest,
        notes=notes,
        min_version=min_version,
    )
    db.add(
        AuditLog(user_id=admin.id, action="release_upload", detail=f"{version} {filename} {size}B")
    )
    db.commit()
    pushed = notify_release(row.version, notes=row.notes)
    return {**_release_view(row), "pushed": pushed}


@router.delete(Api.ADMIN_RELEASES + "/{release_id}")
def admin_delete_release(
    release_id: int,
    delete_file: bool = False,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """撤下一个版本。默认只删记录（安装包留着），delete_file=true 连包一起删。"""
    row = db.get(ClientRelease, release_id)
    if row is None:
        raise HTTPException(404, {"code": "not_found", "detail": "版本不存在"})

    version, filename = row.version, row.filename
    if delete_file and filename:
        (release_dir() / filename).unlink(missing_ok=True)
    db.delete(row)
    db.commit()
    db.add(AuditLog(user_id=admin.id, action="release_delete", detail=version))
    db.commit()
    return {"ok": True, "version": version, "file_deleted": bool(delete_file and filename)}


@router.get(Api.ADMIN_RELEASES + "/latest-preview")
def admin_latest_preview(
    request: Request,
    _: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """预览客户端调 /api/client/latest 会拿到什么 —— 发布完不用真去装一次才知道。"""
    row = latest_release(db)
    if row is None:
        return {"latest": None, "hint": "还没有发布任何版本"}
    base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    return to_release_payload(row, base).model_dump()
