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
`/api/config` 的响应类型是 ConfigResponse，它的 entry 字段是 EntryPayload，
**类型上就没有真实节点的容身之处**。见 docs/04-security.md。
"""
from __future__ import annotations

from typing import Any

import requests
from pydantic import ValidationError

from canoe_core import Api, ConfigResponse, ErrorCode, LoginResponse

from .config import config

DEFAULT_TIMEOUT = 15


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
        self.token: str | None = None

    @property
    def base(self) -> str:
        return config.server_url

    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            resp = self._http.request(
                method, f"{self.base}{path}", headers=headers, timeout=DEFAULT_TIMEOUT, **kwargs
            )
        except requests.exceptions.SSLError as exc:
            raise CanoeApiError(ErrorCode.TLS, f"TLS 握手失败：{exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise CanoeApiError(ErrorCode.NETWORK, f"连不上渡口 {self.base}") from exc
        except requests.exceptions.Timeout as exc:
            raise CanoeApiError(ErrorCode.TIMEOUT, "渡口响应超时") from exc

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
                "device_name": config["device_name"],
            },
        )
        result = LoginResponse.model_validate(data)
        self.token = result.token
        return result

    def logout(self, session_id: str | None = None) -> None:
        if not self.token:
            return
        try:
            self._request("POST", Api.LOGOUT, json={"session_id": session_id})
        except CanoeApiError:
            pass
        self.token = None

    def me(self) -> dict:
        return self._request("GET", Api.ME)

    # ------------------------------------------------------------------
    # 配置下发 / 心跳
    # ------------------------------------------------------------------
    def fetch_config(self, mode: str) -> ConfigResponse:
        """拉取入口配置。

        ★ 返回类型 ConfigResponse 只含 node_name / entry / token，
          没有任何真实节点字段 —— 这是类型层面的保证，不是约定。★
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

        # 出网前的自检：万一服务端有 bug 把 real_* 发过来了，这里直接拒绝，
        # 而不是把真实节点信息喂给内核。
        resp.assert_no_real_fields()
        resp.entry.assert_whitelisted()
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


api = CanoeApi()
