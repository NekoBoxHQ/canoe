"""轻舟 / Canoe Server —— 配置。环境变量或 .env 覆盖。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from canoe_core import BRAND_CN, NAMESPACE_SERVER, SERVER_NAME

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

#: 管理员密码的内置默认值。
#: ⚠️ 它不是"默认密码"，而是"还没设过"的标记：仓库公开，这个字符串
#:    任何人都能从 GitHub 上搜到。seed.py 见到它就随机生成一把并打印出来，
#:    绝不会拿它当真密码。install.sh 本来就直接生成随机的。
DEFAULT_ADMIN_PASSWORD = "canoe-admin-123"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = SERVER_NAME
    brand: str = BRAND_CN
    namespace: str = NAMESPACE_SERVER

    # --- 监听 ---
    # 默认 58588：客户端固定拿这个端口取更新和订阅（见 deploy/README.md）。
    # 想换端口改 .env 的 PORT 即可，客户端那边改 client.json 的 update_url。
    host: str = "0.0.0.0"
    port: int = 58588
    debug: bool = False

    # --- TLS（直连模式）---
    # 两个都填了，uvicorn 就直接用它们做 HTTPS，不需要 Nginx。
    # 用 Nginx 终止 TLS 时留空即可（Nginx 反代到本机 127.0.0.1:PORT）。
    tls_cert: str = ""
    tls_key: str = ""

    database_url: str = f"sqlite:///{(DATA_DIR / 'canoe.db').as_posix()}"

    # --- 令牌 ---
    # 需求里是 tokens 表 + Bearer token。这里用不透明的随机 token（存哈希），
    # 好处是可以真正做到"封禁立即生效"。见 security.py 的说明。
    token_bytes: int = 32
    token_ttl: int = 86400  # 登录令牌有效期（秒），默认 1 天

    ticket_secret: str = "CHANGE_ME_ticket_secret_before_production"
    entry_ticket_ttl: int = 300  # 入口凭证有效期（秒）

    # --- Web 管理面板 ---
    # 面板是纯静态的（HTML+CSS+JS，无构建步骤），挂在这个目录上。
    panel_dir: str = str(BASE_DIR / "panel")
    panel_path: str = "/panel"       # 面板的挂载路径
    #: 面板单独监听的端口。0（默认）= 和 PORT 同一个口。
    #:
    #: 客户端固定拿 PORT 取更新和订阅，那一个口必须对所有用户开放；
    #: 面板想只对自己开放的话，就在这里另开一个口，然后在防火墙/
    #: 安全组里只放行你自己的 IP。开了之后：
    #:   · 客户端那个口**不再响应 /panel**（扫描器看不到管理入口）
    #:   · 面板口仍然提供 /api/*，因为面板要调它（同源，不需要 CORS）
    #: 两个端口由同一个进程监听 —— 不能拆成两个进程，否则推送中心会分裂。
    panel_port: int = 0

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
    #: 内置默认值。**这个值在仓库里是公开的**，所以 seed.py 见到它就会
    #: 换成随机密码 —— 否则照着文档手动部署的人，会在公网上开一个
    #: 密码人尽皆知的管理面板。见 DEFAULT_ADMIN_PASSWORD。
    admin_password: str = DEFAULT_ADMIN_PASSWORD

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
