"""更新通道的数据层 —— 「客户端更新」和「订阅更新」两条。

    /api/client/latest   客户端有没有新版本   <- latest_release()
    /api/subscription    我的订阅变了没有     <- subscription_for()

两条都**只回客户端该看的东西**：
    · 更新记录里只有版本号和安装包元信息，没有任何节点信息；
    · 订阅走 to_entry_payload()，和 /api/config 同一条白名单出口。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from canoe_core import Api, ClientReleaseResponse, SubscriptionResponse

from ..config import settings
from ..models import ClientRelease, Node, User, epoch
from .nodes import get_config_version, pick_node, to_entry_payload


# --------------------------------------------------------------------------
# 版本号
# --------------------------------------------------------------------------


def version_key(text: str) -> tuple[int, ...]:
    """'v1.10.2-beta' -> (1, 10, 2)。取不到数字就给空元组（排最后）。"""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3])


def release_dir() -> Path:
    path = Path(settings.release_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str) -> str:
    """只留文件名本身，挡掉 ../ 和绝对路径这类上传文件名。"""
    base = Path(name).name
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base[:200] or "release.bin"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# 客户端更新
# --------------------------------------------------------------------------


def latest_release(db: DBSession) -> ClientRelease | None:
    """返回启用的发布里版本号最大的那个。

    不用 SQL 的 MAX(version) —— 版本号是字符串，'1.9' 会排在 '1.10' 前面。
    取回来在 Python 里比。
    """
    rows = db.scalars(select(ClientRelease).where(ClientRelease.enabled.is_(True))).all()
    if not rows:
        return None
    return max(rows, key=lambda r: version_key(r.version))


def to_release_payload(release: ClientRelease, base_url: str) -> ClientReleaseResponse:
    url = ""
    if release.filename:
        url = f"{base_url.rstrip('/')}{Api.DOWNLOAD_PREFIX}/{release.filename}"
    return ClientReleaseResponse(
        version=release.version,
        url=url,
        notes=release.notes or "",
        published_at=epoch(release.published_at) or 0,
        size=release.size or 0,
        sha256=release.sha256 or "",
        min_version=release.min_version or "",
    )


def publish_release(
    db: DBSession,
    *,
    version: str,
    filename: str = "",
    size: int = 0,
    sha256: str = "",
    notes: str = "",
    min_version: str = "",
) -> ClientRelease:
    """发布（或覆盖）一个版本。同一个版本号重复发布就更新它。"""
    row = db.scalars(
        select(ClientRelease).where(ClientRelease.version == version)
    ).first()
    if row is None:
        row = ClientRelease(version=version)
        db.add(row)

    row.filename = filename or row.filename
    row.size = size or row.size
    row.sha256 = sha256 or row.sha256
    if notes:
        row.notes = notes
    row.min_version = min_version
    row.enabled = True
    db.commit()
    db.refresh(row)
    return row


def list_releases(db: DBSession) -> list[ClientRelease]:
    rows = list(db.scalars(select(ClientRelease)).all())
    rows.sort(key=lambda r: version_key(r.version), reverse=True)
    return rows


# --------------------------------------------------------------------------
# 订阅更新
# --------------------------------------------------------------------------


def revision_of(config_version: int, node: Node | None, entry) -> str:
    """订阅指纹。

    入口域名、端口、UUID、路径、节点、配置版本 —— 任一变化都会让指纹变，
    客户端只要存住上一轮的字符串比一比就知道要不要更新。
    不接入任何真实节点字段（这里也拿不到）。
    """
    parts = [
        str(config_version),
        str(node.id) if node is not None else "",
        node.name if node is not None else "",
        entry.host if entry else "",
        str(entry.port) if entry else "",
        entry.uuid if entry else "",
        entry.path if entry else "",
        entry.sni if entry else "",
    ]
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def subscription_for(db: DBSession, user: User) -> SubscriptionResponse:
    """当前用户此刻的订阅。

    **不建会话、不发凭证** —— 只是个"看看现在是什么"的只读接口，
    所以客户端可以在没启航的时候随手调。
    """
    node = pick_node(db, user)
    entry = to_entry_payload(node) if node is not None else None
    config_version = get_config_version(db)

    resp = SubscriptionResponse(
        config_version=config_version,
        node_name=node.name if node is not None else None,
        entry=entry,
        expires_at=epoch(user.expire_at),
        heartbeat_interval=settings.heartbeat_interval,
        revision=revision_of(config_version, node, entry),
    )
    resp.assert_no_real_fields()
    if resp.entry is not None:
        resp.entry.assert_whitelisted()
    return resp
