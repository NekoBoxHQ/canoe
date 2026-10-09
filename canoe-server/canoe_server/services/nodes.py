"""节点与订阅正文。

一个节点就是**一行链接**（管理员在面板上贴的），加一个给自己看的备注。
客户端的订阅 = 这个用户绑定的那些节点的链接，一行一行拼起来。

这里没有"选一个节点下发"这回事 —— 那是中转层时代的做法。现在服务端
不转发流量，也就不需要替客户端挑节点：给它哪些节点，它自己看着连。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from canoe_core import LinkError, parse_links

from ..models import Node, User, UserNode, epoch


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------


def link_summary(link: str) -> dict:
    """从一行链接里抠出能给人看的信息（名称/协议/主机/端口）。

    只是为了列表好看。解析失败不抛 —— 管理员可能正在粘一半，
    存下来报错就是了，不该让整个页面挂掉。
    """
    text = (link or "").strip()
    if not text:
        return {"ok": False, "name": "", "protocol": "", "host": "", "port": 0}

    result = parse_links(text)
    if not result.links:
        return {"ok": False, "name": "", "protocol": "", "host": "", "port": 0}

    nl = result.links[0]
    ob = nl.outbound
    return {
        "ok": True,
        "name": nl.name,
        "protocol": str(ob.get("type") or ""),
        "host": str(ob.get("server") or ""),
        "port": int(ob.get("server_port") or 0),
    }


def to_node_view(node: Node) -> dict:
    """管理端看到的节点。"""
    info = link_summary(node.link)
    return {
        "id": node.id,
        "name": node.name,
        "remark": node.remark,
        "enabled": node.enabled,
        "sort_order": node.sort_order,
        "link": node.link or "",
        # 解析出来的展示信息（前端不用自己再解析一遍）
        "protocol": info["protocol"],
        "host": info["host"],
        "port": info["port"],
        "valid": info["ok"],
        "created_at": epoch(node.created_at) or 0,
        "updated_at": epoch(node.updated_at) or 0,
    }


# --------------------------------------------------------------------------
# 绑定与订阅
# --------------------------------------------------------------------------


def bound_node_ids(db: DBSession, user: User) -> list[int]:
    return list(
        db.scalars(select(UserNode.node_id).where(UserNode.user_id == user.id)).all()
    )


def bound_nodes(db: DBSession, user: User, enabled_only: bool = True) -> list[Node]:
    """该用户绑定的节点，按 sort_order 排。

    没绑任何节点就返回空 —— **不再有"自动分配一个"这种兜底**：
    那会让"我没给这个客户配节点"和"配了但都停用了"变得无法区分，
    而这两种情况服务端给出的订阅都应该是空的。
    """
    stmt = (
        select(Node)
        .join(UserNode, UserNode.node_id == Node.id)
        .where(UserNode.user_id == user.id)
        .order_by(Node.sort_order, Node.id)
    )
    if enabled_only:
        stmt = stmt.where(Node.enabled.is_(True))
    return list(db.scalars(stmt).all())


def set_bound_nodes(db: DBSession, user: User, node_ids: list[int]) -> None:
    """整体替换该用户的绑定。"""
    db.query(UserNode).filter(UserNode.user_id == user.id).delete(synchronize_session=False)
    for nid in dict.fromkeys(node_ids):     # 去重，保持顺序
        if db.get(Node, nid) is None:
            continue
        db.add(UserNode(user_id=user.id, node_id=nid))
    db.commit()


def subscription_text_for(db: DBSession, user: User) -> str:
    """这个用户此刻该拿到的订阅正文 = 绑定节点的链接拼起来。

    加上他自己那份「附加订阅」（可选，管理员想单独给他塞点别的时用）。
    """
    return "\n".join(n.link.strip() for n in bound_nodes(db, user) if (n.link or "").strip())


def count_nodes(db: DBSession, enabled_only: bool = False) -> int:
    stmt = select(func.count()).select_from(Node)
    if enabled_only:
        stmt = stmt.where(Node.enabled.is_(True))
    return db.scalar(stmt) or 0


def validate_link(link: str) -> str:
    """管理员提交节点时校验一下这行链接能不能解析。返回可读的错误，没毛病返回空串。"""
    text = (link or "").strip()
    if not text:
        return "节点链接不能为空"
    try:
        result = parse_links(text)
    except LinkError as exc:
        return f"这行链接解析不了：{exc}"
    if not result.links:
        return "认不出这行链接 —— 支持 ss:// vmess:// vless:// trojan://"
    if len(result.links) > 1:
        return "一个节点只放一行链接（多了请分成多个节点）"
    return ""
