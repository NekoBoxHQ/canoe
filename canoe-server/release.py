"""发布一个客户端安装包 —— 命令行版，不用开面板。

用法：

    python release.py --github                    # 拉最新那个 Release
    python release.py --github v1.0.31            # 拉指定 tag
    python release.py --list                      # 看看已经发了哪些

★ 上面那条 `--github` 就是主路径，也是 `canoe release` 用的那条。
  开发机把 zip 挂到 GitHub Release 上，服务端自己去拉，**不用把 83MB
  从开发机怼上来**。

  仓库是公开的，这条下载不需要任何凭据。开发机那边 `package_release.py`
  已经把 zip 和 `.sha256` 都算好了，Release 资产就是那两份；这里会把
  `.sha256` 读回来核对 —— 摘要跟传输链路无关，对不上就整个丢掉。

  为什么不走"本地 SSH 上传"：那条路上真断过两次连接，/tmp 里留下 46MB 的
  半截包，而 store_release_file 是按**落盘的字节**算摘要的 —— 残包自洽，
  于是被当成合法版本发了出去。

剩下的两种入参形式（本机文件 / 任意 http(s) 地址）是**底层能力**：
`canoe release` 已经不暴露它们了，留着是为了应急时能直接 python 跑。
其中本机文件的用法是：

    python release.py Canoe-1.0.1-win64.zip [--notes "说明"]
    python release.py https://example.com/Canoe-1.0.1-win64.zip --sha256 <摘要>

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
    ReleaseMismatch,
    ReleaseSourceError,
    ReleaseTooLarge,
    guess_version,
    latest_release,
    list_releases,
    publish_release,
    pull_from_github,
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


def publish_from_github(args) -> int:
    """从 GitHub Release 拉一个包并发布。

    真正干活的是 `services/updates.pull_from_github` —— 面板上那个
    「拉取最新轻舟」走的是同一个函数。这里只负责把它接到命令行上：
    解析参数、打印结果、给退出码。
    """
    init_db()
    with SessionLocal() as db:
        before = latest_release(db)
        try:
            row, verified = pull_from_github(
                db,
                repo=settings.github_repo,
                tag=args.github,
                version=args.version,
                notes=args.notes,
                min_version=args.min_version,
            )
        except (ReleaseSourceError, ReleaseTooLarge, ReleaseMismatch) as exc:
            die(str(exc))

        admin = db.scalars(
            select(User).where(User.username == settings.admin_username)
        ).first()
        db.add(
            AuditLog(
                user_id=admin.id if admin else None,
                action="release_pull",
                detail=f"{row.version} {row.filename} {row.size}B"
                       f"（GitHub {settings.github_repo}）"
                       + ("" if verified else " ⚠ 没挂 .sha256，只核对了大小"),
            )
        )
        db.commit()
        version, size = row.version, row.size or 0
        digest, filename = row.sha256 or "", row.filename or ""
        enabled_total = len([r for r in list_releases(db) if r.enabled])

    print()
    print("=" * 56)
    print(f"  已发布    {version}    （库里一共 {enabled_total} 个版本）")
    # 重发同一个版本时不打"上一版" —— 那会印成"上一版 1.0.31"，看着像没发上去
    if before and before.version != version:
        print(f"  上一版    {before.version}")
    print(f"  来源      GitHub {settings.github_repo}"
          + (f"   tag {args.github}" if args.github else "   最新那个 Release"))
    print(f"  文件      {filename}")
    print(f"  大小      {size / 1024 / 1024:.1f} MB")
    print(f"  sha256    {digest}")
    if not verified:
        print("  ⚠ 这个 Release 没挂 .sha256 —— 只核对了大小，证明不了包是完整的")
    print(f"  下载      {public_base()}/downloads/{filename}")
    print("=" * 56)
    print()
    print("  客户端下次点「更新」就会看到它；在线的客户端会收到推送。")
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
    ap.add_argument(
        "--sha256", default="",
        help="发布者本地算好的摘要；下载到的东西核对不过就不发（防残包）",
    )
    # ★ 推荐用法：从 GitHub Release 拉。给 `canoe release` 用的就是这个。
    #   nargs="?" + const="" 让它既能 `--github`（拉最新）也能 `--github v1.0.31`。
    ap.add_argument(
        "--github", nargs="?", const="", default=None,
        help="从 GitHub Release 拉取并发布（后面可跟 tag，不跟就是最新那个）",
    )
    ap.add_argument("--list", action="store_true", help="列出已经发布的版本")
    args = ap.parse_args()

    if args.github is not None:
        return publish_from_github(args)

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

    # ★ 这个包在读取期间不许变大。
    #
    #   命令行这条路上真出过事：PyInstaller 还在往 dist/ 里写 exe 的时候就被
    #   `canoe release` 抓去发了，读端读到那时的 EOF 就以为读完了 —— 87MB 的
    #   包装成 47MB 送上去。服务端照单全收、照这份残包算 sha256 写进发布记录；
    #   客户端下载时一校验"通过"（它核对的就是这份残包的摘要），装上才发现
    #   exe 是残的 —— 单文件 exe 截断了**照样能启动**，只是解不出 python313.dll，
    #   弹一句 "Failed to load Python DLL" 就完事。
    #
    #   所以两头都卡：传上去的字节数必须等于读之前的大小（store_release_file
    #   那边核对），传完之后它也不许再变大。
    before = local.stat().st_size
    after = before
    try:
        with local.open("rb") as fh:
            dest, size, digest = store_release_file(
                fh, filename, expected_size=before,
                expected_sha256=args.sha256.strip(),
            )
        after = local.stat().st_size
    except ReleaseTooLarge as exc:
        cleanup_temp(temp_root)
        die(str(exc))
    except ReleaseMismatch as exc:
        cleanup_temp(temp_root)
        die(str(exc))
    except OSError as exc:
        cleanup_temp(temp_root)
        die(f"写文件失败：{exc}")
    finally:
        cleanup_temp(temp_root)

    if after != size:
        (release_dir() / filename).unlink(missing_ok=True)
        die(
            f"这个包在传的过程中还在变大（{size} → {after} 字节）—— "
            "它八成还没写完，或者还在复制。等它稳定了再发一次。"
        )

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
