"""把服务端下发的入口参数转成 sing-box 出站。

★ 这是"客户端拿不到真实节点"在客户端的落点 ★

    客户端只知道**中转层入口**（域名 + 端口 + 一个 UUID + WS 路径），
    真实节点的地址/端口/协议/密钥它根本拿不到 —— 服务端也不会发
    （响应类型 ConfigResponse 里就没有容得下它们的字段）。

入口按设计是 VLESS + WebSocket (+TLS)：

    客户端 ──VLESS+WS+TLS──► 中转层 sing-box ──真实协议──► 真实节点

中转层那边的入站配置由服务端渲染（services/relay.py），两边要对得上。
改这里之前先看一眼那个文件。
"""
from __future__ import annotations

from typing import Any

from canoe_core import EntryPayload


class EntryError(Exception):
    """入口参数有问题，没法拼出可用的出站。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "bad_entry"
        self.message = message


def build_entry_outbound(entry: EntryPayload, tag: str = "proxy") -> dict[str, Any]:
    """把入口参数拼成 sing-box 的 vless 出站。

    只用到 EntryPayload 里白名单那几个字段 —— 它上面本来也没有别的。
    """
    if not entry.host:
        raise EntryError("服务端没给入口地址，请联系管理员")
    if not entry.uuid:
        raise EntryError("服务端没给入口凭证，请联系管理员")

    outbound: dict[str, Any] = {
        "type": "vless",
        "tag": tag,
        "server": entry.host,
        "server_port": int(entry.port),
        "uuid": entry.uuid,
    }

    if entry.transport == "ws":
        transport: dict[str, Any] = {"type": "ws", "path": entry.path or "/"}
        # 中转层是 Nginx 按路径分流的，Host 头必须带上入口域名
        if entry.sni or entry.host:
            transport["headers"] = {"Host": entry.sni or entry.host}
        outbound["transport"] = transport
    elif entry.transport == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": (entry.path or "").lstrip("/"),
        }
    elif entry.transport != "tcp":
        raise EntryError(f"不认识的入口传输方式：{entry.transport}")

    if entry.tls:
        tls: dict[str, Any] = {
            "enabled": True,
            "server_name": entry.sni or entry.host,
            "insecure": bool(entry.insecure),
        }
        outbound["tls"] = tls

    return outbound


def entry_label(entry: EntryPayload) -> str:
    """给界面/日志看的入口描述。

    ⚠ 只在**出错提示**里用，而且要先打码 —— 界面不显示节点信息。
    """
    return f"{entry.host}:{entry.port}"
