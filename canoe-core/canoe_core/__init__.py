"""轻舟 / Canoe —— 客户端与服务端的公共库。

轻舟已过万重山。
"""
from __future__ import annotations

from .constants import (
    APP_ID,
    BRAND_CN,
    BRAND_EN,
    CLIENT_EXE,
    CLIENT_PROCESS,
    CONFIG_DIR_NAME,
    DOMAINS,
    MAX_NODES_PER_USER,
    NAMESPACE_CLIENT,
    NAMESPACE_SERVER,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SLOGAN_CN,
    SLOGAN_EN,
    Api,
    ErrorCode,
    Palette,
    Route,
    Text,
)
from .models import (
    ApiError,
    ClientReleaseResponse,
    ConfigResponse,
    DeviceInfo,
    HeartbeatRequest,
    HeartbeatResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RegisterRequest,
    RegisterResponse,
    SubscriptionResponse,
    UserInfo,
)
from .crypto import Envelope, SubCryptoError, new_sub_key, seal, unseal
from .links import (
    LinkError,
    NodeLink,
    ParseResult,
    assert_no_leaks,
    find_leaks,
    parse_links,
    pick_link,
)
from .passwords import hash_password, new_token, token_hash, verify_password
from .version import VERSION, __version__

__all__ = [
    "APP_ID", "BRAND_CN", "BRAND_EN", "CLIENT_EXE", "CLIENT_PROCESS",
    "CONFIG_DIR_NAME", "DOMAINS", "MAX_NODES_PER_USER",
    "NAMESPACE_CLIENT", "NAMESPACE_SERVER", "PROTOCOL_VERSION",
    "SERVER_NAME", "SLOGAN_CN", "SLOGAN_EN",
    "VERSION", "__version__",
    "Api", "ErrorCode", "Palette", "Route", "Text",
    "hash_password", "verify_password", "new_token", "token_hash",
    "ApiError", "ConfigResponse", "DeviceInfo",
    "HeartbeatRequest", "HeartbeatResponse", "HealthResponse",
    "LoginRequest", "LoginResponse", "LogoutRequest", "MeResponse",
    "RegisterRequest", "RegisterResponse", "UserInfo",
    "ClientReleaseResponse", "SubscriptionResponse",
    "Envelope", "SubCryptoError", "new_sub_key", "seal", "unseal",
    "LinkError", "NodeLink", "ParseResult", "parse_links", "pick_link",
    "find_leaks", "assert_no_leaks",
]
