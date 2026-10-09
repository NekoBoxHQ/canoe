"""节点链接解析 —— 转发到 canoe_core.links。

实现已经搬到 `canoe-core`：**服务端也要解析同一批链接**（管理端加节点时
贴的就是这个，它得知道这行链接指向哪台机器、叫什么名字，才能列表显示）。
放两份一定会漂 —— 客户端认的格式和服务端认的格式必须是同一个。

这里保留一层转发，是为了不动客户端里已有的 `from .. import links`。
"""
from __future__ import annotations

from canoe_core.links import (
    SCHEMES,
    LinkError,
    NodeLink,
    ParseResult,
    parse,
    pick,
    split_lines,
)

__all__ = [
    "SCHEMES",
    "LinkError",
    "NodeLink",
    "ParseResult",
    "parse",
    "pick",
    "split_lines",
]
