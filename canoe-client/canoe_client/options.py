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
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# 第一排：分流模式（二选一）
# --------------------------------------------------------------------------

PROFILE_SPLIT = "split"      # 分流：局域网与大陆直连，其余走代理（默认）
PROFILE_GLOBAL = "global"    # 全局：所有流量都走代理

PROFILE_LABELS = {
    PROFILE_SPLIT: "分流",
    PROFILE_GLOBAL: "全局",
}

# --------------------------------------------------------------------------
# 第二排：接管方式（可并存）
# --------------------------------------------------------------------------

LABEL_SYSTEM_PROXY = "系统代理"
LABEL_TUN = "TUN 模式"


@dataclass
class RunOptions:
    #: 第一排：分流模式。默认分流（绕过局域网和大陆）。
    profile: str = PROFILE_SPLIT

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
    def bypass_lan(self) -> bool:
        """局域网直连。分流模式下开，全局模式下关。"""
        return self.profile == PROFILE_SPLIT

    @property
    def bypass_china(self) -> bool:
        """大陆直连。分流模式下开，全局模式下关。"""
        return self.profile == PROFILE_SPLIT

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
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

        for key in ("profile", "use_system_proxy", "use_tun",
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
        return opts
