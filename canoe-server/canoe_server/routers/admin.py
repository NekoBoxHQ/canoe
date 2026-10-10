"""管理后台：用户管理、节点管理（含绑定）、在线会话、统计、发布。

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

from canoe_core import MAX_NODES_PER_USER, Api

from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_admin
from ..models import (
    AuditLog,
    ClientRelease,
    Node,
    Session as SessionRow,
    User,
    UserNode,
    epoch,
    utcnow,
)
from ..security import hash_password
from ..services.broadcast import hub, notify_config_changed, notify_kick, notify_release
from ..services.nodes import (
    bound_node_ids,
    count_nodes,
    set_bound_nodes,
    subscription_text_for,
    to_node_view,
    validate_link,
)
from ..services.sessions import revoke_user_sessions, revoke_user_tokens
from ..services.updates import (
    ReleaseMismatch,
    ReleaseSourceError,
    ReleaseTooLarge,
    latest_release,
    list_releases,
    publish_release,
    pull_from_github,
    release_dir,
    safe_filename,
    store_release_file,
    subscription_revision,
    to_release_payload,
)

# 不设 prefix：Api.* 常量里已经是完整路径（/api/admin/...），
# 再加 prefix 会拼成 /api/admin/api/admin/...
router = APIRouter(tags=["admin"])


# ==========================================================================
# 推送辅助
# ==========================================================================


def _broadcast_config(db: DBSession) -> int:
    """节点或绑定变了，通知在线客户端重新拉订阅。

    广播里**不带任何订阅内容** —— 那是群发，带上就等于把某个人的节点
    发给所有人。只说"变了"，客户端自己去拉自己那份（拉回来是空就靠岸）。
    """
    del db  # 已经不需要算出具体版本号了：客户端自己比对 revision
    return notify_config_changed(0)


# ==========================================================================
# 请求模型
# ==========================================================================


class NodeUpsert(BaseModel):
    """建/改一个节点 —— **核心就是一行链接**，其余都是给自己看的。

    链接里已经含着协议、地址、端口、密钥、名称了，不用再一个字段一个字段
    填（那是中转层时代的表单，那时服务端要自己渲染配置）。
    """

    #: 节点链接：ss:// vmess:// vless:// trojan://（一行，可带 #名称）
    link: str = ""
    #: 给自己看的备注。留空则列表里显示链接自带的名称。
    remark: str = ""
    #: 列表排序，小的在前
    sort_order: int = 100
    enabled: bool = True


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)
    expire_days: int | None = None
    max_devices: int | None = None
    remark: str = ""


class UserUpdate(BaseModel):
    """改一个账号。全部可选 —— 只改传上来的那几项。

    改用户名是允许的（管理员把自己改成别的名字也要行）：令牌按 user_id
    记，改完不用重新登录，只是下次 /api/me 显示新名字。
    """

    username: str | None = Field(
        default=None, min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"
    )
    password: str | None = Field(default=None, min_length=8, max_length=128)
    expire_at: int | None = None      # 0 = 永不过期
    max_devices: int | None = None
    remark: str | None = None
    role: str | None = None


class BindUserNodes(BaseModel):
    """把一个客户绑到哪些节点上。整体替换，不是追加。

    条数卡在 MAX_NODES_PER_USER —— 客户端底部就 6 个灯位，一个灯一个
    节点。放更多出去客户端也显示不出来，用户只会以为"我绑了 8 个怎么
    只亮 6 个"。超了直接 422，别静默截断。
    """

    node_ids: list[int] = Field(default_factory=list, max_length=MAX_NODES_PER_USER)


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
        #: 绑了哪些节点（面板上要显示勾选状态）
        "node_ids": bound_node_ids(db, user),
        #: 最终发给客户端的订阅有几行（就是绑定的节点数）
        "subscription_lines": len(_lines(subscription_text_for(db, user))),
        "created_at": epoch(user.created_at) or 0,
        "last_login_at": epoch(user.last_login_at),
        "online": online,
    }


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


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
    return {
        "total": total, "page": page, "size": size,
        "items": [_user_view(db, u) for u in users],
        # 面板拿它来限制勾选（勾满就不让再勾了）。写死在前端的话，
        # 这个数改了要改两处。
        "max_nodes_per_user": MAX_NODES_PER_USER,
    }


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

    changes: list[str] = []

    # 改名要查重 —— 不然两个同名账号在登录那里就成了"先到先得"，
    # 谁也别想弄清楚自己登的是哪一个。
    if body.username is not None and body.username != user.username:
        taken = db.scalars(
            select(User).where(User.username == body.username, User.id != user.id)
        ).first()
        if taken is not None:
            raise HTTPException(409, {"code": "username_taken", "detail": "用户名已存在"})
        changes.append(f"用户名 {user.username} → {body.username}")
        user.username = body.username

    if body.password:
        user.password_hash = hash_password(body.password)
        changes.append("改了密码")
    if body.expire_at is not None:
        user.expire_at = (
            None if body.expire_at == 0 else datetime.fromtimestamp(body.expire_at, tz=timezone.utc)
        )
        changes.append("改了到期时间")
    if body.max_devices is not None:
        user.max_devices = body.max_devices
    if body.remark is not None:
        user.remark = body.remark
    if body.role in {"user", "admin"}:
        user.role = body.role
        changes.append(f"角色 → {body.role}")

    db.commit()
    db.add(
        AuditLog(
            user_id=admin.id,
            action="user_update",
            detail=f"{user.username}：" + ("、".join(changes) or "无改动"),
        )
    )
    db.commit()
    return {"ok": True, "username": user.username, "changed": changes}


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
    """把请求里的字段写进节点行。

    名称优先用管理员填的备注；没填就用链接里 `#` 那段（比如「🌍日本家宽🌍」），
    再没有就退回 host:port —— 总之列表里得有东西可看。
    """
    link = (body.link or "").strip()
    node.link = link
    node.remark = body.remark or ""
    node.enabled = body.enabled
    node.sort_order = body.sort_order

    from ..services.nodes import link_summary

    info = link_summary(link)
    node.name = (body.remark or "").strip() or info["name"] or (
        f"{info['host']}:{info['port']}" if info["host"] else "未命名节点"
    )
    return node


@router.get(Api.ADMIN_NODES)
def list_nodes(_: User = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    nodes = db.scalars(select(Node).order_by(Node.sort_order, Node.id)).all()
    # 面板的「分配节点」弹窗要用这个数限制勾选，所以列表里一起带上。
    return {"items": [to_node_view(n) for n in nodes], "max_nodes_per_user": MAX_NODES_PER_USER}


@router.post(Api.ADMIN_NODES, status_code=status.HTTP_201_CREATED)
def create_node(
    body: NodeUpsert, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    problem = validate_link(body.link)
    if problem:
        raise HTTPException(400, {"code": "bad_link", "detail": problem})

    node = Node()
    _apply_node(node, body)
    db.add(node)
    db.commit()
    db.refresh(node)

    db.add(AuditLog(user_id=admin.id, action="node_create", detail=node.name))
    db.commit()
    pushed = _broadcast_config(db)
    return {"id": node.id, "node": to_node_view(node), "pushed": pushed}


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

    problem = validate_link(body.link)
    if problem:
        raise HTTPException(400, {"code": "bad_link", "detail": problem})

    _apply_node(node, body)
    db.commit()
    db.add(AuditLog(user_id=admin.id, action="node_update", detail=node.name))
    db.commit()
    pushed = _broadcast_config(db)
    return {"ok": True, "node": to_node_view(node), "pushed": pushed}


@router.delete(Api.ADMIN_NODES + "/{node_id}")
def delete_node(
    node_id: int, admin: User = Depends(get_current_admin), db: DBSession = Depends(get_db)
):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, {"code": "not_found", "detail": "节点不存在"})
    db.delete(node)
    db.commit()
    db.add(AuditLog(user_id=admin.id, action="node_delete", detail=str(node_id)))
    db.commit()
    pushed = _broadcast_config(db)
    return {"ok": True, "pushed": pushed}


@router.put(Api.ADMIN_USERS + "/{user_id}/nodes")
def bind_user_nodes(
    user_id: int,
    body: BindUserNodes,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """把这个客户绑到哪些节点上。**整体替换**，不是追加。

    绑定的节点决定他能拿到什么订阅：绑几个就有几条链接，
    一个都不绑（或绑的都被停用了）订阅就是空的，客户端会就地销毁本地订阅。
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, {"code": "not_found", "detail": "用户不存在"})

    # 再兜一道：模型层已经卡了长度，这里是防着别处（比如脚本直连库）
    # 绕过接口塞进去更多。绑定是整体替换，超了就整批拒掉，不静默截断
    # —— 静默截断的话用户以为绑上了，实际没有。
    if len(set(body.node_ids)) > MAX_NODES_PER_USER:
        raise HTTPException(
            422,
            {
                "code": "too_many_nodes",
                "detail": f"一个客户最多绑 {MAX_NODES_PER_USER} 个节点（多了客户端也显示不下）",
            },
        )
    set_bound_nodes(db, user, body.node_ids)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="user_bind_nodes",
            detail=f"{user.username} -> {len(body.node_ids)} 个节点",
        )
    )
    db.commit()

    # 绑的是这个人，只推给他 —— 广播会把无关的人全叫醒
    from ..services.broadcast import notify_subscription_changed

    pushed = notify_subscription_changed(user.id, subscription_revision(user, subscription_text_for(db, user)))
    return {"ok": True, "node_ids": bound_node_ids(db, user), "pushed": pushed}


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
        # 订阅模式不让会话挂节点了，node_id 是空的 —— 别拿 None 去查
        node = db.get(Node, r.node_id) if r.node_id else None
        items.append(
            {
                "session_id": r.id,
                "user_id": r.user_id,
                "username": user.username if user else "?",
                "node_name": node.name if node else "",
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
        # 最新客户端版本 —— 概览页显示用。以前这里是 config_version，
        # 那是"中转层 + 全局配置版本号"时代的字段，改成订阅模式之后
        # 没有这个东西了（变更检测改走每个用户各自的 revision），
        # 面板那边就成了 undefined。
        "latest_client_version": (latest_release(db).version if latest_release(db) else ""),
        # 推送连接数：排查"为什么客户端没收到推送"时先看这里
        "push": hub.stats(),
    }


# ==========================================================================
# 客户端版本发布（「客户端更新」的数据源）
# ==========================================================================


class ReleasePublish(BaseModel):
    version: str = Field(min_length=1, max_length=32)
    notes: str = ""
    min_version: str = ""


class ReleasePull(BaseModel):
    """「拉取最新轻舟」的入参。整条都是空的也能跑 —— 拉最新那个 Release，
    版本号从资产文件名里抠。"""
    tag: str = ""            # 留空 = 最新；要发回旧版就写 v1.0.29
    version: str = ""        # 留空 = 从文件名抠
    notes: str = ""          # 留空 = 拿 Release 正文第一行
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
    # 发布方本地那个文件的字节数 / sha256。可选，但面板和 CLI 都会带上 ——
    # 上传被截断（比如传的是一个还在写的文件）时，只有靠它对得出来。
    size: int = Form(0),
    sha256: str = Form(""),
    file: UploadFile = File(...),
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """上传安装包并发布。

    包落到 releases/ 下，由 StaticFiles 对外提供下载；
    /api/client/latest 会把地址拼成 <站点>/downloads/<文件名>。

    真正的落盘逻辑在 services/updates.store_release_file() —— `canoe release`
    那条命令行走的是同一个函数，免得两处实现慢慢跑偏。

    传上来的字节数和摘要必须和发布方声明的一致，否则**整个包丢掉、不发布**：
    客户端下载是会核对摘要的，但核对的是服务端自己算的那份 —— 服务端把一份
    没传完的包当成好包发布出去，客户端的校验根本救不了它。
    """
    filename = safe_filename(file.filename or "Canoe.zip")
    try:
        _dest, size, digest = store_release_file(
            file.file, filename, expected_size=size, expected_sha256=sha256
        )
    except ReleaseTooLarge as exc:
        raise HTTPException(413, {"code": "too_large", "detail": str(exc)}) from exc
    except ReleaseMismatch as exc:
        raise HTTPException(400, {"code": "incomplete", "detail": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(500, {"code": "io_error", "detail": f"写文件失败：{exc}"}) from exc
    finally:
        file.file.close()

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


@router.post(Api.ADMIN_RELEASES + "/pull", status_code=status.HTTP_201_CREATED)
def admin_pull_release(
    body: ReleasePull,
    admin: User = Depends(get_current_admin),
    db: DBSession = Depends(get_db),
):
    """从 GitHub Release 拉安装包并发布 —— 面板上那个「拉取最新轻舟」。

    开发机把 zip 挂到 Release 上，服务端自己去拉。**不再从开发机往服务器
    上传那 83MB**：上传那条路断过两次，/tmp 里留下 46MB 的半截包，而
    store_release_file 是按**落盘的字节**算 sha256 的 —— 残包自洽，于是被
    当成合法版本发了出去。

    真正干活的在 `services/updates.pull_from_github` —— 命令行 `canoe release`
    走的是同一个函数，两处不会跑偏。
    """
    before = latest_release(db)
    try:
        row, verified = pull_from_github(
            db,
            repo=settings.github_repo,
            tag=body.tag,
            version=body.version,
            notes=body.notes,
            min_version=body.min_version,
        )
    except ReleaseSourceError as exc:
        raise HTTPException(400, {"code": "github", "detail": str(exc)}) from exc
    except ReleaseTooLarge as exc:
        raise HTTPException(413, {"code": "too_large", "detail": str(exc)}) from exc
    except ReleaseMismatch as exc:
        raise HTTPException(400, {"code": "incomplete", "detail": str(exc)}) from exc

    db.add(AuditLog(
        user_id=admin.id,
        action="release_pull",
        detail=f"{row.version} {row.filename} {row.size}B（GitHub {settings.github_repo}）"
               + ("" if verified else " ⚠ 没挂 .sha256，只核对了大小"),
    ))
    db.commit()
    pushed = notify_release(row.version, notes=row.notes)
    return {
        **_release_view(row),
        "pushed": pushed,
        "verified": verified,              # 摘要核对过没有
        "previous": before.version if before else "",
    }


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
