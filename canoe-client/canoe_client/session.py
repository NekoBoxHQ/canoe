"""内存态会话。

阶段1 没有服务端，所以会话里只有：当前账号、节点显示名、运行状态。
节点名来自 testnodes.py（写死的测试节点）。

阶段3 会把 node_name / entry 换成服务端 /api/config 下发的值，
其余字段和状态机不变。
"""
from __future__ import annotations

from dataclasses import dataclass

from canoe_core import Text

# 界面状态机（四态，文案取自 canoe_core.Text）
STATE_DOCKED = "docked"      # 已靠岸
STATE_SAILING = "sailing"    # 渡江中…
STATE_SAILED = "sailed"      # 已启航
STATE_STORM = "storm"        # 风浪太大，请重试

STATE_TEXT = {
    STATE_DOCKED: Text.ST_DISCONNECTED,
    STATE_SAILING: Text.ST_CONNECTING,
    STATE_SAILED: Text.ST_CONNECTED,
    STATE_STORM: Text.ST_ERROR,
}


@dataclass
class Session:
    # 账号
    username: str = ""
    logged_in: bool = False

    # 节点
    node_name: str = ""

    # 运行态
    state: str = STATE_DOCKED
    error: str = ""

    @property
    def sailing(self) -> bool:
        return self.state in (STATE_SAILING, STATE_SAILED)

    @property
    def status_text(self) -> str:
        return STATE_TEXT.get(self.state, Text.ST_DISCONNECTED)

    def login(self, username: str, node_name: str) -> None:
        self.username = username
        self.logged_in = True
        self.node_name = node_name
        self.state = STATE_DOCKED
        self.error = ""

    def logout(self) -> None:
        self.username = ""
        self.logged_in = False
        self.node_name = ""
        self.state = STATE_DOCKED
        self.error = ""


session = Session()
