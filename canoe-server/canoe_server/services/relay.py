"""从中转层渲染 sing-box 配置。

★★ 真实节点（real_*）的另一个、也是最后一个读取点 ★★

生成的结构：
    inbound  entry-<id>   VLESS + WS (+TLS)，客户端连这里
    outbound node-<id>    指向真实节点（真实 IP/端口/UUID 只出现在这）
    route    inbound → outbound 一对一把两者绑起来

关于端口
    sing-box 的多个入站不能共用同一个 listen_port。所以推荐做法是
    Nginx 在 443 终止 TLS，按 WS 路径反代到本机 20000+ 的不同端口。
    见 canoe-server/relay/nginx.example.conf。

见 docs/01-architecture.md 第 3 节。
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from ..config import settings
from ..models import Node


def _entry_tag(node: Node) -> str:
    return f"entry-{node.id}"


def _node_tag(node: Node) -> str:
    return f"node-{node.id}"


def internal_port(index: int, node: Node) -> int:
    if settings.relay_behind_nginx:
        return settings.relay_internal_base + index + 1
    return node.entry_port or settings.relay_port


def _entry_inbound(node: Node, index: int) -> dict:
    transport: dict = {"type": node.entry_transport}
    if node.entry_transport == "ws":
        transport["path"] = node.entry_path or f"/e/n{node.id}"
    elif node.entry_transport == "grpc":
        transport["service_name"] = (node.entry_path or f"n{node.id}").lstrip("/")

    listen = "127.0.0.1" if settings.relay_behind_nginx else settings.relay_listen

    inbound: dict = {
        "type": "vless",
        "tag": _entry_tag(node),
        "listen": listen,
        "listen_port": internal_port(index, node),
        "users": [{"uuid": node.entry_uuid, "flow": ""}],
        "transport": transport,
    }

    # 只有直连模式才由 sing-box 自己终止 TLS；Nginx 前置时 TLS 归 Nginx
    if node.entry_tls and not settings.relay_behind_nginx:
        inbound["tls"] = {
            "enabled": True,
            "server_name": node.entry_sni or node.entry_host,
            "certificate_path": settings.relay_tls_cert,
            "key_path": settings.relay_tls_key,
        }
    return inbound


def _real_outbound(node: Node) -> dict:
    """真实节点出站。这里的每个字段都是保密的。"""
    extra = node.real_extra or {}
    out: dict = {
        "type": node.real_protocol,
        "tag": _node_tag(node),
        "server": node.real_host,
        "server_port": node.real_port,
    }

    if node.real_protocol == "vless":
        out["uuid"] = node.real_uuid
        if node.real_flow:
            out["flow"] = node.real_flow
    elif node.real_protocol == "vmess":
        out["uuid"] = node.real_uuid
        out["security"] = extra.get("security", "auto")
        out["alter_id"] = extra.get("alter_id", 0)
    elif node.real_protocol == "trojan":
        out["password"] = node.real_uuid
    elif node.real_protocol == "shadowsocks":
        out["method"] = extra.get("method", "aes-128-gcm")
        out["password"] = node.real_uuid
    else:
        raise ValueError(f"不支持的协议: {node.real_protocol}")

    if node.real_network == "ws":
        transport: dict = {"type": "ws", "path": node.real_ws_path or "/"}
        if node.real_ws_host:
            transport["headers"] = {"Host": node.real_ws_host}
        out["transport"] = transport
    elif node.real_network == "grpc":
        out["transport"] = {"type": "grpc", "service_name": node.real_grpc_service or ""}

    if node.real_tls:
        tls: dict = {
            "enabled": True,
            "server_name": node.real_sni or node.real_host,
            "insecure": node.real_insecure,
        }
        if node.real_fingerprint:
            tls["utls"] = {"enabled": True, "fingerprint": node.real_fingerprint}
        out["tls"] = tls

    return out


def _enabled_nodes(db: DBSession) -> list[Node]:
    return list(
        db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.sort_order, Node.id)).all()
    )


def render_singbox(db: DBSession) -> dict:
    inbounds: list[dict] = []
    outbounds: list[dict] = [{"type": "direct", "tag": "direct"}]
    rules: list[dict] = []
    used_ports: dict[int, str] = {}
    index = 0

    for node in _enabled_nodes(db):
        if not node.entry_host or not node.entry_uuid or not node.real_host:
            continue
        try:
            outbound = _real_outbound(node)
        except ValueError:
            continue

        port = internal_port(index, node)
        if port in used_ports:
            raise ValueError(
                f"节点「{node.name}」与「{used_ports[port]}」监听端口冲突（{port}）。"
                "请给每个节点不同的 entry_port，或启用 relay_behind_nginx。"
            )
        used_ports[port] = node.name

        inbounds.append(_entry_inbound(node, index))
        outbounds.append(outbound)
        rules.append({"inbound": [_entry_tag(node)], "outbound": _node_tag(node)})
        index += 1

    config: dict = {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {"rules": rules, "final": "direct"},
    }
    if settings.relay_behind_nginx:
        config["_comment"] = (
            "本配置假设 Nginx 在前面终止 TLS：客户端连 gate.example.com:443，"
            f"Nginx 按 WS 路径反代到 127.0.0.1:{settings.relay_internal_base}+N。"
        )
    return config


def render_nginx(db: DBSession) -> str:
    nodes = [n for n in _enabled_nodes(db) if n.entry_host and n.entry_uuid]
    blocks: list[str] = []
    for index, node in enumerate(nodes):
        path = node.entry_path or f"/e/n{node.id}"
        blocks.append(
            f"    location {path} {{\n"
            f"        proxy_pass http://127.0.0.1:{internal_port(index, node)};\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Upgrade $http_upgrade;\n"
            '        proxy_set_header Connection "upgrade";\n'
            "        proxy_set_header Host $host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_read_timeout 3600s;\n"  # ⚠ 太小会掐断长连接
            "        proxy_send_timeout 3600s;\n"
            "        proxy_buffering off;\n"
            "    }\n"
        )

    sni = (nodes[0].entry_sni or nodes[0].entry_host) if nodes else "canoe.example.com"
    return (
        "# 由 light-boat/Canoe Server 生成：Nginx 前置，443 终止 TLS，按 WS 路径分流\n"
        "server {\n"
        "    listen 443 ssl;\n"
        "    http2 on;\n"
        f"    server_name {sni};\n\n"
        f"    ssl_certificate     /etc/letsencrypt/live/{sni}/fullchain.pem;\n"
        f"    ssl_certificate_key /etc/letsencrypt/live/{sni}/privkey.pem;\n"
        "    ssl_protocols       TLSv1.2 TLSv1.3;\n\n"
        "    # 伪装：根路径看起来像个普通站点\n"
        '    location / { return 200 "OK\\n"; add_header Content-Type text/plain; }\n\n'
        + "\n".join(blocks)
        + "}\n\n"
        "server {\n    listen 80;\n    server_name " + sni + ";\n"
        "    return 301 https://$host$request_uri;\n}\n"
    )


def dump_singbox(db: DBSession, path: str | Path | None = None) -> str:
    text = json.dumps(render_singbox(db), indent=2, ensure_ascii=False)
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return text
