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
    ENTRY_FIELDS,
    NAMESPACE_CLIENT,
    NAMESPACE_SERVER,
    PROTOCOL_VERSION,
    REAL_FIELD_PREFIX,
    SERVER_NAME,
    SLOGAN_CN,
    SLOGAN_EN,
    Api,
    ErrorCode,
    Palette,
    Text,
)
from .models import (
    ApiError,
    ConfigResponse,
    DeviceInfo,
    EntryPayload,
    HeartbeatRequest,
    HeartbeatResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RegisterRequest,
    RegisterResponse,
    UserInfo,
)
from .passwords import hash_password, new_token, token_hash, verify_password
from .version import VERSION, __version__

__all__ = [
    "APP_ID", "BRAND_CN", "BRAND_EN", "CLIENT_EXE", "CLIENT_PROCESS",
    "CONFIG_DIR_NAME", "DOMAINS", "ENTRY_FIELDS",
    "NAMESPACE_CLIENT", "NAMESPACE_SERVER", "PROTOCOL_VERSION",
    "REAL_FIELD_PREFIX", "SERVER_NAME", "SLOGAN_CN", "SLOGAN_EN",
    "VERSION", "__version__",
    "Api", "ErrorCode", "Palette", "Text",
    "hash_password", "verify_password", "new_token", "token_hash",
    "ApiError", "ConfigResponse", "DeviceInfo", "EntryPayload",
    "HeartbeatRequest", "HeartbeatResponse", "HealthResponse",
    "LoginRequest", "LoginResponse", "LogoutRequest", "MeResponse",
    "RegisterRequest", "RegisterResponse", "UserInfo",
]
