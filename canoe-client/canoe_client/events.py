"""服务端推送（SSE）：挂着 /api/events 的长连接。

服务端会主动推这些（见 docs/02-api.md）：

    hello            连上就发，带 config_version / revision
    config_changed   节点或绑定变了 -> 提示"配置已更新，请重新启航"
    release          发布了新的客户端版本
    kick             被踢下线 / 封禁 -> **立即靠岸**

跑在一个后台线程里 —— `requests` 的 iter_lines 是阻塞的，不能放主线程。
事件通过 Qt 信号发回主线程，界面那边只管接信号。

断线自动重连（指数退避）。服务端那条连接每 20 秒会发一个注释帧保活，
所以正常的连接不会长时间"什么都没收到"。
"""
from __future__ import annotations

import json
import threading

import requests
from PySide6.QtCore import QObject, Signal

from canoe_core import Api

from .config import config

#: 重连退避（秒），一路涨到上限为止
BACKOFF = (1, 2, 5, 10, 20, 30)
#: 收不到任何字节多久算掉线。服务端 20 秒一个保活帧，40 秒足够宽松。
READ_TIMEOUT = 40


class EventStream(QObject):
    """一条 SSE 长连接。start() / stop() 可以反复调。"""

    event = Signal(dict)
    #: 连接状态变化：(是否已连上, 说明)
    state = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._token = ""
        self._generation = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, token: str) -> None:
        """挂上长连接。重复调用会先停掉旧的。"""
        self.stop()
        if not token:
            return
        self._generation += 1
        generation = self._generation
        self._token = token
        stop_event = threading.Event()
        self._stop = stop_event
        self._thread = threading.Thread(
            target=self._loop,
            args=(token, stop_event, generation),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _emit_state(self, generation: int, connected: bool, message: str) -> None:
        """旧线程退出途中不得覆盖新连接的状态。"""
        if generation == self._generation and not self._stop.is_set():
            self.state.emit(connected, message)

    # ------------------------------------------------------------------
    def _loop(self, token: str, stop_event: threading.Event, generation: int) -> None:
        url = f"{config.server_url}{Api.EVENTS}"
        headers = {"Authorization": f"Bearer {token}",
                   "Accept": "text/event-stream",
                   "User-Agent": "Canoe-Client/1.0"}
        attempt = 0

        while not stop_event.is_set():
            try:
                with requests.get(url, headers=headers, stream=True,
                                  timeout=(10, READ_TIMEOUT),
                                  verify=config.ca_bundle) as resp:
                    if resp.status_code == 401 or resp.status_code == 403:
                        # 令牌废了 / 被封 —— 重连也没用，交给心跳那条路去处理
                        self._emit_state(generation, False, "未授权")
                        return
                    resp.raise_for_status()
                    attempt = 0
                    self._emit_state(generation, True, "")
                    for line in resp.iter_lines(decode_unicode=True):
                        if stop_event.is_set():
                            return
                        if not line or not line.startswith("data: "):
                            continue          # 空行和 ": ping" 保活帧都跳过
                        try:
                            payload = json.loads(line[6:])
                        except ValueError:
                            continue
                        if isinstance(payload, dict):
                            self.event.emit(payload)
            except requests.exceptions.RequestException as exc:
                self._emit_state(generation, False, type(exc).__name__)
            except Exception:  # noqa: BLE001 - 后台线程绝不能把程序带崩
                self._emit_state(generation, False, "内部错误")

            if stop_event.is_set():
                return
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            # 用 wait 而不是 sleep，stop() 时能立刻醒来
            if stop_event.wait(delay):
                return


stream = EventStream()
