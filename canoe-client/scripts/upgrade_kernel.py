"""把 bin/sing-box.exe 换成官方新版本。

    python scripts/upgrade_kernel.py              # 升到最新
    python scripts/upgrade_kernel.py --version 1.15.0
    python scripts/upgrade_kernel.py --check      # 只看有没有新版，什么都不动
    python scripts/upgrade_kernel.py --from <zip> # 用已经下好的 zip（国内下不动时）
    python scripts/upgrade_kernel.py --force      # 同版本也重装一遍

它替你做完这几件事，顺序不能反：

    1. 从 GitHub Releases 拿 asset（只认 `-windows-amd64.zip`，不认 legacy）
    2. **核对摘要** —— 跟 GitHub 自己算的 digest 比，对不上直接扔
    3. 只从 zip 里取 `sing-box.exe`（包里的 libcronet.dll 不要，见下）
    4. 让新内核自报一句版本，跟预期对不上就停
    5. 备份现在这份到 %TEMP%（**不能备份在 bin/ 里** —— 见下），再覆盖
    6. 真跑一遍 `tests/test_config.py`（它拿新内核跑 `sing-box check`），
       **跑不过就自动把旧内核换回去**

★ 为什么备份放 %TEMP% 而不是 bin/：`canoe.spec` 是把 `bin/` **整个递归**打进
  客户端里的。在那儿留一个 `sing-box.exe.bak`，用户那份 83MB 的安装包就白白
  多背 80MB —— 而且没人会发现。

★ 为什么只取 sing-box.exe：官方 zip 里还有个 9.5MB 的 libcronet.dll，那是
  cronet 传输（一种 HTTP 客户端引擎）才要的可选件，本客户端的配置生成器
  永远不生成 cronet。实测不在 bin/ 里内核照常 check、照常启动。

★ 为什么最后一定要跑 test_config：配置写法（字段名、route.rules 的动作名）
  是跟着内核版本走的。换完内核 `sing-box check` 报错的话，用户那边就是
  "更新完启航就报错"—— 在这儿红总比在用户那儿红好。（大版本升级时它只是
  及格线：回国那套规则、TUN 那条路最好再实连一次。）
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
CLIENT = HERE.parent
KERNEL = CLIENT / "bin" / "sing-box.exe"

REPO = "SagerNet/sing-box"
API = "https://api.github.com"
UA = {"User-Agent": "Canoe-Upgrade-Kernel/1.0"}


def headers() -> dict[str, str]:
    """带上 token（有的话）。

    不带 token 走匿名：每小时每个 IP 只有 60 次 —— 一天里多查几次版本、
    或者几个人共用一个出口 IP，就会撞上 `403 rate limit exceeded`。
    设一个 `GITHUB_TOKEN`（只读的 public repo 权限就够）能到 5000 次。
    **仓库里不放任何口令**，这个是环境变量。
    """
    h = dict(UA)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """跑一条命令，输出统一按 UTF-8 收。

    ⚠ Windows 上 `text=True` 默认按**系统 ANSI 代码页**（这边是 GBK）解码，
      子进程只要吐出一个非 GBK 的字节，读取线程就抛 UnicodeDecodeError ——
      症状是"测试莫名其妙没过"，其实是解码炸了，跟测试一点关系都没有。
      （写这个脚本的第一版就踩了：明明 test_config 自己跑是全绿的。）
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        timeout=kw.pop("timeout", 120), **kw,
    )


def installed_version() -> str:
    """现在 bin/ 里那个内核报的版本（没有就空串）。"""
    if not KERNEL.is_file():
        return ""
    try:
        out = run([str(KERNEL), "version"]).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"sing-box version (\S+)", out)
    return m.group(1) if m else ""


def api(path: str) -> dict:
    req = urllib.request.Request(f"{API}{path}", headers=headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_asset(release: dict) -> dict:
    """挑 windows-amd64 那个 asset。

    ⚠ 有个 `-legacy-windows-7.zip` 也含 "windows-amd64" 这个词，
      它是给 Win7 的老构建。精确匹配文件名，别用 in。
    """
    want = f"sing-box-{release['tag_name'].lstrip('v')}-windows-amd64.zip"
    for a in release.get("assets", []):
        if a["name"] == want:
            return a
    raise SystemExit(f"[x] {release['tag_name']} 里没有 {want}")


def download(url: str, dest: Path) -> None:
    print(f"  [↓] {url}")
    req = urllib.request.Request(url, headers=headers())
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r      {got / 1048576:.1f}/{total / 1048576:.1f} MB", end="", flush=True)
    print()


def verify_zip(zip_path: Path, asset: dict) -> None:
    """核对摘要：字节数 + GitHub 自己算的 digest。对不上就扔。"""
    want = (asset.get("digest") or "").removeprefix("sha256:")
    data = zip_path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    print(f"  [i] sha256 {got[:24]}…  大小 {len(data)} 字节")
    if len(data) != asset["size"]:
        raise SystemExit("[x] 字节数跟 GitHub 报的对不上，包是残的，扔掉")
    if want and got != want:
        raise SystemExit(f"[x] sha256 对不上！\n    期望 {want}\n    实际 {got}")
    print("  [ok] 摘要与 GitHub 自己算的一致")


def verify_against_release(zip_path: Path, ver: str) -> None:
    """`--from` 走本地 zip 时，尽量拿 GitHub 的 digest 比一下。

    这条路的人十有八九是"国内连不上 API 才自己下的"，所以连不上**不算错**
    —— 但要把算出来的 sha256 打出来，让他对着 Release 页面自己比一眼。
    不声不响地把一个来路不明的内核装进去，是这件事里最不能接受的做法：
    它会跟着安装包发给每一个用户。
    """
    if not ver:
        print("[!] 文件名里看不出是哪个版本，跳过摘要核对 —— 自己确认一下来源")
        return
    try:
        verify_zip(zip_path, find_asset(api(f"/repos/{REPO}/releases/tags/v{ver}")))
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, SystemExit) as exc:
        data = zip_path.read_bytes()
        print(f"[!] 没法自动核对（{exc}）—— 请自己跟 Release 页面对一遍：")
        print(f"    {hashlib.sha256(data).hexdigest()}  {len(data)} 字节")


def extract_exe(zip_path: Path, into: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith("sing-box.exe")]
        if not names:
            raise SystemExit(f"[x] {zip_path.name} 里没有 sing-box.exe")
        out = into / "sing-box.exe"
        with z.open(names[0]) as src, out.open("wb") as dst:
            dst.write(src.read())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="升级 bin/sing-box.exe")
    ap.add_argument("--version", help="指定版本（默认拿最新）")
    ap.add_argument("--from", dest="local_zip", help="用本地已下好的 zip，不联网")
    ap.add_argument("--check", action="store_true", help="只报告，不动任何文件")
    ap.add_argument("--force", action="store_true", help="同版本也重装")
    ap.add_argument("--no-test", action="store_true", help="跳过 test_config（不建议）")
    args = ap.parse_args()

    if os.name != "nt":
        print("[!] 本脚本面向 Windows（目标是 sing-box.exe），当前系统可能不适用")

    cur = installed_version()
    print(f"[i] 当前内核：{cur or '（没有）'}")

    # ---- 1. 找到要装的那个包 ----
    tmp = Path(tempfile.mkdtemp(prefix="canoe-kernel-"))
    # ★ 工作目录用完必须删：里头躺着一个 32MB 的 zip 和 80MB 的解包内核。
    #   写这个脚本的第一版就忘了 —— 跑了 7 次，%TEMP% 里攒了 157MB。
    #   （跟 test_release 那个"删之前没 dispose"是同一类病：临时目录不清、
    #     又没人会去看它。）备份是**另一个目录**，不受这行影响。
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    if args.local_zip:
        zip_path = Path(args.local_zip).resolve()
        if not zip_path.is_file():
            raise SystemExit(f"[x] 找不到 {zip_path}")
        print(f"[i] 用本地包：{zip_path.name}")
        m = re.search(r"sing-box-(\d+\.\d+\.\d+)-windows-amd64\.zip$", zip_path.name)
        verify_against_release(zip_path, m.group(1) if m else "")
    else:
        try:
            rel = api(f"/repos/{REPO}/releases/tags/v{args.version}") if args.version \
                else api(f"/repos/{REPO}/releases/latest")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SystemExit(f"[x] 没有 v{args.version} 这个版本 —— 版本号写错了吧")
            hint = "（匿名查询每小时只有 60 次，设个 GITHUB_TOKEN 能到 5000）" \
                if exc.code == 403 else ""
            raise SystemExit(f"[x] GitHub 拒绝了这个查询：{exc} {hint}")
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"[x] 连不上 GitHub（{exc}）。\n"
                f"    国内经常这样。可以自己下好 zip 再跑：\n"
                f"    python scripts/upgrade_kernel.py --from <zip 路径>"
            )
        tag = rel["tag_name"]
        new_ver = tag.lstrip("v")
        if cur == new_ver and not args.force:
            print(f"[=] 已经是最新的 {cur}，什么都没做（想重装加 --force）")
            return 0
        asset = find_asset(rel)
        print(f"[i] 目标 {tag}：{asset['name']}（{asset['size'] / 1048576:.1f} MB）")
        if args.check:
            print("[=] --check：只看，不下载。")
            return 0
        zip_path = tmp / asset["name"]
        download(asset["browser_download_url"], zip_path)

        # ---- 2. 核对摘要 ----
        verify_zip(zip_path, asset)

    # ---- 3. 取出 exe，先让它自报版本 ----
    new_exe = extract_exe(zip_path, tmp)
    out = run([str(new_exe), "version"]).stdout
    m = re.search(r"sing-box version (\S+)", out)
    if not m:
        raise SystemExit(f"[x] 取出来的 sing-box.exe 跑不起来：{out[:200]}")
    new_ver = m.group(1)
    print(f"[i] 新内核自报版本：{new_ver}")
    if cur == new_ver and not args.force:
        print(f"[=] 跟现在这份一样（{cur}），什么都没做")
        return 0

    # ---- 4. 备份旧的（放 %TEMP%，别放 bin/ —— 会被打进安装包）----
    backup_dir = Path(tempfile.gettempdir()) / "canoe-kernel-backup"
    backup_dir.mkdir(exist_ok=True)
    backup = None
    if KERNEL.is_file():
        backup = backup_dir / f"sing-box-{cur or 'unknown'}.exe"
        backup.write_bytes(KERNEL.read_bytes())
        print(f"[i] 旧内核备份在 {backup}")

    KERNEL.parent.mkdir(parents=True, exist_ok=True)
    KERNEL.write_bytes(new_exe.read_bytes())
    print(f"[ok] 已换上 {new_ver}（{KERNEL}）")

    # ---- 5. 让新内核过一遍配置检查，不过就换回去 ----
    if args.no_test:
        print("[!] --no-test：跳过了 test_config —— 换完内核不跑它等于没验")
    else:
        print("[*] 拿新内核跑 tests/test_config.py …")
        p = run([sys.executable, str(CLIENT / "tests" / "test_config.py")], timeout=300)
        tail = (p.stdout or "").strip().splitlines()[-3:]
        for line in tail:
            print("    " + line)
        if p.returncode != 0 or "失败 0 项" not in (p.stdout or ""):
            if backup and backup.is_file():
                KERNEL.write_bytes(backup.read_bytes())
                print(f"[x] 配置检查没过 —— 已自动换回 {cur}（备份 {backup}）")
            else:
                print("[x] 配置检查没过，而且没有备份可换回")
            return 1
        print("[ok] 配置检查通过")

    print()
    print("下一步：")
    print(f"  1. 提交 bin/sing-box.exe（⚠ 它是 gitignore 的，得加 -f）和文档里的版本号")
    print(f"  2. 提客户端版本号 -> pyinstaller canoe.spec -> scripts/package_release.py")
    print(f"  3. 打出来的 exe 用 --selftest 确认 singbox_version 是 {new_ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
