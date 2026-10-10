"""运行选项 —— 界面上用户能选的部分。

界面是两排：

    第一排（二选一）  ○ 分流   ○ 全局
    第二排（可并存）  ☑ 系统代理   ☐ TUN 模式

两排的互斥性不一样，这是刻意的：

  · 第一排是**分流模式**，两种模式天然互斥，用单选。
  · 第二排是**接管方式**，系统代理和 TUN 可以同时开 —— 所以用两个独立复选框，
    而不是单选。早先用一个 `mode` 字段表达，勾了 TUN 就会把系统代理顶掉，
    那是我把它做错了。

TUN 模式始终同时接管 IPv4 和 IPv6，不暴露开关：关掉它只会让走 IPv6 的流量
绕过代理，纯属坑，不是一个该给用户的选项。

★ 还有一件**界面管不着**的东西：线路方向（`route_mode`）。它是服务端
给这个客户定的（出国 / 回国），客户端只显示、不给改。它管的是"分流"
这个词到底什么意思 —— 全局模式下两个方向没区别，所以那一排不受影响。
见 canoe_core.constants.Route。
"""
from __future__ import annotations

from dataclasses import dataclass

from canoe_core import Route

# --------------------------------------------------------------------------
# 第一排：分流模式（二选一）
# --------------------------------------------------------------------------

PROFILE_SPLIT = "split"      # 分流：按线路方向分（默认）
PROFILE_GLOBAL = "global"    # 全局：所有流量都走代理

PROFILE_LABELS = {
    PROFILE_SPLIT: "分流",
    PROFILE_GLOBAL: "全局",
}

#: 规则里的两种出站。这里写成常量是因为它俩现在会**按方向反过来**，
#: 散在各处的 "direct"/"proxy" 字面量很容易改漏一处。
OUT_DIRECT = "direct"
OUT_PROXY = "proxy"

# --------------------------------------------------------------------------
# 第二排：接管方式（可并存）
# --------------------------------------------------------------------------

LABEL_SYSTEM_PROXY = "系统代理"
LABEL_TUN = "TUN 模式"


@dataclass
class RunOptions:
    #: 第一排：分流模式。默认分流（绕过局域网和大陆）。
    profile: str = PROFILE_SPLIT

    #: 线路方向：out=出国（默认）/ in=回国。
    #:
    #: ★ **服务端给的，客户端只读**。本地存的这份只是个缓存 —— 冷启动
    #: 没网时先拿它顶着，每次登舟/启航/拉订阅拿到服务端的值就覆盖。
    #: 界面上只显示、不给改（见 ui/main_view.py 的 route_label）。
    route_mode: str = Route.OUT

    #: 第二排：是否设置 Windows 系统代理。默认开。
    use_system_proxy: bool = True

    #: 第二排：是否开启 TUN 全局接管。默认关。
    #: 可以和 use_system_proxy 同时为真。
    use_tun: bool = False

    #: TUN 是否同时接管 IPv6。
    #: 需求里说"TUN开启的话V4V6一起开启，不管系统是否带V6，开启无害"，
    #: 所以恒为 True，界面上不暴露。
    tun_ipv6: bool = True

    #: 系统代理模式下本地 mixed 入站的端口
    mixed_port: int = 20818

    #: TUN 网卡地址
    tun_address_v4: str = "172.19.0.1/30"
    tun_address_v6: str = "fdfe:dcba:9876::1/126"

    #: 内核日志级别。排查问题时调成 info / debug。
    log_level: str = "warn"

    # ---------------------------------------------------------------- 派生
    @property
    def split_active(self) -> bool:
        """「分流」这一档开着吗。全局模式下什么规则都不挂。"""
        return self.profile == PROFILE_SPLIT

    @property
    def bypass_lan(self) -> bool:
        """局域网直连 —— 两个方向都这样（家里的打印机不该绕到国外去）。"""
        return self.split_active

    @property
    def china_outbound(self) -> str:
        """大陆域名 / IP 走哪个出站。

        出国：直连 —— 人在国内，大陆站本来就不该绕出去
        回国：代理 —— 人在国外，要连回国内，大陆站必须走那个国内节点
        全局：代理（全局不挂规则，这里只是为了调用方不用再看 profile）
        """
        if not self.split_active:
            return OUT_PROXY
        return OUT_DIRECT if self.route_mode == Route.OUT else OUT_PROXY

    @property
    def final_outbound(self) -> str:
        """没被任何规则命中的那部分走哪个出站。

        出国 / 全局：代理（默认都出去）
        回国：直连（国外流量就该直着走，只有大陆站才绕回去）
        """
        if self.profile == PROFILE_GLOBAL:
            return OUT_PROXY
        return OUT_PROXY if self.route_mode == Route.OUT else OUT_DIRECT

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "route_mode": self.route_mode,
            "use_system_proxy": self.use_system_proxy,
            "use_tun": self.use_tun,
            "tun_ipv6": self.tun_ipv6,
            "mixed_port": self.mixed_port,
            "log_level": self.log_level,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunOptions":
        opts = cls()
        data = data or {}

        for key in ("profile", "route_mode", "use_system_proxy", "use_tun",
                    "tun_ipv6", "mixed_port", "log_level"):
            if key in data:
                setattr(opts, key, data[key])

        # --- 兼容旧版配置 ---
        # v1: mode = "system_proxy" | "tun"
        if "mode" in data and "use_tun" not in data:
            opts.use_tun = data["mode"] == "tun"
            opts.use_system_proxy = not opts.use_tun
        # v1 更早: bypass_lan / bypass_china 两个布尔
        if "profile" not in data and ("bypass_lan" in data or "bypass_china" in data):
            both_on = bool(data.get("bypass_lan", True)) and bool(data.get("bypass_china", True))
            opts.profile = PROFILE_SPLIT if both_on else PROFILE_GLOBAL

        if opts.profile not in PROFILE_LABELS:
            opts.profile = PROFILE_SPLIT
        # 线路方向：老配置文件里没有这一项 -> 出国（跟以前的行为一样）。
        # 服务端给了别的值也认不出来就当出国，别让它把内核配置搞挂。
        opts.route_mode = Route.clean(opts.route_mode)
        return opts
