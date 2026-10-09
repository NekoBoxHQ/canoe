"""节点链接 -> sing-box 出站。

订阅栏里贴的就是这种东西，一行一个：

    ss://2022-blake3-aes-128-gcm:<server_key>:<user_key>@host:port#名称
    vmess://<base64 的 JSON>
    vless://<uuid>@host:port?security=tls&type=ws&path=/x#名称
    trojan://<密码>@host:port?security=tls&sni=x#名称

也接受整体 base64 的订阅（机场常见做法）—— 解开后还是这些行。

解析规则按各家实现里事实上的通用做法来，宁可宽容一点：
认不出的行跳过并记下来，而不是整份订阅直接失败 —— 一份订阅里
混着一条新格式的链接，不该让用户整个用不了。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

#: 认得的协议
SCHEMES = ("ss", "vmess", "vless", "trojan")


class LinkError(Exception):
    pass


@dataclass
class NodeLink:
    """一个解析好的节点。"""

    name: str                 # 显示名（链接里 # 后面那段）
    outbound: dict[str, Any]  # sing-box 出站（不含 tag）
    raw: str = ""             # 原始链接，只在排错时用

    @property
    def summary(self) -> str:
        """给日志/测试看的一行，**不含**密钥。"""
        ob = self.outbound
        return f"{self.name}（{ob.get('type')} {ob.get('server')}:{ob.get('server_port')}）"


@dataclass
class ParseResult:
    links: list[NodeLink] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # 认不出的行（原文）

    @property
    def ok(self) -> bool:
        return bool(self.links)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _b64(text: str) -> bytes:
    """宽松的 base64 解码：补 padding、兼容 urlsafe。"""
    s = re.sub(r"\s+", "", text or "")
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s)
    except (binascii.Error, ValueError) as exc:
        raise LinkError("不是合法的 base64") from exc


def _q(query: dict[str, list[str]], key: str, default: str = "") -> str:
    return (query.get(key) or [default])[0]


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "tls", "reality"}


def _split_hostport(netloc: str) -> tuple[str, int]:
    """host:port。IPv6 会写成 [::1]:443。"""
    try:
        parts = urlsplit(f"//{netloc}")
        host = parts.hostname or ""
        port = parts.port or 0
    except ValueError as exc:
        raise LinkError("地址部分解析不了") from exc
    if not host or not port:
        raise LinkError("链接里没有 host 或 port")
    return host, port


def _apply_transport(out: dict[str, Any], kind: str, query: dict[str, list[str]]) -> None:
    """把 ?type=ws/grpc/tcp 那套翻译成 sing-box 的 transport。"""
    kind = (kind or "tcp").lower()
    if kind == "ws":
        transport: dict[str, Any] = {"type": "ws", "path": _q(query, "path", "/") or "/"}
        host = _q(query, "host")
        if host:
            transport["headers"] = {"Host": host}
        out["transport"] = transport
    elif kind == "grpc":
        out["transport"] = {
            "type": "grpc",
            "service_name": _q(query, "serviceName") or _q(query, "service_name"),
        }
    elif kind in ("http", "h2"):
        out["transport"] = {"type": "http", "host": [_q(query, "host")] if _q(query, "host") else []}
    # tcp / 其它：不加 transport，sing-box 默认就是 tcp


def _apply_tls(out: dict[str, Any], security: str, query: dict[str, list[str]], host: str) -> None:
    security = (security or "").lower()
    if security in ("tls", "reality"):
        sni = _q(query, "sni") or _q(query, "peer") or host
        tls: dict[str, Any] = {"enabled": True, "server_name": sni}
        if _q(query, "allowInsecure") in ("1", "true"):
            tls["insecure"] = True
        fp = _q(query, "fp") or _q(query, "fingerprint")
        if fp:
            tls["utls"] = {"enabled": True, "fingerprint": fp}
        out["tls"] = tls
    elif security in ("xtls", "none", ""):
        pass


# --------------------------------------------------------------------------
# 各协议
# --------------------------------------------------------------------------


def _parse_ss(link: str) -> NodeLink:
    body = link[len("ss://"):]
    frag = ""
    if "#" in body:
        body, frag = body.split("#", 1)

    if "@" not in body:
        # 老格式：整段 base64，里面是 method:password@host:port
        decoded = _b64(body).decode("utf-8", "replace")
        if "@" not in decoded:
            raise LinkError("ss 链接里既没有 @ 也不是合法的 base64")
        body = decoded

    userinfo, netloc = body.rsplit("@", 1)
    # 新格式（SIP002）：userinfo 可能是 base64(method:password)，也可能是
    # 直接的 method:password。判据就一个：里面有没有冒号。
    #
    # ⚠ 这里不能"先试 base64 再退回" —— "abcd:efgh" 不带冒号的情况不存在，
    #   但 "AAAA" 这种纯 base64 字符集的东西两种解释都成立，先试 base64
    #   会把 method 解成乱码。有冒号就一定不是 base64。
    if ":" in userinfo:
        method, _, password = unquote(userinfo).partition(":")
    else:
        decoded = _b64(userinfo).decode("utf-8", "replace")
        method, _, password = decoded.partition(":")
    if not method or not password:
        raise LinkError("ss 链接里缺 method 或 password")

    host, port = _split_hostport(netloc)
    out = {
        "type": "shadowsocks",
        "server": host,
        "server_port": port,
        "method": method,
        "password": password,
    }
    return NodeLink(name=unquote(frag) or f"{host}:{port}", outbound=out, raw=link)


def _parse_vmess(link: str) -> NodeLink:
    payload = link[len("vmess://"):].strip()
    try:
        data = json.loads(_b64(payload).decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LinkError("vmess 里的 base64 不是 JSON") from exc
    if not isinstance(data, dict):
        raise LinkError("vmess 的 JSON 不是对象")

    host = str(data.get("add") or "")
    try:
        port = int(data.get("port") or 0)
    except (TypeError, ValueError) as exc:
        raise LinkError("vmess 的端口不是数字") from exc
    if not host or not port:
        raise LinkError("vmess 缺 add 或 port")

    out: dict[str, Any] = {
        "type": "vmess",
        "server": host,
        "server_port": port,
        "uuid": str(data.get("id") or ""),
        "security": str(data.get("scy") or "auto") or "auto",
        "alter_id": int(data.get("aid") or 0),
    }
    if not out["uuid"]:
        raise LinkError("vmess 缺 id")

    query: dict[str, list[str]] = {}
    if data.get("host"):
        query["host"] = [str(data["host"])]
    if data.get("path"):
        query["path"] = [str(data["path"])]
    if data.get("sni"):
        query["sni"] = [str(data["sni"])]
    _apply_transport(out, str(data.get("net") or "tcp"), query)
    _apply_tls(out, "tls" if _truthy(str(data.get("tls") or "")) else "", query, host)

    name = str(data.get("ps") or "").strip() or f"{host}:{port}"
    return NodeLink(name=name, outbound=out, raw=link)


def _parse_url_scheme(link: str, scheme: str) -> NodeLink:
    parts = urlsplit(link)
    query = parse_qs(parts.query)

    host, port = _split_hostport(parts.netloc.rpartition("@")[2])
    userinfo = unquote(parts.username or "")
    if scheme == "trojan":
        if not userinfo:
            raise LinkError("trojan 缺密码")
        out: dict[str, Any] = {
            "type": "trojan",
            "server": host,
            "server_port": port,
            "password": userinfo,
        }
    else:  # vless
        if not userinfo:
            raise LinkError("vless 缺 uuid")
        out = {
            "type": "vless",
            "server": host,
            "server_port": port,
            "uuid": userinfo,
        }
        flow = _q(query, "flow")
        if flow:
            out["flow"] = flow

    _apply_transport(out, _q(query, "type", "tcp"), query)
    _apply_tls(out, _q(query, "security"), query, host)

    name = unquote(parts.fragment) or f"{host}:{port}"
    return NodeLink(name=name, outbound=out, raw=link)


_PARSERS = {
    "ss": _parse_ss,
    "vmess": _parse_vmess,
    "vless": lambda link: _parse_url_scheme(link, "vless"),
    "trojan": lambda link: _parse_url_scheme(link, "trojan"),
}


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def split_lines(text: str) -> list[str]:
    """订阅文本 -> 链接行。

    若整段是 base64（机场常见），先解开再分行。判据：不含任何已知协议
    前缀，但能 base64 解出含协议前缀的内容。
    """
    raw = (text or "").strip()
    if not raw:
        return []

    has_scheme = any(f"{s}://" in raw for s in SCHEMES)
    if not has_scheme:
        try:
            decoded = _b64(raw).decode("utf-8", "replace")
        except LinkError:
            decoded = ""
        if any(f"{s}://" in decoded for s in SCHEMES):
            raw = decoded

    return [line.strip() for line in raw.splitlines() if line.strip()]


def parse(text: str) -> ParseResult:
    """整份订阅 -> 节点列表。认不出的行进 skipped，不抛。"""
    result = ParseResult()
    for line in split_lines(text):
        scheme = line.split("://", 1)[0].lower() if "://" in line else ""
        parser = _PARSERS.get(scheme)
        if parser is None:
            result.skipped.append(line)
            continue
        try:
            result.links.append(parser(line))
        except (LinkError, ValueError, TypeError):
            result.skipped.append(line)
    return result


def pick(text: str) -> NodeLink | None:
    """从订阅里挑一个节点。

    保持客户端极简，不提供节点列表，所以就是第一个能解析出来的。
    """
    result = parse(text)
    return result.links[0] if result.links else None


#: 给 canoe_core 顶层用的别名。`parse` / `pick` 太泛，直接摆在包的
#: 命名空间里会让人摸不着头脑（`from canoe_core import parse` 解析啥？）。
parse_links = parse
pick_link = pick


def find_leaks(payload: Any) -> list[str]:
    """递归找出一份**已序列化**的响应里有没有节点链接。

    节点只走 /api/subscription 的加密信封。别的接口（config / me / 心跳 /
    hello / 客户端更新……）明文里出现一行 `ss://…` 就是泄漏。
    返回命中的路径，空列表表示干净。
    """
    hits: list[str] = []
    stack: list[tuple[Any, str]] = [(payload, "$")]
    while stack:
        cur, path = stack.pop()
        if isinstance(cur, dict):
            stack.extend((v, f"{path}.{k}") for k, v in cur.items())
        elif isinstance(cur, (list, tuple)):
            stack.extend((v, f"{path}[{i}]") for i, v in enumerate(cur))
        elif isinstance(cur, str):
            for scheme in SCHEMES:
                if f"{scheme}://" in cur:
                    hits.append(f"{path} 含 {scheme}://")
                    break
    return hits


def assert_no_leaks(payload: Any, where: str = "响应") -> None:
    """出网前的自检：明文响应里一旦出现节点链接就直接炸，而不是静默泄漏。"""
    leaks = find_leaks(payload)
    if leaks:
        raise LinkError(f"{where}里泄漏了节点信息：{'、'.join(leaks[:3])}")
