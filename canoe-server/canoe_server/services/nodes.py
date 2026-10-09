"""节点选择与序列化。

★★ 安全核心文件 ★★

    to_entry_payload()  客户端能看到节点信息的**唯一**出口，白名单逐字段组装。

    绝不要把它改成 node.model_dump() / node.__dict__ —— 那样新加的
    任何 real_* 字段都会顺带泄漏给客户端。这是这类系统最常见的失手方式。

    改这里之前请先读 docs/04-security.md 第 1 层。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from canoe_core import EntryPayload

from ..models import ConfigMeta, Node, User, UserNode, epoch

NODES_VERSION_KEY = "nodes_version"


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------


def to_entry_payload(node: Node) -> EntryPayload:
    """客户端可见的入口参数。

    返回类型是 canoe_core.EntryPayload —— 它构造时会校验字段集合，
    多一个字段都会抛错。所以这里不可能"顺手"多带东西出去。
    """
    payload = EntryPayload(
        transport=node.entry_transport,
        host=node.entry_host,
        port=node.entry_port,
        uuid=node.entry_uuid,
        path=node.entry_path,
        sni=node.entry_sni or node.entry_host,
        tls=node.entry_tls,
        insecure=node.entry_insecure,
    )
    payload.assert_whitelisted()
    return payload


def to_admin_payload(node: Node) -> dict:
    """管理端可见，**含真实节点**。只允许被 admin 路由调用。"""
    return {
        "id": node.id,
        "name": node.name,
        "remark": node.remark,
        "enabled": node.enabled,
        "sort_order": node.sort_order,
        "entry": {
            "transport": node.entry_transport,
            "host": node.entry_host,
            "port": node.entry_port,
            "uuid": node.entry_uuid,
            "path": node.entry_path,
            "sni": node.entry_sni,
            "tls": node.entry_tls,
            "insecure": node.entry_insecure,
        },
        "real": {
            "protocol": node.real_protocol,
            "host": node.real_host,
            "port": node.real_port,
            "uuid": node.real_uuid,
            "flow": node.real_flow,
            "tls": node.real_tls,
            "sni": node.real_sni,
            "fingerprint": node.real_fingerprint,
            "network": node.real_network,
            "ws_path": node.real_ws_path,
            "ws_host": node.real_ws_host,
            "grpc_service": node.real_grpc_service,
            "insecure": node.real_insecure,
            "extra": node.real_extra or {},
        },
        "created_at": epoch(node.created_at) or 0,
        "updated_at": epoch(node.updated_at) or 0,
    }


# --------------------------------------------------------------------------
# 选择
# --------------------------------------------------------------------------


def bound_node_ids(db: DBSession, user: User) -> list[int]:
    return list(
        db.scalars(select(UserNode.node_id).where(UserNode.user_id == user.id)).all()
    )


def pick_node(db: DBSession, user: User, requested_node_id: int | None = None) -> Node | None:
    """选择要给该用户分配的节点。

    优先级：
        1. 请求里明确指定的节点（且可用）
        2. user_node 表里绑定的节点（按 sort_order）
        3. 全局第一个可用节点
    """
    if requested_node_id is not None:
        node = db.get(Node, requested_node_id)
        if node is not None and node.enabled:
            return node

    bound = bound_node_ids(db, user)
    if bound:
        node = db.scalars(
            select(Node)
            .where(Node.id.in_(bound), Node.enabled.is_(True))
            .order_by(Node.sort_order, Node.id)
            .limit(1)
        ).first()
        if node is not None:
            return node

    return db.scalars(
        select(Node).where(Node.enabled.is_(True)).order_by(Node.sort_order, Node.id).limit(1)
    ).first()


def node_name_for(db: DBSession, user: User) -> str | None:
    """该用户当前会被分配到哪个节点 —— 登录时就要告诉客户端。

    不能只看 user_node：没绑定节点的用户是自动分配的，
    但在客户端主界面"节点名称"这一栏里，用户在点启航之前就该看到它。
    """
    node = pick_node(db, user)
    return node.name if node else None


# --------------------------------------------------------------------------
# 配置版本号
# --------------------------------------------------------------------------


def get_config_version(db: DBSession) -> int:
    meta = db.get(ConfigMeta, NODES_VERSION_KEY)
    if meta is None:
        meta = ConfigMeta(key=NODES_VERSION_KEY, value="1")
        db.add(meta)
        db.commit()
    return int(meta.value)


def bump_config_version(db: DBSession) -> int:
    """节点增删改时调用。客户端心跳发现版本变了会重新拉配置。"""
    meta = db.get(ConfigMeta, NODES_VERSION_KEY)
    if meta is None:
        db.add(ConfigMeta(key=NODES_VERSION_KEY, value="2"))
        db.commit()
        return 2
    meta.value = str(int(meta.value) + 1)
    db.commit()
    return int(meta.value)


def count_nodes(db: DBSession, enabled_only: bool = False) -> int:
    stmt = select(func.count()).select_from(Node)
    if enabled_only:
        stmt = stmt.where(Node.enabled.is_(True))
    return db.scalar(stmt) or 0
