"""网络测试：TCping（TCP 握手延迟）与 URL 测试（经代理请求耗时）。

两者的区别值得说清楚，因为很容易混：

  TCping   直接对本机到**节点服务器**做 TCP 三次握手，测的是
           "我到节点这条路通不通、快不快"。不经过代理，也测不出节点到
           目标网站那段。

  URL测试  经**本地代理**请求一个网址，测的是端到端：本机 → 节点 → 目标站。
           代理没启航时做不了。

所以节点能 TCping 通但 URL 测试失败，通常意味着真实节点那边有问题；
两个都不通，多半是本机到节点这一段断了。
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

import requests

TCPING_TIMEOUT = 3.0
DEFAULT_URL = "http://www.gstatic.com/generate_204"


@dataclass
class PingResult:
    target: str
    ok_count: int = 0
    total: int = 0
    times: list[float] = field(default_factory=list)
    error: str = ""

    @property
    def lost(self) -> int:
        return self.total - self.ok_count

    @property
    def ok(self) -> bool:
        return self.ok_count > 0

    def summary(self) -> str:
        if not self.times:
            return f"{self.target}  全部超时（{self.total} 次）" + (
                f"  {self.error}" if self.error else ""
            )
        lo, hi = min(self.times), max(self.times)
        avg = sum(self.times) / len(self.times)
        text = f"{self.target}  最小 {lo:.0f}ms / 平均 {avg:.0f}ms / 最大 {hi:.0f}ms"
        if self.lost:
            text += f"  丢包 {self.lost}/{self.total}"
        return text


@dataclass
class UrlResult:
    url: str
    ok: bool = False
    status: int = 0
    elapsed_ms: float = 0.0
    error: str = ""

    def summary(self) -> str:
        if self.ok:
            return f"{self.url}  HTTP {self.status}  耗时 {self.elapsed_ms:.0f}ms"
        return f"{self.url}  失败：{self.error}"


# --------------------------------------------------------------------------
# TCping
# --------------------------------------------------------------------------


def tcping(host: str, port: int, count: int = 4, timeout: float = TCPING_TIMEOUT) -> PingResult:
    """对 host:port 连 count 次，测 TCP 握手耗时。"""
    result = PingResult(target=f"{host}:{port}", total=count)
    for i in range(count):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
            result.times.append((time.perf_counter() - t0) * 1000)
            result.ok_count += 1
        except OSError as exc:
            result.error = _describe(exc)
        if i < count - 1:
            time.sleep(0.25)
    return result


def _describe(exc: OSError) -> str:
    if isinstance(exc, socket.timeout):
        return "连接超时"
    if isinstance(exc, socket.gaierror):
        return "域名解析失败"
    if getattr(exc, "winerror", None) == 10061 or "refused" in str(exc).lower():
        return "连接被拒绝"
    return str(exc)


# --------------------------------------------------------------------------
# URL 测试
# --------------------------------------------------------------------------


def url_test(proxy_port: int, url: str = DEFAULT_URL, timeout: float = 15.0) -> UrlResult:
    """经本地代理请求 url，测端到端耗时。

    sing-box 的 mixed 入站同时支持 SOCKS 和 HTTP，
    所以直接用 HTTP 代理模式，不需要额外的 SOCKS 依赖。
    """
    result = UrlResult(url=url)
    proxies = {
        "http": f"http://127.0.0.1:{proxy_port}",
        "https": f"http://127.0.0.1:{proxy_port}",
    }
    t0 = time.perf_counter()
    try:
        resp = requests.get(url, proxies=proxies, timeout=timeout,
                            headers={"User-Agent": "Canoe-Client/1.0"})
    except requests.exceptions.ProxyError as exc:
        result.error = f"代理不可用（还没启航？）{exc}"
    except requests.exceptions.SSLError as exc:
        result.error = f"TLS 失败：{exc}"
    except requests.exceptions.ConnectTimeout:
        result.error = "连接超时"
    except requests.exceptions.ReadTimeout:
        result.error = "读取超时"
    except requests.exceptions.RequestException as exc:
        result.error = str(exc) or exc.__class__.__name__
    else:
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        result.status = resp.status_code
        # generate_204 正常返回 204；其它网址 2xx/3xx 都算通
        result.ok = resp.status_code < 400
        if not result.ok:
            result.error = f"HTTP {resp.status_code}"
    return result
