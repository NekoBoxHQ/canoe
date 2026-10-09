"""轻舟 / Canoe Server —— 配置。环境变量或 .env 覆盖。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from canoe_core import BRAND_CN, NAMESPACE_SERVER, SERVER_NAME

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = SERVER_NAME
    brand: str = BRAND_CN
    namespace: str = NAMESPACE_SERVER

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    database_url: str = f"sqlite:///{(DATA_DIR / 'canoe.db').as_posix()}"

    # --- 令牌 ---
    # 需求里是 tokens 表 + Bearer token。这里用不透明的随机 token（存哈希），
    # 好处是可以真正做到"封禁立即生效"。见 security.py 的说明。
    token_bytes: int = 32
    token_ttl: int = 86400  # 登录令牌有效期（秒），默认 1 天

    ticket_secret: str = "CHANGE_ME_ticket_secret_before_production"
    entry_ticket_ttl: int = 300  # 入口凭证有效期（秒）

    # --- 客户端更新（安装包自托管）---
    # 对外可访问的站点根，用来拼安装包下载地址。
    # 留空则按请求里的 Host 现算 —— 本地调试不用配，生产建议写死成
    # https://canoe.example.com，免得被 Host 头带偏。
    public_base_url: str = ""
    release_dir: str = str(BASE_DIR / "releases")
    max_release_mb: int = 300        # 单个安装包上限，防止把磁盘写满

    # --- 推送（SSE）---
    sse_keepalive: int = 20          # 保活注释间隔（秒）
    sse_max_connections: int = 2000  # 同时在线长连接上限，防打满
    sse_max_per_user: int = 5        # 单账号并发长连接上限

    # --- 业务策略 ---
    default_expire_days: int = 30
    default_max_devices: int = 3
    heartbeat_interval: int = 30
    online_timeout: int = 90

    # --- 中转层 ---
    relay_reload_hook: str = ""  # 例如 systemctl restart sing-box
    relay_listen: str = "::"
    relay_port: int = 443
    relay_tls_cert: str = "/etc/sing-box/cert.pem"
    relay_tls_key: str = "/etc/sing-box/key.pem"
    # sing-box 多个入站不能共用同一端口，所以推荐 Nginx 前置：
    # 443 上终止 TLS，按 WS 路径反代到本机 20000+ 的不同端口。
    relay_behind_nginx: bool = True
    relay_internal_base: int = 20000
    relay_config_out: str = str(DATA_DIR / "relay_config.json")

    # --- seed ---
    admin_username: str = "admin"
    admin_password: str = "canoe-admin-123"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
