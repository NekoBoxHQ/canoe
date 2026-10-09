"""日志总线 —— 内核输出与程序自身的事件都汇到这里，界面定时来取。

为什么要这一层：sing-box 的日志是从子进程的管道里读出来的（在工作线程里），
而界面只能在主线程刷。中间放一个线程安全的环形缓冲，两边就解耦了：
写的一方只管往里塞，界面用 QTimer 定时取新增的行。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

#: 标签。界面按标签给不同颜色。
TAG_SYSTEM = "系统"
TAG_KERNEL = "内核"
TAG_TEST = "测试"
TAG_ERROR = "错误"


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
    def system(self, message: str) -> None:
        self.add(TAG_SYSTEM, message)

    def kernel(self, message: str) -> None:
        self.add(TAG_KERNEL, message)

    def test(self, message: str) -> None:
        self.add(TAG_TEST, message)

    def error(self, message: str) -> None:
        self.add(TAG_ERROR, message)

    # ---------------------------------------------------------------- 读
    def since(self, seq: int) -> list[LogLine]:
        """取 seq 之后的新行。界面拿它做增量刷新。"""
        with self._lock:
            return [ln for ln in self._lines if ln.seq >= seq]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


bus = LogBus()
