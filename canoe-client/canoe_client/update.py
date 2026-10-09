"""检查客户端更新。

从配置里的地址拉一个 JSON，和当前版本比大小：

    { "version": "1.1.0", "url": "https://.../Canoe-1.1.0-win64.zip",
      "notes": "修复 TUN 快速重连卡顿" }

阶段1 还没有服务端，所以这个地址**默认是空的**，按钮会提示「未配置更新地址」。
阶段4 把它指到自己的服务器即可，不需要改代码。

不引第三方版本比较库 —— 三段式版本号手写十几行就够，少一个依赖。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

TIMEOUT = 15


@dataclass
class UpdateInfo:
    latest: str
    current: str
    url: str = ""
    notes: str = ""

    @property
    def is_newer(self) -> bool:
        return compare_versions(self.latest, self.current) > 0


class UpdateError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_version(text: str) -> tuple[int, ...]:
    """从 'v1.2.3-beta' 里取出 (1, 2, 3)。取不到就返回空元组。"""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3])


def compare_versions(a: str, b: str) -> int:
    """a > b 返回 1，相等 0，小于 -1。位数不齐按 0 补。"""
    pa, pb = list(parse_version(a)), list(parse_version(b))
    if not pa or not pb:
        return 0
    width = max(len(pa), len(pb))
    pa += [0] * (width - len(pa))
    pb += [0] * (width - len(pb))
    return (pa > pb) - (pa < pb)


def check(url: str, current: str) -> UpdateInfo:
    """拉取更新信息。url 为空或格式不对会抛 UpdateError。"""
    if not url or not url.strip():
        raise UpdateError("未配置更新地址（阶段4 部署服务端后填入）")

    try:
        resp = requests.get(url.strip(), timeout=TIMEOUT,
                            headers={"User-Agent": "Canoe-Client/1.0"})
    except requests.exceptions.SSLError as exc:
        raise UpdateError(f"TLS 握手失败：{exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise UpdateError(f"连不上更新地址：{exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise UpdateError("更新地址响应超时") from exc

    if resp.status_code >= 400:
        raise UpdateError(f"更新地址返回 HTTP {resp.status_code}")

    try:
        data = json.loads(resp.text)
    except ValueError as exc:
        raise UpdateError("更新信息不是合法 JSON") from exc

    if not isinstance(data, dict) or not data.get("version"):
        raise UpdateError("更新信息里没有 version 字段")

    return UpdateInfo(
        latest=str(data["version"]),
        current=current,
        url=str(data.get("url", "")),
        notes=str(data.get("notes", "")),
    )
