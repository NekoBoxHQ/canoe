"""轻舟客户端本地配置。

只存**非敏感**信息：界面偏好、内核路径、本地端口、设备号。
账号密码不落盘（阶段3 起账号在服务端，本地只留一个登录令牌在内存里）。
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from pathlib import Path
from typing import Any

from canoe_core import CONFIG_DIR_NAME

# ---------------------------------------------------------------------------
# ★ 服务端地址 —— 写死的 ★
# ---------------------------------------------------------------------------
#
#   客户端是发给用户"下载即用"的，**不给任何配置入口** —— 用户不该、
#   也不能自己填服务器地址。所以这里写死。
#
#   换服务器 = 重新发一版客户端。
#
#   本地开发/联调时可以用环境变量临时覆盖（不影响打包发布的行为）：
#       set CANOE_SERVER_URL=http://127.0.0.1:8010
#
SERVER_BASE = "https://canoe.s-ui.com:58588"

DEFAULTS: dict[str, Any] = {
    "singbox_path": "",            # 留空则自动在 bin/ 与 PATH 里找
    "device_id": "",               # 首次运行自动生成（见 device_id 属性）
    # 界面选项（见 options.py 的 RunOptions）
    "options": {
        "profile": "split",         # 分流：绕过局域网和大陆
        "use_system_proxy": True,   # 默认勾系统代理
        "use_tun": False,           # TUN 默认关，可与系统代理同时开
        "tun_ipv6": True,           # TUN 恒双栈
        "mixed_port": 20818,
        "log_level": "info",
    },
}


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / CONFIG_DIR_NAME


CONFIG_DIR = _config_dir()
CONFIG_FILE = CONFIG_DIR / "client.json"

# 打包后 bin/ 在 exe 同级（_internal/bin）；源码运行时是 canoe-client/bin/
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"

# 同理：打包后在 _internal/assets，源码运行时是 canoe-client/assets
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


class ClientConfig:
    def __init__(self) -> None:
        self._data: dict[str, Any] = json.loads(json.dumps(DEFAULTS))  # 深拷贝
        self.load()

    def load(self) -> None:
        if not CONFIG_FILE.exists():
            return
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # 配置损坏就用默认值，不要因此起不来

        if not isinstance(saved, dict):
            return
        for key, value in saved.items():
            if key not in DEFAULTS:
                continue
            if key == "options" and isinstance(value, dict):
                self._data["options"].update(
                    {k: v for k, v in value.items() if k in DEFAULTS["options"]}
                )
            else:
                self._data[key] = value

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # 写不了配置不该让程序崩

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def set_and_save(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if key in DEFAULTS:
                self._data[key] = value
        self.save()

    # ------------------------------------------------------------------
    # 服务端相关（全部写死或派生，界面里没有对应入口）
    # ------------------------------------------------------------------
    @property
    def server_url(self) -> str:
        """服务端根地址。写死，只有开发时能用环境变量顶掉。"""
        return (os.environ.get("CANOE_SERVER_URL") or SERVER_BASE).rstrip("/")

    @property
    def update_url(self) -> str:
        """客户端更新检查地址。由服务端地址派生，不单独配置。"""
        from canoe_core import Api

        return f"{self.server_url}{Api.CLIENT_LATEST}"

    @property
    def ca_bundle(self) -> str | bool:
        """HTTPS 校验用的 CA。

        **默认真校验**（返回 True）—— 生产是真实证书，这里绝不能放宽。

        只有本地拿 run_local_https.py 起自签证书联调时，才用环境变量
        指一张 CA 进来：

            set CANOE_CA_BUNDLE=canoe-server/data/certs/local-cert.pem

        注意是「换成只信这一张」，不是「关掉校验」：关掉的话中间人
        就能伪造渡口，客户端会把令牌交出去。文件不存在也一律回退到
        真校验，免得写错路径静默降级成不校验。
        """
        path = (os.environ.get("CANOE_CA_BUNDLE") or "").strip()
        if path and Path(path).is_file():
            return path
        return True

    @property
    def device_id(self) -> str:
        """本机设备号。首次运行生成并落盘，之后一直用它。

        服务端拿它做「设备数上限」和设备级令牌绑定，
        所以必须稳定 —— 每次启动都换的话，用户会被自己的设备挤下线。
        """
        did = str(self._data.get("device_id") or "")
        if not did:
            did = uuid.uuid4().hex
            self._data["device_id"] = did
            self.save()
        return did

    @property
    def device_name(self) -> str:
        try:
            return socket.gethostname()[:64]
        except OSError:
            return ""

    # ------------------------------------------------------------------
    def find_singbox(self) -> Path | None:
        """按 配置 -> bin/ -> 同级 -> PATH 的顺序找 sing-box。"""
        configured = self._data.get("singbox_path")
        if configured and Path(configured).is_file():
            return Path(configured)

        for name in ("sing-box.exe", "sing-box"):
            for candidate in (BIN_DIR / name, BIN_DIR.parent / name):
                if candidate.is_file():
                    return candidate

        from shutil import which

        found = which("sing-box")
        return Path(found) if found else None


config = ClientConfig()
