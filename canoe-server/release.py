"""发布一个客户端安装包 —— 命令行版，不用开面板。

用法：

    python release.py Canoe-1.0.1-win64.zip
    python release.py Canoe-1.0.1-win64.zip --notes "删掉中转层"
    python release.py https://example.com/Canoe-1.0.1-win64.zip
    python release.py --list

版本号默认从文件名里抠（`Canoe-1.0.1-win64.zip` -> `1.0.1`）；抠不出来
就必须用 `--version` 显式给。

落盘和登记走的是 `services/updates` 里那两个函数 —— 跟面板上传接口
同一份实现，不然两边会慢慢跑偏（一边修了大小限制，另一边忘了）。

管理脚本里包了一层：`canoe release <zip>`。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

# Windows 控制台默认 GBK，中文会炸。强制 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from canoe_server.config import settings
from canoe_server.database import SessionLocal, init_db
from canoe_server.models import AuditLog, User
from canoe_server.services.updates import (
    ReleaseTooLarge,
    guess_version,
    list_releases,
    publish_release,
    release_dir,
    safe_filename,
    store_release_file,
)


def die(msg: str) -> NoReturn:
    print(f"[x] {msg}", file=sys.stderr)
    raise SystemExit(1)


def cleanup_temp(root: Path | None) -> None:
    """删掉下载用的临时目录。

    ⚠ 必须容忍 None —— 传进来的是本地路径时压根没有临时目录，
    `shutil.rmtree(None, ignore_errors=True)` 照样会抛 TypeError
    （ignore_errors 只管 OS 错误，不管参数类型）。踩过一次：
    本该是"文件名里抠不出版本号"的友好提示，结果吐了个栈。
    """
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


def fetch(url: str) -> Path:
    """从 http(s) 拉一个包到临时文件。"""
    tmp = Path(tempfile.mkdtemp(prefix="canoe-rel-")) / Path(url.split("?")[0]).name
    print(f"[*] 下载 {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out, 1024 * 1024)
    except (urllib.error.URLError, OSError) as exc:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        die(f"下载失败：{exc}")
    print(f"[*] 落盘 {tmp.stat().st_size / 1024 / 1024:.1f} MB")
    return tmp


def public_base() -> str:
    """下载地址的前缀。跟 /api/client/latest 里的算法保持一致。"""
    base = (settings.public_base_url or "").rstrip("/")
    if base:
        return base
    scheme = "https" if (settings.tls_cert and settings.tls_key) else "http"
    return f"{scheme}://127.0.0.1:{settings.port}"


def show_list() -> int:
    init_db()       # 全新机器上还没建表，--list 也得能回答
    with SessionLocal() as db:
        rows = list_releases(db)
    if not rows:
        print("  还没有发布过任何版本。")
        print("  发第一个：python release.py Canoe-1.0.1-win64.zip")
        return 0
    print(f"  {'版本':<12} {'大小':>10}  {'启用':<4} 文件")
    for r in rows:
        size = f"{r.size / 1024 / 1024:.1f} MB" if r.size else "-"
        print(f"  {r.version:<12} {size:>10}  {'是' if r.enabled else '否':<4} {r.filename or '-'}")
    latest = rows[0]        # list_releases 已经按版本从大到小排好
    print(f"\n  客户端现在会拿到：{latest.version}")
    print(f"  下载地址：{public_base()}/downloads/{latest.filename or '-'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="release.py",
        description="发布一个客户端安装包",
        add_help=True,
    )
    ap.add_argument("package", nargs="?", help="安装包路径，或者 http(s) 地址")
    ap.add_argument("--version", default="", help="版本号（默认从文件名里抠）")
    ap.add_argument("--notes", default="", help="更新说明")
    ap.add_argument("--min-version", default="", help="低于这个版本强制升级")
    ap.add_argument("--file-name", default="", help="存到服务器上用什么文件名")
    ap.add_argument("--list", action="store_true", help="列出已经发布的版本")
    args = ap.parse_args()

    if args.list or not args.package:
        return show_list()

    init_db()       # 可能还没建表（比如手工 copy 过来的机器）

    src_arg = args.package
    temp_root: Path | None = None
    if src_arg.startswith(("http://", "https://")):
        local = fetch(src_arg)
        temp_root = local.parent
    else:
        local = Path(src_arg).expanduser()
        if not local.is_file():
            die(f"找不到文件：{local}")

    filename = safe_filename(args.file_name or local.name)
    version = args.version.strip() or guess_version(filename)
    if not version:
        cleanup_temp(temp_root)
        die(f"从 {filename} 里抠不出版本号，请用 --version 指定")

    try:
        with local.open("rb") as fh:
            dest, size, digest = store_release_file(fh, filename)
    except ReleaseTooLarge as exc:
        cleanup_temp(temp_root)
        die(str(exc))
    except OSError as exc:
        cleanup_temp(temp_root)
        die(f"写文件失败：{exc}")
    finally:
        cleanup_temp(temp_root)

    with SessionLocal() as db:
        row = publish_release(
            db,
            version=version,
            filename=filename,
            size=size,
            sha256=digest,
            notes=args.notes,
            min_version=args.min_version,
        )
        # 审计里记是谁发的。命令行没有"当前管理员"这个身份，
        # 就记到 .env 里那个管理员名下（找不到就留空，不编一个假的）。
        admin = db.scalars(
            select(User).where(User.username == settings.admin_username)
        ).first()
        db.add(
            AuditLog(
                user_id=admin.id if admin else None,
                action="release_upload",
                detail=f"{version} {filename} {size}B（命令行：canoe release）",
            )
        )
        db.commit()
        enabled_total = len([r for r in list_releases(db) if r.enabled])

    print()
    print("=" * 56)
    print(f"  已发布    {row.version}    （库里一共 {enabled_total} 个版本）")
    print(f"  文件      {dest}")
    print(f"  大小      {size / 1024 / 1024:.1f} MB")
    print(f"  sha256    {digest}")
    print(f"  下载      {public_base()}/downloads/{filename}")
    print("=" * 56)
    print()
    print("  客户端下次点「更新」就会看到它；在线的客户端会收到推送。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
