"""★ 阶段1 专用 · 临时文件 · 阶段3 会删除 ★

这里写死了测试节点，用于在服务端出来之前把桌面端跑通。

════════════════════════════════════════════════════════════════
  ⚠ 安全声明（重要）

  阶段1 客户端**确实持有真实节点信息**，这是刻意的临时状态，
  只用于本地联调。它违反了 docs/04-security.md 里的硬性要求，所以：

    · 这个文件在阶段3 必须删除
    · 阶段3 之后，节点信息改由服务端 /api/config 下发的中转层入口提供，
      客户端拿到的永远只是中转层地址 + 短期凭证
    · 任何情况下都不要把这份代码当作正式版发布

  为了让阶段3 的替换成本最小，节点信息只在这一个文件里，
  并且只通过 `build_proxy_outbound()` 一个函数对外暴露。
  阶段3 替换这一个函数即可。
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# 测试节点
# --------------------------------------------------------------------------

#: 用户在界面上看到的节点名（只显示名字，不显示地址/端口/协议/密码）
NODE_DISPLAY_NAME = "🌍 日本家宽 🌍"

#: 阶段1 的写死测试节点。字段名直接对应 sing-box 的 shadowsocks outbound。
TEST_NODE: dict[str, Any] = {
    "type": "shadowsocks",
    "server": "one.leycc.com",
    "server_port": 33222,
    "method": "2022-blake3-aes-128-gcm",
    # Shadowsocks 2022 多用户：password 是 "server_key:user_key" 两段 base64 密钥
    "password": "hlKPbKuiXS9LaEmUOq5HYA==:J8DQT4ZqDl+r3DQtWhI+Zg==",
}


def node_display_name() -> str:
    """界面上显示的节点名。"""
    return NODE_DISPLAY_NAME


def build_proxy_outbound(tag: str = "proxy") -> dict[str, Any]:
    """把测试节点转成 sing-box 出站配置。

    ★ 阶段3 替换点 ★
      阶段3 时这个函数会被改成读取服务端下发的 EntryPayload，
      返回指向**中转层入口**的 vless 出站，而不是直连真实节点。
      调用方（kernel.py）不需要任何改动。
    """
    out: dict[str, Any] = {
        "type": TEST_NODE["type"],
        "tag": tag,
        "server": TEST_NODE["server"],
        "server_port": TEST_NODE["server_port"],
        "method": TEST_NODE["method"],
        "password": TEST_NODE["password"],
    }
    return out
