"""推送中心 —— 把服务端的事件推给在线客户端（SSE）。

    GET /api/events 是一条**长连接**。客户端启航后挂着它，
    管理员一改节点 / 踢人 / 发新版本，客户端立刻收到，不用等心跳。

为什么值得单独一个模块：

    管理端的路由是同步函数（FastAPI 丢在线程池里跑），而长连接活在
    asyncio 事件循环里。**不能直接从工作线程往 asyncio.Queue 里塞东西** ——
    必须走 `loop.call_soon_threadsafe`。这个坑不小心就会变成"推送时灵时不灵"。

事件类型（客户端按 type 分支）：

    hello           连上就发，带上当前 config_version / revision，便于立刻对齐
    config_changed  节点或绑定变了 -> 提示"配置已更新，请重新启航"
    release         发布了新的客户端版本
    kick            管理端踢下线 / 封禁 -> 客户端必须立即靠岸
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator

from ..config import settings

#: 每条连接的待发队列上限。客户端读得慢也不至于把服务端内存顶爆。
QUEUE_SIZE = 64


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[asyncio.Queue, int] = {}   # queue -> user_id
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- 生命周期 ------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """应用启动时记下事件循环，publish() 靠它跨线程投递。"""
        self._loop = loop

    # -- 观察 ----------------------------------------------------------
    @property
    def connections(self) -> int:
        with self._lock:
            return len(self._subs)

    def connections_of(self, user_id: int) -> int:
        with self._lock:
            return sum(1 for uid in self._subs.values() if uid == user_id)

    def stats(self) -> dict[str, int]:
        with self._lock:
            users = len({uid for uid in self._subs.values()})
            return {"connections": len(self._subs), "users": users}

    # -- 订阅 ----------------------------------------------------------
    def can_accept(self, user_id: int) -> tuple[bool, str]:
        """连接上限保护。超了就拒绝，而不是把自己拖垮。"""
        with self._lock:
            if len(self._subs) >= settings.sse_max_connections:
                return False, "连接数已达上限"
            mine = sum(1 for uid in self._subs.values() if uid == user_id)
            if mine >= settings.sse_max_per_user:
                return False, "该账号长连接数已达上限"
        return True, ""

    def _register(self, user_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subs[queue] = user_id
        return queue

    def _drop(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs.pop(queue, None)

    async def stream(self, user_id: int, hello: dict[str, Any]) -> AsyncIterator[dict]:
        """一条连接的完整生命周期。产出 dict，由路由层转成 SSE 帧。"""
        queue = self._register(user_id)
        try:
            yield {"type": "hello", **hello}
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=settings.sse_keepalive
                    )
                except asyncio.TimeoutError:
                    # 超时就发一个注释帧保活。中间的代理（Nginx 等）会把
                    # 长时间没数据的连接掐掉，所以这个心跳是必需的。
                    yield {"type": "ping"}
                    continue
                yield event
        finally:
            self._drop(queue)

    # -- 发布 ----------------------------------------------------------
    def publish(self, event: dict[str, Any]) -> int:
        """把事件推给所有在线连接。**可以从任意线程调用。**

        返回投递前的连接数（用来判断"有没有人在听"，不是投递成功数）。
        """
        with self._lock:
            count = len(self._subs)
        loop = self._loop
        if loop is None or loop.is_closed() or count == 0:
            return count
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:
            # 循环正在关闭（进程退出中）—— 丢掉这条推送即可，不要抛
            return 0
        return count

    def _fanout(self, event: dict[str, Any]) -> None:
        """在事件循环线程里执行。"""
        with self._lock:
            queues = list(self._subs.keys())
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 客户端明显卡住了：队列满说明它根本没在读。
                # 丢掉最老的一条再放新的 —— 宁可丢历史，也不要让连接永远滞后。
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


hub = EventHub()


# --------------------------------------------------------------------------
# 便捷发布函数（管理端直接调）
# --------------------------------------------------------------------------


def notify_config_changed(config_version: int, revision: str = "", node_name: str = "") -> int:
    return hub.publish(
        {
            "type": "config_changed",
            "config_version": config_version,
            "revision": revision,
            "node_name": node_name,
        }
    )


def notify_release(version: str, url: str = "", notes: str = "") -> int:
    return hub.publish(
        {"type": "release", "version": version, "url": url, "notes": notes}
    )


def notify_kick(user_id: int, reason: str = "管理员操作") -> int:
    """踢下线。客户端收到必须立即靠岸。"""
    return hub.publish({"type": "kick", "user_id": user_id, "reason": reason})
