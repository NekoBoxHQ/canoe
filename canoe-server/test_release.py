"""`release.py` / `canoe release` 的测试。

**不需要服务端在跑** —— 全程用一份临时数据库 + 临时 releases 目录，
跑完就删，碰不到你线上的东西。

    cd canoe-server
    .venv/Scripts/python test_release.py        # Windows
    .venv/bin/python test_release.py            # Linux

盯的是这几件事：

  1. 版本号能从文件名里抠出来（抠不出就得报错让人显式给，不能静默发成 0.0.0）
  2. 包真的落到 releases/ 下，大小和 sha256 对得上
  3. 超过上限的包被拒绝，而且**不留半个文件**在磁盘上
  4. 文件名里的 ../ 被收拾掉（不然能往上级目录写东西）
  5. 同一个版本号重发是覆盖，不是插两条
  6. --list 的输出里能看到刚发的版本
  7. 发布方报的字节数/摘要和收到的对不上就**整个丢掉**（上传被截断过一次）
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {extra}")


def make_zip(path: Path, payload: bytes = b"P" * 4096, *, compress: bool = True) -> None:
    """造一个假的安装包。

    ⚠ 要试"超限被拒"时必须让内容**压不动** —— 一长串相同字节会被
    deflate 压到几 KB，于是 2 MB 的"包"根本没到 1 MB 上限，
    测试会以为拒绝逻辑坏了（其实坏的是这个测试）。用 os.urandom。
    """
    mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", mode) as z:
        z.writestr("Canoe.exe", payload)


def run_cli(args: list[str], env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """跑一次 release.py，环境变量顶掉数据库和 releases 目录。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **env_extra)
    return subprocess.run(
        [sys.executable, str(HERE / "release.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(HERE),
        timeout=120,
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="canoe-reltest-"))
    releases = tmp / "releases"
    releases.mkdir()
    env = {
        "DATABASE_URL": f"sqlite:///{(tmp / 'test.db').as_posix()}",
        "RELEASE_DIR": str(releases),
        "PUBLIC_BASE_URL": "https://canoe.example.com",
        "ADMIN_USERNAME": "admin",
    }
    # 下面有几段是**在本进程里**直接调 store_release_file 的，不是走子进程。
    # canoe_server.config 在 import 的时候就把这些读进去了，所以必须先
    # 灌进去再 import —— 不然 release_dir() 会指向真的 releases/，
    # 测试往线上目录写包。
    os.environ.update(env)

    print("\n== 轻舟 · 客户端发布（命令行）测试 ==\n")

    try:
        # --- 1. 版本号从文件名里抠 ---
        print("[1] 版本号")
        sys.path.insert(0, str(HERE))
        from canoe_server.services.updates import guess_version

        check("Canoe-1.0.1-win64.zip -> 1.0.1", guess_version("Canoe-1.0.1-win64.zip") == "1.0.1")
        check("Canoe-1.10.2.zip -> 1.10.2", guess_version("Canoe-1.10.2.zip") == "1.10.2")
        check("抠不出来时给空串（而不是编一个）", guess_version("canoe.zip") == "")

        r = run_cli(["--list"], env)
        check("还没发过时 --list 不报错", r.returncode == 0, r.stderr[:200])
        check("--list 提示了怎么发第一个", "release.py" in r.stdout, r.stdout[:200])

        # 文件名里真的没有版本号 —— 得先把这个文件造出来，
        # 不然会被"找不到文件"那条挡在前面，测的就不是版本号这件事了。
        noversion = tmp / "canoe.zip"
        make_zip(noversion)
        r = run_cli([str(noversion)], env)
        check("★ 文件名里没有版本号时，拒绝发（别发成 0.0.0）", r.returncode != 0, r.stdout[:200])
        check("拒绝的原因说得清", "版本号" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:200])
        r = run_cli([str(noversion), "--version", "3.2.1"], env)
        check("给了 --version 就能发", r.returncode == 0, (r.stdout + r.stderr)[:200])

        # --- 2. 正常发布 ---
        print("\n[2] 发布")
        pkg = tmp / "Canoe-1.0.1-win64.zip"
        make_zip(pkg)
        digest = hashlib.sha256(pkg.read_bytes()).hexdigest()

        r = run_cli([str(pkg), "--notes", "删掉中转层"], env)
        check("发布成功", r.returncode == 0, (r.stdout + r.stderr)[:300])
        check("★ 版本号是从文件名抠的", "1.0.1" in r.stdout, r.stdout[:300])
        check("★ 打印了 sha256", digest in r.stdout, r.stdout[-400:])
        check("打印了下载地址（用 PUBLIC_BASE_URL 拼）",
              "https://canoe.example.com/downloads/Canoe-1.0.1-win64.zip" in r.stdout, r.stdout[-400:])

        dest = releases / "Canoe-1.0.1-win64.zip"
        check("★ 包落到了 RELEASE_DIR 下", dest.is_file(), str(list(releases.iterdir())))
        check("落盘的字节和源文件一致",
              dest.is_file() and dest.read_bytes() == pkg.read_bytes())

        # --- 3. --list 能看到 ---
        print("\n[3] --list")
        r = run_cli(["--list"], env)
        check("列出版本号", "1.0.1" in r.stdout, r.stdout[:300])
        check("列出文件名", "Canoe-1.0.1-win64.zip" in r.stdout, r.stdout[:300])
        check("★ 说明了客户端会拿到哪个版本", "客户端现在会拿到" in r.stdout, r.stdout[:300])

        # --- 4. 同版本重发是覆盖 ---
        print("\n[4] 重复发布")
        pkg2 = tmp / "Canoe-1.0.1-win64.zip"
        make_zip(pkg2, payload=b"Q" * 8192)
        r = run_cli([str(pkg2)], env)
        check("同版本重发成功", r.returncode == 0, (r.stdout + r.stderr)[:200])
        r = run_cli(["--list"], env)
        # 数"以版本号开头的表格行"，而不是数版本号出现的次数 ——
        # 下面几行也会提到 1.0.1（"客户端现在会拿到：1.0.1"），
        # 那样数出来必然是 5，跟"有没有重复登记"没关系。
        rows = [l for l in r.stdout.splitlines() if l.startswith("  1.0.1 ")]
        check("★ 库里还是一条 1.0.1（不是两条）", len(rows) == 1, r.stdout[:400])

        # --- 5. 超过上限 ---
        print("\n[5] 体积上限")
        env_small = dict(env, MAX_RELEASE_MB="1")
        big = tmp / "Canoe-9.9.9-win64.zip"
        make_zip(big, payload=os.urandom(2 * 1024 * 1024), compress=False)
        r = run_cli([str(big)], env_small)
        check("★ 超限的包被拒绝", r.returncode != 0, (r.stdout + r.stderr)[:200])
        check("提示里说了上限", "上限" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:200])
        check("★ 没在磁盘上留半个包", not (releases / "Canoe-9.9.9-win64.zip").exists(),
              str(list(releases.iterdir())))

        # --- 6. 文件名里的路径穿越 ---
        print("\n[6] 文件名")
        sys.path.insert(0, str(HERE))
        from canoe_server.services.updates import safe_filename

        check("★ ../ 被收拾掉",
              "/" not in safe_filename("../../etc/passwd") and ".." not in safe_filename("../../etc/passwd"),
              safe_filename("../../etc/passwd"))

        # --- 7. 文件不存在时给出可读的错 ---
        print("\n[7] 找不到文件")
        r = run_cli([str(tmp / "没有这个文件.zip")], env)
        check("报错而不是崩栈", r.returncode != 0 and "Traceback" not in r.stderr, r.stderr[:300])
        check("错误里指出了路径", "找不到文件" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:200])

        # --- 8. 传上来的到底是不是一个完整的包 ---
        #
        # 真出过事：发布的时候读的是一个**还在写**的文件，87MB 的安装包装成
        # 47MB 送了上去。服务端照单全收、照这份残包算 sha256 写进发布记录；
        # 客户端下载时一校验"通过"（它核对的就是这份残包的摘要），装上才发现
        # exe 是残的 —— 单文件 exe 截断了**照样能启动**，只是解不出
        # python313.dll，弹一句 "Failed to load Python DLL" 就完事。
        # 所以发布方必须自报字节数/摘要，服务端拿它对。
        print("\n[8] 发布包完整性")
        from canoe_server.services.updates import (  # noqa: PLC0415
            ReleaseMismatch,
            store_release_file,
        )

        whole = b"W" * 50_000
        whole_digest = hashlib.sha256(whole).hexdigest()

        # 声明 5 万字节，实际只给 3 万 —— 就是"上传被截断"的样子
        try:
            store_release_file(
                io.BytesIO(whole[:30_000]), "Canoe-2.0.0-win64.zip",
                expected_size=len(whole),
            )
            check("★ 字节数对不上时必须拒绝（上传被截断）", False, "居然收下了")
        except ReleaseMismatch:
            check("★ 字节数对不上时必须拒绝（上传被截断）", True)
        check("★ 拒绝后磁盘上不留半个包",
              not (releases / "Canoe-2.0.0-win64.zip").exists(),
              str(list(releases.iterdir())))

        # 摘要对不上也一样
        try:
            store_release_file(
                io.BytesIO(whole), "Canoe-2.0.1-win64.zip", expected_sha256="0" * 64
            )
            check("★ 摘要对不上时必须拒绝", False, "居然收下了")
        except ReleaseMismatch:
            check("★ 摘要对不上时必须拒绝", True)
        check("★ 摘要对不上也不留文件",
              not (releases / "Canoe-2.0.1-win64.zip").exists(),
              str(list(releases.iterdir())))

        # 对得上就正常收下 —— 别把好包也拦了
        _d, got_size, got_digest = store_release_file(
            io.BytesIO(whole), "Canoe-2.0.2-win64.zip",
            expected_size=len(whole), expected_sha256=whole_digest,
        )
        check("★ 对得上就正常收下（没把好包一起拦了）",
              got_size == len(whole) and got_digest == whole_digest)
        check("★ 收下的包在磁盘上完整",
              (releases / "Canoe-2.0.2-win64.zip").stat().st_size == len(whole))

        # 不给这两个参数（老调用方）行为不变
        _d, got_size, _ = store_release_file(io.BytesIO(whole), "Canoe-2.0.3-win64.zip")
        check("老调用方（不报 size/sha256）照旧能用", got_size == len(whole))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 9. 从 GitHub Release 拉包 ---
    #
    # 面板上那个「拉取最新轻舟」走的就是这里。它替代了"开发机往服务器上传
    # 83MB"那条路 —— 那条断过两次，/tmp 里留下 46MB 半截包，而
    # store_release_file 是按落盘字节算摘要的，残包自洽，被当合法版本发了出去。
    # 这段不联网：只验挑资产、读 .sha256、拼 URL 这几步的判断。
    print("\n[9] 从 GitHub Release 拉包（不联网，验判断逻辑）")
    from canoe_server.services import github  # noqa: PLC0415

    digest = "a" * 64
    REL = {
        "tag_name": "v1.0.29",
        "body": "修掉更新后 Failed to load Python DLL\n\n其余说明……",
        "assets": [
            {"name": "Canoe-1.0.29-win64.zip.sha256", "size": 90,
             "browser_download_url": "https://example.invalid/sha"},
            {"name": "Canoe-1.0.29-win64.zip", "size": 87_176_078,
             "browser_download_url": "https://example.invalid/zip"},
            {"name": "Canoe-1.0.29-win64-debug.zip", "size": 100,
             "browser_download_url": "https://example.invalid/dbg"},
        ],
    }

    real_side = github.read_sidecar
    github.read_sidecar = lambda url: digest if url.endswith("/sha") else ""
    try:
        asset, got = github.pick_assets(REL)
        check("★ 挑的是安装包本体，不是旁边那份 .sha256",
              asset["name"] == "Canoe-1.0.29-win64.zip", str(asset.get("name")))
        check("★ 挂了多个 zip 时取最大的那个", asset["size"] == 87_176_078)
        check("★ 顺带把 .sha256 里的摘要读了回来", got == digest, got[:16])
    finally:
        github.read_sidecar = real_side

    try:
        github.pick_assets({"assets": []})
        check("Release 里没有 zip 时应当报错", False)
    except github.GithubError as exc:
        check("★ 没有 zip 时给的是人话（提示去跑 package_release.py）",
              "package_release.py" in str(exc), str(exc))

    # .sha256 是 sha256sum 那种标准格式，别写成只认纯 64 个字符
    import urllib.request as _urlreq  # noqa: PLC0415

    real_urlopen = _urlreq.urlopen

    class _Resp:
        def __init__(self, data): self._data = data
        def read(self): return self._data
        def __enter__(self): return self
        def __exit__(self, *a): return False

    try:
        _urlreq.urlopen = lambda url, timeout=0: _Resp(
            (digest + "  Canoe-1.0.29-win64.zip\n").encode())
        check("★ 标准的 `摘要  文件名` 格式能读出摘要",
              github.read_sidecar("https://example.invalid/x") == digest)

        _urlreq.urlopen = lambda url, timeout=0: _Resp(b"<!doctype html>404")
        check("★ 旁边挂的不是摘要文件时返回空串（不炸）",
              github.read_sidecar("https://example.invalid/x") == "")

        def _boom(url, timeout=0):
            raise OSError("断网")
        _urlreq.urlopen = _boom
        check("★ 读不到 .sha256 时返回空串（不能把整次发布弄挂）",
              github.read_sidecar("https://example.invalid/x") == "")
    finally:
        _urlreq.urlopen = real_urlopen

    seen: list[str] = []
    real_json = github._get_json
    github._get_json = lambda url: (seen.append(url), REL)[1]
    try:
        github.find_release("NekoBoxHQ/canoe", "v1.0.29")
        check("★ 指定了 tag 就查 releases/tags/<tag>",
              seen[-1].endswith("/repos/NekoBoxHQ/canoe/releases/tags/v1.0.29"), seen[-1])
        github.find_release("NekoBoxHQ/canoe", "")
        check("★ 不指定 tag 就查 releases/latest",
              seen[-1].endswith("/repos/NekoBoxHQ/canoe/releases/latest"), seen[-1])
    finally:
        github._get_json = real_json

    # --- 10. 从 Release 拉包并发布（pull_from_github）---
    #
    # 这一段是真跑：把"Release 资产"的下载地址指向本地文件（file:// 走的是
    # 标准 urlopen），所以**不联网**也验得到落盘、核对、发布整条链。
    # 面板那个「拉取最新轻舟」和命令行的 `canoe release` 走的就是这个函数。
    print("\n[10] 从 GitHub Release 拉包并发布")
    from canoe_server.database import SessionLocal, init_db  # noqa: PLC0415
    from canoe_server.services.updates import (  # noqa: PLC0415
        ReleaseSourceError,
        pull_from_github,
    )

    # ⚠ 上面那段的 finally 已经把 tmp 整个删了，这里得自己造一个；
    #   releases/ 也在 tmp 底下，一并重建（store_release_file 要往里写）。
    tmp10 = Path(tempfile.mkdtemp(prefix="canoe-pulltest-"))
    releases.mkdir(parents=True, exist_ok=True)

    pkg = tmp10 / "Canoe-9.9.9-win64.zip"
    pkg.write_bytes(b"PK\x05\x06" + b"\0" * 18)          # 空 zip 就够，只看字节
    want_sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
    side = tmp10 / "Canoe-9.9.9-win64.zip.sha256"
    side.write_text(f"{want_sha}  {pkg.name}\n", encoding="utf-8")

    def _asset(path):
        return {"name": path.name, "size": path.stat().st_size,
                "browser_download_url": path.as_uri()}

    fake = {
        "tag_name": "v9.9.9",
        "body": "这一版是拿来测的\n第二行不该被当成更新说明",
        "assets": [_asset(side), _asset(pkg)],
    }

    real_find = github.find_release
    init_db()
    try:
        github.find_release = lambda repo, tag="": fake
        with SessionLocal() as db:
            row, verified = pull_from_github(db, repo="NekoBoxHQ/canoe")
            check("★ 版本号从资产文件名里抠出来", row.version == "9.9.9", row.version)
            check("★ 摘要核对过了（Release 上挂了 .sha256）", verified)
            check("★ 包真的落到了 releases/ 下", (releases / pkg.name).is_file())
            check("★ 发布记录里的 sha256 和本地一致", row.sha256 == want_sha,
                  (row.sha256 or "")[:16])
            check("★ 更新说明取 Release 正文第一行",
                  row.notes == "这一版是拿来测的", repr(row.notes))

            # 摘要对不上 → 整个丢掉，绝不发布
            side.write_text("0" * 64 + "  x\n", encoding="utf-8")
            try:
                pull_from_github(db, repo="NekoBoxHQ/canoe", version="9.9.8")
                check("摘要对不上时应当报错", False)
            except Exception as exc:                       # noqa: BLE001
                check("★ 摘要对不上就整个丢掉（不发布）",
                      type(exc).__name__ == "ReleaseMismatch", type(exc).__name__)

            # 没有 zip 资产 → 给人话
            github.find_release = lambda repo, tag="": {"tag_name": "v9", "assets": []}
            try:
                pull_from_github(db, repo="NekoBoxHQ/canoe")
                check("没有 zip 资产时应当报错", False)
            except ReleaseSourceError as exc:
                check("★ 没有 zip 资产时给的是人话",
                      "package_release.py" in str(exc), str(exc))
    finally:
        github.find_release = real_find
        shutil.rmtree(tmp10, ignore_errors=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
