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
"""
from __future__ import annotations

import hashlib
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

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
