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

    #: 给 tokens.sub_key 做静态加密用的主密钥（环境变量 SECRET_KEY）。
    #: 留空则退回 TICKET_SECRET —— 那是历史遗留字段，之前**没有任何代码读它**，
    #: 正好拿来用；两个都没有就在 data/subkey.key 里生成一把（0600）。
    #: ⚠ 换掉这把钥匙 = 已存的订阅密钥全部解不开（客户端会收到空信封、
    #:   销毁本地订阅），所以一旦有值就别再动它。
    secret_key: str = ""
    ticket_secret: str = ""

    # --- 反向代理 ---
    #: 是否信任 X-Forwarded-For。**默认不认**。
    #: 本部署是 uvicorn 直接对公网、前面没有反代，认了这个头就等于让任何人
    #: 随手伪造来源 IP：审计日志变成写小说，按 IP 的限速也能"一请求换一个
    #: 假 IP"绕过去。真放到 Nginx 后面（那时只有反代能连到本进程）再打开。
    trust_proxy: bool = False

    # --- 登录 / 注册限速（进程内滑动窗口，见 services/ratelimit.py）---
    #: 同一个用户名在窗口内连续失败这么多次就锁住。/api/login 每次要跑
    #: 24 万次 PBKDF2，没有这道闸，它就是一个不用登录就能按下去的 CPU 开关。
    login_max_fails: int = 10
    login_fail_window: int = 300     # 失败计数的统计窗口（秒）
    login_lock_seconds: int = 300    # 触发后锁多久（秒）
    #: 同一个 IP 在窗口内最多注册几个号。/api/register 不要任何凭据，
    #: 不限速就是一台免费的造号机 —— 顺带还能把推送连接池占满。
    register_max_per_ip: int = 5
    register_window: int = 3600

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
    #: 客户端安装包挂在哪个 GitHub 仓库的 Release 上。
    #: 面板上「拉取最新轻舟」就是去这儿拉 —— 开发机不用再把 83MB 传上来。
    #: ⚠ 这条只在仓库**公开**时成立：未登录的 api.github.com 读不到私有仓库。
    github_repo: str = "NekoBoxHQ/canoe"

    # --- 推送（SSE）---
    sse_keepalive: int = 20          # 保活注释间隔（秒）
    sse_max_connections: int = 2000  # 同时在线长连接上限，防打满
    sse_max_per_user: int = 5        # 单账号并发长连接上限

    # --- 业务策略 ---
    default_expire_days: int = 30
    default_max_devices: int = 3
    heartbeat_interval: int = 30
    online_timeout: int = 90

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
