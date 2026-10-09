"""从 GitHub Release 取客户端安装包。

开发机把打包好的 zip 挂到 GitHub Release 上（tag 形如 `v1.0.29`），服务端
**自己去拉** —— 那 83MB 不再从开发机往服务器上传。

为什么非改不可：上传那条路断过两次，/tmp 里留下 46MB 的半截包，而
`store_release_file` 是按**落盘的字节**算 sha256 的 —— 残包自洽，于是被当成
合法版本发了出去，客户端装上才发现 exe 是残的。走 Release 之后，"这份包是
什么"由 GitHub 保证，而摘要是开发机本地算好、当作资产一起挂上去的
（`.sha256`），跟下载这条链路无关 —— 对不上就整个丢掉。

仓库是 public，所以这里**不需要任何凭据**。也正因为不需要，服务端才敢在
管理面板上一次点击就把包拉回来。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

API = "https://api.github.com"
#: 元数据请求的超时（秒）。下载大包走的是另一个 socket，超时按每次读写算，
#: 所以 83MB 不会被这个值卡住。
TIMEOUT = 30
UA = "canoe-server"


class GithubError(Exception):
    """拉取失败。message 是直接写给管理员看的，别塞栈。"""


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GithubError(
                "GitHub 上找不到这个 Release —— 仓库名对不对？开发机那边把包挂上去了吗？"
            ) from exc
        if exc.code in (403, 429):
            raise GithubError(
                "GitHub 拒绝了这次请求。未登录的调用每个 IP 每小时只有 60 次，"
                "大概是用完了，过一会儿再试。"
            ) from exc
        raise GithubError(f"GitHub 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GithubError(f"连不上 GitHub：{exc}") from exc
    except ValueError as exc:
        raise GithubError("GitHub 返回的不是合法 JSON") from exc


def find_release(repo: str, tag: str = "") -> dict:
    """取一个 Release。tag 留空 = 最新那个（草稿和预发布不算）。"""
    tag = (tag or "").strip()
    if tag:
        return _get_json(f"{API}/repos/{repo}/releases/tags/{tag}")
    return _get_json(f"{API}/repos/{repo}/releases/latest")


def read_sidecar(url: str) -> str:
    """读 `<包名>.sha256` 资产，取里面的摘要。没有或读不出来就返回空串。

    里面是 `sha256sum` 那种标准格式：`<64位摘要>  <文件名>`。
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    return m.group(1).lower() if m else ""


def pick_assets(release: dict) -> tuple[dict, str]:
    """从 Release 里挑出安装包，顺带把旁边那份 `.sha256` 读出来。

    返回 (zip 资产, 摘要)；摘要是空串表示没挂 .sha256 —— 那样就没法核对，
    调用方自己决定要不要拦。
    """
    assets = release.get("assets") or []
    zips = [a for a in assets
            if str(a.get("name", "")).lower().endswith(".zip") and a.get("browser_download_url")]
    if not zips:
        raise GithubError(
            "这个 Release 里没有 .zip 资产 —— 开发机那边先跑 "
            "scripts/package_release.py，再把 zip 挂上来。"
        )
    # 挂了多个就取最大的那个（.sha256 之类的小文件不会撞上）
    asset = max(zips, key=lambda a: int(a.get("size") or 0))

    digest = ""
    for other in assets:
        if other.get("name") == str(asset.get("name")) + ".sha256":
            digest = read_sidecar(other["browser_download_url"])
            break
    return asset, digest


def open_asset(asset: dict):
    """打开安装包的数据流，给 `store_release_file` 用。

    返回的东西按上下文管理器用（它有 `.read(n)`）。
    GitHub 的下载地址会 302 到它自己的 CDN，urlopen 默认跟着跳。
    """
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=TIMEOUT)
