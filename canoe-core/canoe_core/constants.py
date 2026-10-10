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

    # 工具按钮。图标走 artwork.icon() 的线性图标，不再用 emoji ——
    # 所以文案里只有字，图标由控件自己设。
    BTN_UPDATE = "更新"
    BTN_TCPING = "TCping"
    BTN_URLTEST = "URL测试"
    LABEL_RESULT = "输出结果"

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
    #: 登录页那个勾选框。勾上就把账号密码记在本地（密码走 DPAPI 加密）
    LABEL_REMEMBER = "记住账号密码"
    #: 鼠标停在勾选框上时的说明 —— 把"记在哪儿、怎么保护"讲明白，
    #: 免得用户以为是把密码明文扔在某个文件里
    HINT_REMEMBER = "账号密码存在本机，密码用 Windows 凭据加密；换台电脑要重新输"

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

    # ---- 夜色主题（美化稿）------------------------------------------
    # 天空自上而下
    NIGHT_TOP = "#050A16"
    NIGHT_MID = "#091529"
    NIGHT_HORIZON = "#0C2040"
    # 卡片 / 描边
    CARD = "#0D1B33"
    CARD_LINE = "#1C3358"
    CARD_LINE_SOFT = "#152846"
    # 主色：亮蓝 -> 青，用于渐变按钮与强调
    ACCENT = "#2E8BFF"
    ACCENT_DEEP = "#1668E3"
    CYAN = "#22D3EE"
    CYAN_DEEP = "#0EA5C6"
    # 文字
    TEXT = "#EAF2FF"
    TEXT_DIM = "#8AA2C2"
    TEXT_FAINT = "#5B7290"
    # 结果绿 / 告警橙
    GREEN = "#22C55E"
    AMBER = "#F5A524"
    # 山水三层（越近越深）
    MOUNT_FAR = "#12355F"
    MOUNT_MID = "#0D2647"
    MOUNT_NEAR = "#081A33"
    WATER = "#06132A"
    WATER_DEEP = "#040D1E"
    MOON = "#E4EEFF"
    # 工具按钮三种色调
    TOOL_UPDATE = "#4C9BFF"
    TOOL_PING = "#22D3EE"
    TOOL_URL = "#A78BFA"


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

    # 更新通道（客户端「更新」按钮对接这两条）
    #   客户端更新：有没有新版本的 Canoe.exe
    CLIENT_LATEST = "/api/client/latest"
    #   订阅更新：我这条订阅（节点 + 入口）变了没有
    SUBSCRIPTION = "/api/subscription"

    # 推送：SSE 长连接，服务端主动告知配置变更
    EVENTS = "/api/events"

    # 安装包下载（服务端自托管，挂在 StaticFiles 上）
    DOWNLOAD_PREFIX = "/downloads"

    # 管理后台
    ADMIN_USERS = "/api/admin/users"
    ADMIN_NODES = "/api/admin/nodes"
    ADMIN_SESSIONS = "/api/admin/sessions"
    ADMIN_STATS = "/api/admin/stats"
    # 发布新版本（从 GitHub Release 拉，见 services/github.py）
    ADMIN_RELEASES = "/api/admin/releases"


# --------------------------------------------------------------------------
# 线路方向
# --------------------------------------------------------------------------


class Route:
    """线路方向 —— 客户是在国内往外走，还是在国外往回走。

    ★ **服务端定死，客户端只读**。管理员在面板上给每个客户选一个，
    客户端把它当既成事实：拉回来是什么就按什么连，界面上只显示、不给改。

    它决定的是「分流」的含义（"全局"两个方向下都是全走代理，不受影响）：

        出国（OUT）  大陆直连，其余走代理      ← 一直是默认，也是绝大多数人
        回国（IN）   国外直连，国内走代理      ← 人在国外，要连回国内

    两边的规则集是同两份（geosite-cn / geoip-cn），只是出站反着指；
    DNS 也跟着翻（见客户端 kernel.py 的 _dns_config）。
    """

    OUT = "out"
    IN = "in"

    ALL = (OUT, IN)

    #: 界面上那行字（客户端底部、面板那一栏都用它）
    LABELS = {OUT: "出国模式", IN: "回国模式"}

    #: 两个字那种写法，塞进一句话里用（"线路已切到回国"）
    SHORT = {OUT: "出国", IN: "回国"}

    #: 一句话说明，给客户端当提示用
    HINTS = {
        OUT: "绕过局域网和大陆，其余流量走代理",
        IN: "国外直连，国内走代理",
    }

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.ALL

    @classmethod
    def clean(cls, value: str) -> str:
        """认不出来的一律当出国 —— 这是老数据和老客户端的默认值。"""
        return value if value in cls.ALL else cls.OUT


# --------------------------------------------------------------------------
# 协议
# --------------------------------------------------------------------------

PROTOCOL_VERSION = 1

#: 一个客户最多能绑几个节点。
#:
#: 这个数不是随便定的 —— 客户端主界面底部那排「节点灯」就是 6 个位置，
#: 一个灯对应一个节点。服务端放更多出去，客户端也显示不出来，用户会
#: 以为"我绑了 8 个怎么只亮 6 个"。两边共用这一个常量，改就一起改。
MAX_NODES_PER_USER = 6

# 这里原本还有 REAL_FIELD_PREFIX / ENTRY_FIELDS —— 那是「中转层」模型
# 的产物：那时服务端自己渲染 sing-box 配置做中转，客户端只能拿到入口，
# 于是需要一份白名单来守住"真实节点不下发"。现在服务端只发订阅、
# 不转发流量，节点就是管理员贴进来的那行链接，白名单没有守的对象了。


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
