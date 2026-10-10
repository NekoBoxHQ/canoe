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
from . import github


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


class ReleaseTooLarge(Exception):
    """安装包超过 settings.max_release_mb。"""


class ReleaseMismatch(Exception):
    """收到的安装包和发布方声明的对不上（多半是这一次上传没传完）。"""


class ReleaseSourceError(Exception):
    """上游（GitHub Release）那边的问题：找不到 Release、没有 zip 资产、下载失败。

    单独一个类，是为了让调用方能把它跟"包本身不对"分开 —— 面板要回 400 并带
    人话，命令行要直接吐这句话。
    """


def guess_version(filename: str) -> str:
    """从文件名里抠版本号：Canoe-1.0.1-win64.zip -> 1.0.1。

    只是为了少让用户手打一遍 —— 抠不出来就返回空串，由调用方要求显式给。
    """
    m = re.search(r"\d+(?:\.\d+)+", Path(filename).name)
    return m.group(0) if m else ""


def store_release_file(
    src,
    filename: str,
    *,
    expected_size: int = 0,
    expected_sha256: str = "",
) -> tuple[Path, int, str]:
    """把一个安装包流式落到 releases/ 下，返回 (路径, 字节数, sha256)。

    传进来的 `src` 只要有 `.read(n)` 就行（UploadFile.file 或普通文件对象）。

    超限或者写盘失败都**不会**把半个包留下 —— 留了的话，下载页面上会
    挂着一个永远下不完的文件，而发布记录看起来是成功的。

    ★ 发布方可以把本地那个文件的字节数和 sha256 一起报上来
      （expected_size / expected_sha256），这里核对。**这个必须要有**：

      只靠 HTTP 的 Content-Length 证明不了什么 —— 它只能说明"这一段 body
      收全了"，说明不了"这个 body 是一份完整的安装包"。真出过事：上传脚本
      读的是**还在写**的文件，把 47MB（实际 87MB）当成完整包传了上来。
      服务端照单全收、照这份残缺内容算 sha256、写进发布记录；客户端下载下来
      一校验"通过"（因为它核对的就是这个残缺文件的摘要），装上才发现是个
      残废的 exe —— 而单文件 exe 截断了**照样能启动**，只是解不出
      python313.dll，弹一句 "Failed to load Python DLL" 就没了。
      让发布方自己报数，是唯一能戳穿这种情况的办法。
    """
    dest = release_dir() / safe_filename(filename)
    limit = settings.max_release_mb * 1024 * 1024
    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ReleaseTooLarge(
                        f"安装包超过 {settings.max_release_mb} MB 上限"
                    )
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    stored = sha256_file(dest)
    if expected_size and size != expected_size:
        dest.unlink(missing_ok=True)
        raise ReleaseMismatch(
            f"安装包不完整：发布方声明 {expected_size} 字节，实际收到 {size} 字节"
        )
    if expected_sha256 and stored.lower() != expected_sha256.strip().lower():
        dest.unlink(missing_ok=True)
        raise ReleaseMismatch(
            f"安装包摘要对不上（服务端算出 {stored[:16]}…），上传可能被截断了"
        )
    return dest, size, stored


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
# 从 GitHub Release 拉一个包并发布
# --------------------------------------------------------------------------
def pull_from_github(
    db: DBSession,
    *,
    repo: str,
    tag: str = "",
    version: str = "",
    notes: str = "",
    min_version: str = "",
) -> tuple[ClientRelease, bool]:
    """从 GitHub Release 拉安装包并发布，返回 (发布记录, 摘要核对过没有)。

    ★ **面板那个「拉取最新轻舟」和命令行的 `canoe release` 都走这里。**

      以前这种"两处各写一份"的账吃过一次：面板给了 sha256、命令行忘了给，
      于是命令行发出去的包只核对了大小 —— 这种事看不出来，只能靠共用一份
      来避免。所以 GitHub 那几步（services/github.py）和落盘发布（下面这
      几步）都收进这一个函数。

    版本号优先从资产文件名里抠（Canoe-1.0.31-win64.zip -> 1.0.31），抠不出来
    退到 tag。更新说明留空就拿 Release 正文第一行 —— 客户端那个更新弹窗
    只显示一段话。
    """
    try:
        release = github.find_release(repo, tag)
        asset, digest = github.pick_assets(release)
    except github.GithubError as exc:
        raise ReleaseSourceError(str(exc)) from exc

    filename = safe_filename(str(asset.get("name") or "Canoe.zip"))
    ver = (
        version.strip()
        or guess_version(filename)
        or str(release.get("tag_name") or "").lstrip("vV")
    )
    if not ver:
        raise ReleaseSourceError(
            f"从资产名 {filename} 和 tag 里都抠不出版本号 —— 用 --version 显式给一个。"
        )

    if not notes.strip():
        notes = next(
            (ln.strip().strip("#*` ") for ln in str(release.get("body") or "").splitlines()
             if ln.strip()),
            "",
        )[:200]

    try:
        with github.open_asset(asset) as src:
            _dest, size, sha = store_release_file(
                src,
                filename,
                expected_size=int(asset.get("size") or 0),
                expected_sha256=digest,
            )
    except github.GithubError as exc:
        raise ReleaseSourceError(str(exc)) from exc
    except (ReleaseTooLarge, ReleaseMismatch):
        raise                      # 这两种调用方要单独认，别包成"上游问题"
    except OSError as exc:
        raise ReleaseSourceError(f"下载或写盘失败：{exc}") from exc

    row = publish_release(
        db,
        version=ver,
        filename=filename,
        size=size,
        sha256=sha,
        notes=notes,
        min_version=min_version,
    )
    return row, bool(digest)


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
