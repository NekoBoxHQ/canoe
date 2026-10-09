"""把阻塞调用（网络、启动内核）丢到线程池，别卡住 Qt 主线程。

这里有两个 PySide6 上很容易踩、而且只在运行时才暴露的坑，都已在代码里规避：

  1. QRunnable 没有 Python 引用时会被 GC 回收，连带 self.signals 一起销毁，
     回调**静默不触发**。所以用 _ACTIVE 集合持有运行中的 Worker。

  2. 释放引用不能在信号回调里立刻做。如果在槽函数里 _ACTIVE.discard(self)，
     引用归零会让 _Signals 在信号**还在分发**的过程中被销毁，
     排在后面的 on_ok / on_err 就永远收不到。所以延后到下一个事件循环 tick。
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot


class _Signals(QObject):
    finished = Signal(object)   # 成功：返回值
    failed = Signal(str, str)   # 失败：(错误码, 错误信息)
    progress = Signal(int, int)  # 进度：(已完成, 总量)。总量未知时为 0


# 持有运行中的 Worker，防止被 GC 回收（见模块头第 1 条）
_ACTIVE: set["Worker"] = set()


class Worker(QRunnable):
    """用法：

        Worker(fn, arg1, kw=1).run_with(on_ok, on_err)
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _Signals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一转成信号交给 UI
            code = getattr(exc, "code", "error")
            message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
            traceback.print_exc()
            self.signals.failed.emit(str(code), str(message))
        else:
            self.signals.finished.emit(result)

    def run_with(
        self,
        on_ok: Callable[[Any], None] | None = None,
        on_err: Callable[[str, str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """排进线程池跑起来。

        给了 on_progress 的话，会把信号本身当成回调**注入被调函数的
        `on_progress` 参数** —— 被调函数只要按 `on_progress(done, total)`
        调它就行，不用自己操心线程怎么回到界面线程（Qt 信号自动排队）。
        下载安装包那条路就是这么报进度的。
        """
        _ACTIVE.add(self)
        if on_ok:
            self.signals.finished.connect(on_ok)
        if on_err:
            self.signals.failed.connect(on_err)
        if on_progress:
            self._kwargs["on_progress"] = self.signals.progress.emit
            self.signals.progress.connect(on_progress)
        # 释放必须最后连接，而且是延后执行（见模块头第 2 条）
        self.signals.finished.connect(self._schedule_release)
        self.signals.failed.connect(self._schedule_release)
        QThreadPool.globalInstance().start(self)

    @Slot()
    def _schedule_release(self) -> None:
        QTimer.singleShot(0, lambda: _ACTIVE.discard(self))
