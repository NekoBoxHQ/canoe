"""日志总线 —— 事件汇总。界面只取其中的**结果**类，不显示内核日志。

为什么要这一层：内核输出是在子进程管道的读取线程里产生的，而界面只能在
主线程刷。中间放一个线程安全的环形缓冲，两边就解耦了。

★ 为什么界面上**不显示内核日志** ★

    sing-box 的日志里带节点域名和入口地址，比如
        outbound/shadowsocks[proxy]: outbound connection to one.leycc.com:443
    把它显示在界面上，等于把节点信息暴露给了用户 —— 这违反项目的硬性要求
    （客户端不显示节点地址/端口/协议/密码）。

    所以内核日志照读不误（不读的话子进程会写满管道阻塞），
    但**只留在总线里，不进界面**。界面只显示三类结果：
    更新版本号、TCping 毫秒数、URL 测试毫秒数。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

#: 标签。
TAG_RESULT = "结果"    # ★ 可以显示在界面上的：更新 / TCping / URL测试 的结果
TAG_SYSTEM = "系统"    # 程序自身事件，不显示
TAG_KERNEL = "内核"    # sing-box 输出，**绝不显示**（含节点域名）
TAG_TEST = "测试"
TAG_ERROR = "错误"     # 失败原因，会显示（但内容要脱敏）

#: 界面上允许显示的标签
VISIBLE_TAGS = frozenset({TAG_RESULT, TAG_ERROR})


@dataclass(frozen=True)
class LogLine:
    seq: int
    time_text: str
    tag: str
    message: str

    def render(self) -> str:
        return f"[{self.time_text}] [{self.tag}] {self.message}"


class LogBus:
    def __init__(self, maxlen: int = 800) -> None:
        self._lines: deque[LogLine] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, tag: str, message: str) -> None:
        message = (message or "").rstrip()
        if not message:
            return
        with self._lock:
            self._lines.append(
                LogLine(
                    seq=self._seq,
                    time_text=time.strftime("%H:%M:%S"),
                    tag=tag,
                    message=message,
                )
            )
            self._seq += 1

    # 便捷方法，省得每次都 import 标签常量
    def result(self, message: str) -> None:
        """会显示在界面上的结果行（更新 / TCping / URL测试）。"""
        self.add(TAG_RESULT, message)

    def system(self, message: str) -> None:
        self.add(TAG_SYSTEM, message)

    def kernel(self, message: str) -> None:
        """内核输出。只会留在总线里，界面不显示（含节点域名）。"""
        self.add(TAG_KERNEL, message)

    def test(self, message: str) -> None:
        self.add(TAG_TEST, message)

    def error(self, message: str) -> None:
        self.add(TAG_ERROR, message)

    # ---------------------------------------------------------------- 读
    def since(self, seq: int) -> list[LogLine]:
        """取 seq 之后的新行（含内核日志）。"""
        with self._lock:
            return [ln for ln in self._lines if ln.seq >= seq]

    def visible_since(self, seq: int) -> list[LogLine]:
        """取 seq 之后**可以显示在界面上**的新行。

        界面用这个而不是 since()，内核日志就被挡在外面了。
        """
        return [ln for ln in self.since(seq) if ln.tag in VISIBLE_TAGS]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


bus = LogBus()
