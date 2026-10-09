"""轻舟 / Canoe —— 品牌、文案、API 路径的唯一来源。

客户端和服务端都从这里取常量和文案，改一处两边同步。
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# 命名体系
# --------------------------------------------------------------------------

BRAND_CN = "轻舟"
BRAND_EN = "Canoe"

SLOGAN_CN = "轻舟已过万重山"
SLOGAN_EN = "One boat, one tap."

APP_ID = "canoe"
CLIENT_EXE = "Canoe.exe"
CLIENT_PROCESS = "Canoe"
SERVER_NAME = "Canoe Server"

NAMESPACE_CLIENT = "com.canoe.client"
NAMESPACE_SERVER = "com.canoe.server"

DOMAINS = ("canoe.app", "canoe.im", "qingzhou.app")

# 配置文件目录名（%APPDATA%\Canoe\）
CONFIG_DIR_NAME = "Canoe"


# --------------------------------------------------------------------------
# 界面文案
# --------------------------------------------------------------------------


class Text:
    """界面上的每一句话都在这里。改文案不用翻代码。"""

    # 按钮（带图标，图标用 emoji，不需要额外的图片资源）
    BTN_LAUNCH = "🚀  启航"
    BTN_DOCK = "🚢  靠岸"
    BTN_LAUNCH_BUSY = "🚀  渡江中…"
    BTN_DOCK_BUSY = "🚢  靠岸中…"
    BTN_REGISTER = "注 册"
    BTN_LOGIN = "登 录"
    BTN_GUEST = "直接体验（跳过注册）"

    # 工具按钮
    BTN_UPDATE = "🔄\n更新"
    BTN_TCPING = "📡\nTCping"
    BTN_URLTEST = "🔗\nURL测试"
    LABEL_LOG = "📋  输出日志"

    # 日志面板首行
    LOG_READY = "准备就绪，等待操作…"

    # 状态（需求指定的四态）
    ST_CONNECTING = "渡江中…"
    ST_CONNECTED = "已启航"
    ST_DISCONNECTED = "已靠岸"
    ST_ERROR = "风浪太大，请重试"

    # 页面标题
    TITLE_LOGIN = "登舟"
    TITLE_REGISTER = "造舟"
    TITLE_MAIN = BRAND_CN

    # 提示
    HINT_NO_ACCOUNT = "还没有渡口？"
    HINT_HAS_ACCOUNT = "已有渡口？"
    LINK_TO_REGISTER = "去造舟"
    LINK_TO_LOGIN = "去登舟"

    SUBTITLE_LOGIN = "报上名号，登舟渡江"
    SUBTITLE_REGISTER = "用户名 3-32 位字母数字，密码至少 8 位"

    LABEL_SERVER = "渡口地址"
    LABEL_NODE = "航路"
    # 选项标签（系统代理 / TUN 模式 / 分流 / 全局）放在 canoe_client.options 里，
    # 因为它们和取值常量 MODE_* / PROFILE_* 是一对，分开放容易走散。

    BTN_LOGOUT = "离舟"
    PLACEHOLDER_USERNAME = "用户名"
    PLACEHOLDER_PASSWORD = "密码"
    PLACEHOLDER_PASSWORD2 = "确认密码"

    @staticmethod
    def progress(action: str) -> str:
        """按钮忙碌态文案，如 启航 -> 启航中…"""
        return f"{action}中…"


# --------------------------------------------------------------------------
# 配色（极简线条 / 深蓝 · 墨青 · 白）
# --------------------------------------------------------------------------


class Palette:
    INK = "#0D1622"          # 墨底
    SURFACE = "#141F2E"      # 卡片
    LINE = "#22303F"         # 描边
    DEEP_BLUE = "#1D4E89"    # 深蓝（主色）
    DEEP_BLUE_HOVER = "#2560A6"
    INK_CYAN = "#2A7F8F"     # 墨青（运行态）
    INK_CYAN_HOVER = "#33939F"
    WHITE = "#F2F6FA"
    MUTED = "#7A8DA0"
    DIM = "#4A5A6B"
    WARN = "#C2603C"         # 风浪（错误）


# --------------------------------------------------------------------------
# API 路径
# --------------------------------------------------------------------------


class Api:
    """需求指定的 5 个端点 + 管理/辅助端点。"""

    # 需求指定
    REGISTER = "/api/register"
    LOGIN = "/api/login"
    CONFIG = "/api/config"
    HEARTBEAT = "/api/heartbeat"
    LOGOUT = "/api/logout"

    # 对本项目建议 API 的补充：结束一次代理会话但**保留登录令牌**。
    # 点「靠岸」时用这个，而不是 /api/logout —— 后者会吊销令牌，
    # 用户每次靠岸都得重新登舟。
    SESSION_STOP = "/api/session/stop"

    # 辅助
    ME = "/api/me"
    HEALTH = "/api/health"

    # 管理后台
    ADMIN_USERS = "/api/admin/users"
    ADMIN_NODES = "/api/admin/nodes"
    ADMIN_SESSIONS = "/api/admin/sessions"
    ADMIN_STATS = "/api/admin/stats"
    ADMIN_RELAY_CONFIG = "/api/admin/relay/config"
    ADMIN_RELAY_RELOAD = "/api/admin/relay/reload"


# --------------------------------------------------------------------------
# 协议
# --------------------------------------------------------------------------

PROTOCOL_VERSION = 1

#: 真实节点字段前缀。客户端侧的任何代码、测试都不应出现这个前缀的字段。
REAL_FIELD_PREFIX = "real_"

#: 客户端允许从服务端拿到的入口字段白名单（与 EntryPayload 保持一致）
ENTRY_FIELDS = frozenset(
    {"transport", "host", "port", "uuid", "path", "sni", "tls", "insecure"}
)


class ErrorCode:
    OK = "ok"
    BAD_CREDENTIALS = "bad_credentials"
    UNAUTHORIZED = "unauthorized"
    BANNED = "banned"
    EXPIRED = "expired"
    DEVICE_LIMIT = "device_limit"
    DEVICE_REVOKED = "device_revoked"
    NOT_FOUND = "not_found"
    NO_NODE = "no_node"
    NETWORK = "network_error"
    TIMEOUT = "timeout"
    TLS = "tls_error"
    INTERNAL = "internal_error"
