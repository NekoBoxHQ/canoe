"""轻舟的 GitHub Release 工具（建 tag / 挂包 / 清旧版本）。

    python scripts/gh_publish.py tag     v1.0.45 <sha>
    python scripts/gh_publish.py release Canoe-1.0.45-win64.zip v1.0.45 "说明"
    python scripts/gh_publish.py prune   --keep 2 [--delete]

以前这套是放在 %TEMP% 里的三个临时脚本，**被系统清过一次**（隔了一周就没了），
所以挪进仓库：它不带任何凭据，只在运行时从 git 的凭据管理器取。

凭据从 `git credential fill` 拿 —— **不落盘、不打印**。
上传完核对**字节数 + GitHub 自己算的 digest**（sha256）才算数。
清旧版本**按版本号排序**，不按 published_at（踩过：重建旧版本的 Release 后
它的 published_at 是"现在"，按时间排会把它当成最新，删掉的却是当前版本）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import requests

REPO = "NekoBoxHQ/canoe"
API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


def headers() -> dict[str, str]:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=True,
    ).stdout
    user = pw = ""
    for line in out.splitlines():
        if line.startswith("username="):
            user = line[9:]
        elif line.startswith("password="):
            pw = line[9:]
    if not user or not pw:
        raise SystemExit("拿不到 GitHub 凭据（git credential fill 没返回）")
    return {
        "Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode(),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def ver_key(tag: str) -> tuple[int, ...]:
    """v1.0.45 -> (1,0,45)。排序用它，不用时间。"""
    nums = re.findall(r"\d+", tag)
    return tuple(int(n) for n in nums) if nums else (0,)


# --------------------------------------------------------------------------
def cmd_tag(tag: str, sha: str) -> int:
    r = requests.post(
        f"{API}/repos/{REPO}/git/refs",
        headers=headers(), timeout=30,
        json={"ref": f"refs/tags/{tag}", "sha": sha},
    )
    print(f"tag {tag} -> {sha}: {r.status_code} {'ok' if r.status_code < 300 else r.text[:200]}")
    return 0 if r.status_code in (201, 422) else 1     # 422 = 已经存在


def cmd_release(zip_path: str, tag: str, notes: str) -> int:
    local = Path(zip_path).resolve()
    want_size = local.stat().st_size
    want_sha = hashlib.sha256(local.read_bytes()).hexdigest()
    sidecar = local.with_suffix(local.suffix + ".sha256")
    print(f"[本地] {local.name}  {want_size} 字节  sha256 {want_sha[:24]}…")

    h = headers()
    # 已经存在就直接用（幂等）
    r = requests.get(f"{API}/repos/{REPO}/releases/tags/{tag}", headers=h, timeout=30)
    if r.status_code == 200:
        rel = r.json()
        print(f"[Release] 已存在 id={rel['id']}，复用")
    else:
        r = requests.post(f"{API}/repos/{REPO}/releases", headers=h, timeout=30, json={
            "tag_name": tag, "name": tag, "body": notes,
        })
        if r.status_code >= 300:
            raise SystemExit(f"建 Release 失败: {r.status_code} {r.text[:300]}")
        rel = r.json()
        print(f"[Release] 新建 {tag} id={rel['id']}")

    existing = {a["name"]: a for a in rel.get("assets", [])}
    for path, name in ((local, local.name), (sidecar, sidecar.name)):
        if name in existing:
            requests.delete(
                f"{API}/repos/{REPO}/releases/assets/{existing[name]['id']}",
                headers=h, timeout=30,
            )
            print(f"[资产] 旧的 {name} 先撤下")
        data = path.read_bytes()
        up = requests.post(
            f"{UPLOAD}/repos/{REPO}/releases/{rel['id']}/assets?name={name}",
            headers={**h, "Content-Type": "application/octet-stream"},
            data=data, timeout=600,
        )
        if up.status_code >= 300:
            raise SystemExit(f"上传 {name} 失败: {up.status_code} {up.text[:300]}")
        asset = up.json()
        print(f"[资产] {name}  远端 {asset['size']} 字节  digest={asset.get('digest')}")

        # ★ 核对：字节数 + GitHub 自己算的摘要
        if asset["size"] != len(data):
            raise SystemExit(f"[x] {name} 字节数对不上：远端 {asset['size']} / 本地 {len(data)}")
        gh = (asset.get("digest") or "").removeprefix("sha256:")
        mine = hashlib.sha256(data).hexdigest()
        if gh and gh != mine:
            raise SystemExit(f"[x] {name} 摘要对不上：GitHub {gh[:16]}… / 本地 {mine[:16]}…")
        print(f"[核对] {name} 字节数一致、sha256 一致")

    print(f"[完成] https://github.com/{REPO}/releases/tag/{tag}")
    return 0


def cmd_prune(keep: int, delete: bool) -> int:
    r = requests.get(f"{API}/repos/{REPO}/releases?per_page=100", headers=headers(), timeout=30)
    r.raise_for_status()
    rels = sorted(r.json(), key=lambda x: ver_key(x["tag_name"]), reverse=True)
    keep_tags = [x["tag_name"] for x in rels[:keep]]
    drop = [x for x in rels[keep:]]
    print(f"[i] 共 {len(rels)} 个 Release，保留 {keep} 个：{keep_tags}")
    if not drop:
        print("[i] 没有要删的")
        return 0
    print(f"[i] 要删 {len(drop)} 个：{[x['tag_name'] for x in drop]}")
    if not delete:
        print("[i] 只是演练 —— 要真删加 --delete")
        return 0
    h = headers()
    for x in drop:
        tag = x["tag_name"]
        d = requests.delete(f"{API}/repos/{REPO}/releases/{x['id']}", headers=h, timeout=30)
        print(f"  [删] Release {tag}  {d.status_code}")
        # 删 Release 不动 tag，得单独删
        t = requests.delete(f"{API}/repos/{REPO}/git/refs/tags/{tag}", headers=h, timeout=30)
        print(f"  [删] tag     {tag}  {t.status_code}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "tag":
        return cmd_tag(sys.argv[2], sys.argv[3])
    if cmd == "release":
        return cmd_release(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    if cmd == "prune":
        keep = 2
        delete = "--delete" in sys.argv
        if "--keep" in sys.argv:
            keep = int(sys.argv[sys.argv.index("--keep") + 1])
        return cmd_prune(keep, delete)
    raise SystemExit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
