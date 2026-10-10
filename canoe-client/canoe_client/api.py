"""★ 阶段3 使用 · 阶段1 的客户端不引用本模块 ★

这是与服务端通信的客户端封装（登录、拉取 /api/config、心跳）。
阶段1 还没有服务端，所以它现在是一份**未接线的草稿**，保留在这里是为了
阶段3 接服务端时可以直接用。

阶段3 的接线方式：
    main_view.start_with_test_node()  ->  改成调用 api.login() 拿 token
    testnodes.build_proxy_outbound()  ->  改成用 api.fetch_config() 返回的 entry
其余界面代码不用动。

--------------------------- 原说明 ---------------------------

与服务端通信。HTTPS + Bearer token。

这个模块拿到的所有响应都会先经 canoe_core 的模型校验 ——
`/api/config` 的响应类型是 ConfigResponse —— 它**类型上就没有节点信息的
容身之处**。节点只在 /api/subscription 的加密信封里。见 docs/04-security.md。
"""
from __future__ import annotations

import time
from typing import Any

import requests
from pydantic import ValidationError

from canoe_core import (
    Api,
    ClientReleaseResponse,
    ConfigResponse,
    ErrorCode,
    LoginResponse,
    SubCryptoError,
    SubscriptionResponse,
    assert_no_leaks,
    unseal,
)

from .config import config

DEFAULT_TIMEOUT = 15

#: 连接断了重试几次、每次等多久（秒）。服务端重启的那一两秒要靠它盖过去。
_RETRIES = 2
_RETRY_WAIT = 0.8


class CanoeApiError(Exception):
    """带错误码的 API 异常。UI 直接拿 message 展示。"""

    def __init__(self, code: str, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    @property
    def needs_relogin(self) -> bool:
        """需要回到登舟页的错误。"""
        return self.code in {
            ErrorCode.UNAUTHORIZED,
            ErrorCode.BANNED,
            ErrorCode.EXPIRED,
            ErrorCode.BAD_CREDENTIALS,
        } or self.status == 401


class CanoeApi:
    def __init__(self) -> None:
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "Canoe-Client/1.0"})
        # ★ 客户端跟服务端说话这条路**不走系统代理**。
        #
        # 代理配置本来就是服务端下发的 —— 让"连得上服务端"依赖"代理能用"，
        # 就成了个环：代理一坏，连"点更新"都下不下来，程序自己没法自愈。
        #
        # 这不是理论问题。系统代理被写成 127.0.0.1:20818、内核却已经退了，
        # 那条代理就是个死端口，requests 会老老实实走它 —— 于是"系统代理
        # 加 TUN 的时候下不了更新"（用户报的）。直连的话这条路一直是通的。
        #
        # 服务端是自己的域名，直连得到；真直连不了，本来也登不上舟。
        self._http.trust_env = False
        self.token: str | None = None
        #: 会话级订阅密钥。**只在内存**，登舟时拿到，离舟时丢掉。
        self.sub_key: str = ""
        #: 当前账号的 id（登录响应里带）。服务端推来的 kick 事件里也有
        #: 一个 user_id —— 那是"这条踢人通知是给谁的"，得跟这个对上才算
        #: 自己的（见 ui/main_view 的 kick 分支）。0 = 还不知道。
        self.user_id: int = 0

    @property
    def base(self) -> str:
        return config.server_url

    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # 默认真校验 TLS；只有联调自签证书时才用 CANOE_CA_BUNDLE 指一张 CA
        kwargs.setdefault("verify", config.ca_bundle)

        # 连接层面抖一下就重试。**不是**为了掩盖问题，是因为服务端重启
        # 的那一两秒里，连接会在握手阶段被掐断，用户看到的是
        # "TLS 握手失败" —— 看着像证书坏了，其实只是服务端在重启
        # （`canoe upgrade` 每次都会重启一次）。重试两次盖住那一下。
        #
        # ⚠ SSLError 是 ConnectionError 的子类，顺序不能反，否则永远
        #   走不到 SSLError 那条分支。
        last_exc: Exception | None = None
        for attempt in range(_RETRIES + 1):
            try:
                resp = self._http.request(
                    method, f"{self.base}{path}", headers=headers,
                    timeout=DEFAULT_TIMEOUT, **kwargs,
                )
                break
            except requests.exceptions.SSLError as exc:
                last_exc = exc
                if attempt < _RETRIES:
                    time.sleep(_RETRY_WAIT * (attempt + 1))
                    continue
                raise CanoeApiError(
                    ErrorCode.TLS,
                    f"TLS 握手失败：{exc}\n"
                    "（证书本身没问题的话，多半是服务端正在重启 —— 稍后再试一次）",
                ) from exc
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                if attempt < _RETRIES:
                    time.sleep(_RETRY_WAIT * (attempt + 1))
                    continue
                raise CanoeApiError(ErrorCode.NETWORK, f"连不上渡口 {self.base}") from exc
            except requests.exceptions.Timeout as exc:
                # 超时不重试 —— 那多半是真的慢，再等两轮只会更难受
                raise CanoeApiError(ErrorCode.TIMEOUT, "渡口响应超时") from exc
        else:                                     # pragma: no cover - 兜底
            raise CanoeApiError(ErrorCode.NETWORK, f"连不上渡口 {self.base}") from last_exc

        if resp.status_code >= 400:
            code, message = ErrorCode.INTERNAL, f"HTTP {resp.status_code}"
            try:
                body = resp.json()
                detail = body.get("detail", body)
                if isinstance(detail, dict):
                    code = detail.get("code", code)
                    message = detail.get("detail", message)
                elif isinstance(detail, str):
                    message = detail
                elif isinstance(detail, list) and detail:
                    first = detail[0]
                    message = first.get("msg", message) if isinstance(first, dict) else str(first)
            except ValueError:
                message = resp.text[:200] or message
            raise CanoeApiError(code, message, resp.status_code)

        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------
    def register(self, username: str, password: str) -> dict:
        return self._request(
            "POST", Api.REGISTER, json={"username": username, "password": password}
        )

    def login(self, username: str, password: str) -> LoginResponse:
        data = self._request(
            "POST",
            Api.LOGIN,
            json={
                "username": username,
                "password": password,
                "device_id": config.device_id,
                "device_name": config.device_name,
            },
        )
        result = LoginResponse.model_validate(data)
        self.token = result.token
        # 订阅密钥只活在内存里：进程一退就没了，下次登舟重新拿。
        # 落盘的话就变成"关掉客户端还能解订阅"，服务端也就没法靠
        # 吊销会话来收回控制权了。
        self.sub_key = result.sub_key
        self.user_id = result.user.id
        return result

    def logout(self, session_id: str | None = None) -> None:
        if not self.token:
            return
        try:
            self._request("POST", Api.LOGOUT, json={"session_id": session_id})
        except CanoeApiError:
            pass
        self.token = None
        self.sub_key = ""
        self.user_id = 0

    def me(self) -> dict:
        return self._request("GET", Api.ME)

    # ------------------------------------------------------------------
    # 配置下发 / 心跳
    # ------------------------------------------------------------------
    def fetch_config(self, mode: str) -> ConfigResponse:
        """启航：建一条会话。

        这个接口不下发任何节点信息 —— 节点从订阅里来（见 subscription_text）。
        """
        data = self._request(
            "GET",
            Api.CONFIG,
            params={"device_id": config.device_id, "mode": mode},
        )
        try:
            resp = ConfigResponse.model_validate(data)
        except ValidationError as exc:
            raise CanoeApiError(
                ErrorCode.INTERNAL, f"服务端返回的配置格式不对：{exc.error_count()} 处问题"
            ) from exc

        # 自检：这个接口本来就不该有节点信息。真收到了说明服务端有 bug，
        # 直接拒绝，不把明文链接带进内核配置里。
        assert_no_leaks(resp.model_dump())
        return resp

    def heartbeat(self, session_id: str) -> dict:
        return self._request("POST", Api.HEARTBEAT, json={"session_id": session_id})

    def stop_session(self, session_id: str) -> None:
        """靠岸：结束会话但保留登录令牌。

        不要用 logout() 代替 —— 那会吊销令牌，用户每次靠岸都得重新登舟。
        """
        try:
            self._request("POST", Api.SESSION_STOP, json={"session_id": session_id})
        except CanoeApiError:
            pass  # 靠岸流程不该因为网络问题卡住

    # ------------------------------------------------------------------
    # 更新通道（「更新」按钮对接的两条）
    # ------------------------------------------------------------------
    def subscription(self) -> SubscriptionResponse:
        """订阅：内容仍是密文。

        不建会话、不发凭证，只读 —— 没启航的时候也能随手调。
        要明文请用 subscription_text()。
        """
        data = self._request("GET", Api.SUBSCRIPTION)
        resp = SubscriptionResponse.model_validate(data)
        # 信封本身是密文，不含链接；解出来的明文才是订阅，所以这里只
        # 检查信封以外的部分没有被塞进明文节点。
        assert_no_leaks({k: v for k, v in resp.model_dump().items() if k != "envelope"})
        return resp

    def subscription_text(self) -> tuple[SubscriptionResponse, str]:
        """订阅 + 解出来的明文（可能为空串）。

        空串表示服务端不给 —— 被封、到期、或管理员把订阅栏清空了。
        调用方见到空串必须销毁本地订阅，而不是当成"网络不好"重试。
        """
        resp = self.subscription()
        try:
            text = unseal(resp.envelope, self.sub_key or "")
        except SubCryptoError as exc:
            # 解不开基本只有一个原因：会话换了。提示重新登舟，别让用户干等重试。
            raise CanoeApiError(ErrorCode.UNAUTHORIZED, f"{exc}，请重新登舟") from exc
        return resp, text

    def latest_release(self) -> ClientReleaseResponse:
        """客户端更新：有没有新版本。这个接口**不需要登录**。"""
        data = self._request("GET", Api.CLIENT_LATEST)
        return ClientReleaseResponse.model_validate(data)


api = CanoeApi()
