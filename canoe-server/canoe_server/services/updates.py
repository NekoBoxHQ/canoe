"""更新通道的数据层 —— 「客户端更新」和「订阅更新」两条。

    /api/client/latest   客户端有没有新版本   <- latest_release()
    /api/subscription    我的订阅是什么       <- subscription_for()

订阅**以密文下发**：明文只有客户端拿登录时那把 sub_key 解得开（见
canoe_core.crypto）。服务端随时可以不发 —— 空信封就是"没有"，
客户端见到必须销毁本地订阅。
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from canoe_core import (
    Api,
    ClientReleaseResponse,
    Envelope,
    SubscriptionResponse,
    assert_no_leaks,
    seal,
)

from ..config import settings
from ..models import ClientRelease, User, epoch


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


def subscription_revision(user: User, text: str) -> str:
    """订阅指纹。

    内容 / 账号状态 / 到期时间 任一变化，指纹就变。客户端存住上一轮的
    字符串比一比就知道要不要重新解密。

    ⚠ 指纹本身是**明文**发给客户端的。所以它只取哈希，不能把订阅内容
    的任何片段直接拼进去 —— 否则等于绕开加密把内容泄出去。
    """
    parts = [
        hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
        user.status,
        str(epoch(user.expire_at) or ""),
    ]
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def subscription_for(db: DBSession, user: User, sub_key: str) -> SubscriptionResponse:
    """当前用户此刻的订阅 —— 内容是**加密**的。

    **不建会话、不发凭证** —— 只读接口，客户端没启航时也能随手调。

    什么情况下给空信封（客户端据此销毁本地订阅）：
      · 账号被封
      · 账号已到期
      · 管理员把订阅栏清空了

    空信封不是报错 —— 报错客户端会重试，空信封是明确的"没有"。
    """
    from .nodes import subscription_text_for

    text = ""

    if user.status == "active" and not user_expired(user):
        # 订阅正文 = 这个用户绑定的那些节点的链接（加可选的手工附加）。
        # 一个节点都没绑就是空串 -> 空信封 -> 客户端就地销毁本地订阅。
        text = subscription_text_for(db, user).strip()

    revision = subscription_revision(user, text)
    # sub_key 为空（老数据里的令牌没这把钥匙）时不加密也不报错，
    # 直接给空信封 —— 客户端重新登舟就会拿到一把新的。
    envelope = seal(text, sub_key, revision=revision) if sub_key else Envelope(revision=revision)

    resp = SubscriptionResponse(
        node_name=None,
        expires_at=epoch(user.expire_at),
        heartbeat_interval=settings.heartbeat_interval,
        revision=revision,
        envelope=envelope,
    )
    # 信封的 data 是密文，明文链接不可能出现在这里；泄漏了就直接炸。
    assert_no_leaks({k: v for k, v in resp.model_dump().items() if k != "envelope"})
    return resp


def user_expired(user: User) -> bool:
    """账号是否已过期。没设到期时间的账号永不过期。"""
    exp = epoch(user.expire_at)
    return exp is not None and exp <= int(time.time())
