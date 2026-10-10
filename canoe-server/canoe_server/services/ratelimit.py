"""登录 / 注册的失败限速 —— 进程内滑动窗口。

为什么要它：

  · `/api/login` 每次都要跑一遍 PBKDF2（24 万次迭代）才回话。这既是
    "密码对不对"的判定，也是一个**不用登录就能按下去的 CPU 开关**；
    而且没有阈值动作的话，admin 的口令可以无限试。
  · `/api/register` 干脆不要任何凭据 —— 不限就等于开了一台免费的造号机，
    顺带还能把 SSE 连接池占满。

为什么放内存、不落库：服务端是**单进程**（推送中心也是进程内的，理由见
serve.py 的说明），重启一次计数清零可以接受；为它单开一张表反而要处理
并发写、清理和跨进程一致性。

键怎么选（这条最容易做错）：

    登录**优先按用户名**（攻击者伪造不了这个名字），IP 只作为辅助；
    注册按 IP。不能只按 IP：同一台 NAT 后面的正常用户会互相拖累。
    也正因为要按 IP，`deps.client_ip()` 默认**不再信任 X-Forwarded-For**
    （见 config.trust_proxy）—— 否则攻击者每个请求换一个假 IP 就绕过去了。
"""

from __future__ import annotations

import threading
import time
from collections import deque

from ..config import settings

#: 字典最多记多少个键。攻击者可以拿随机用户名猛打，不封顶就是内存泄漏。
_MAX_KEYS = 10_000


class RateLimiter:
    """滑动窗口计数：窗口内记满 `limit` 次就锁 `lock_seconds` 秒。

    语义是"记满就锁、锁完清零"，不是"最后 N 次里还有 N 次就一直锁" ——
    后者会让持续攻击者永远出不来，前者过一段就放行，够用了。
    """

    def __init__(self, limit: int, window: float, lock_seconds: float) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window)
        self.lock_seconds = float(lock_seconds)
        self._marks: dict[str, deque[float]] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    # -- 查询 ----------------------------------------------------------
    def retry_after(self, key: str) -> int:
        """现在能不能放行。0 = 可以；>0 = 还要等这么多秒。"""
        if not key:
            return 0
        now = time.monotonic()
        with self._lock:
            until = self._locked.get(key, 0.0)
            if until <= now:
                return 0
            return max(1, int(until - now + 0.999))

    # -- 记录 ----------------------------------------------------------
    def hit(self, key: str) -> None:
        """记一次（登录记失败、注册记成功）。

        time.monotonic() 而不是 time.time()：墙上时钟会被 NTP 往回拨，
        拨一次就可能把锁变成"几小时后才失效"。
        """
        if not key:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._marks) >= _MAX_KEYS:
                self._sweep(now)
            marks = self._marks.setdefault(key, deque())
            marks.append(now)
            while marks and marks[0] < now - self.window:
                marks.popleft()
            if len(marks) >= self.limit:
                self._locked[key] = now + self.lock_seconds
                marks.clear()

    def clear(self, key: str) -> None:
        """成功一次就清零 —— 正常用户偶尔手滑几次不该被累积到锁住。"""
        if not key:
            return
        with self._lock:
            self._marks.pop(key, None)
            self._locked.pop(key, None)

    # -- 内部 ----------------------------------------------------------
    def _sweep(self, now: float) -> None:
        """把过期的键丢掉。**必须在持锁时调用。**"""
        stale = [k for k, v in self._marks.items() if not v or v[-1] < now - self.window]
        for k in stale:
            if self._locked.get(k, 0.0) <= now:
                self._marks.pop(k, None)
                self._locked.pop(k, None)


#: 登录：按用户名 + 按 IP，各查一次（见模块头的说明）
login_limiter = RateLimiter(
    settings.login_max_fails, settings.login_fail_window, settings.login_lock_seconds
)

#: 注册：只按 IP
register_limiter = RateLimiter(
    settings.register_max_per_ip, settings.register_window, settings.register_window
)


def username_key(username: str) -> str:
    """用户名键。大小写归一 —— 否则改个大小写就换了一个计数器。"""
    return f"user:{username.strip().lower()}"


def ip_key(ip: str) -> str:
    return f"ip:{ip}" if ip else ""


def login_retry_after(username: str, ip: str) -> int:
    """登录前查一次。被锁就返回要等的秒数（取两个键里更久的那个）。"""
    return max(
        login_limiter.retry_after(username_key(username)),
        login_limiter.retry_after(ip_key(ip)),
    )


def login_failed(username: str, ip: str) -> None:
    login_limiter.hit(username_key(username))
    login_limiter.hit(ip_key(ip))


def login_ok(username: str, ip: str) -> None:
    login_limiter.clear(username_key(username))
    login_limiter.clear(ip_key(ip))
