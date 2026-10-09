"""轻舟客户端本地配置。

只存**非敏感**信息：界面偏好、内核路径、本地端口。
账号密码在 localauth.py（阶段1）里单独存，阶段3 会换成服务端 token。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from canoe_core import CONFIG_DIR_NAME

DEFAULTS: dict[str, Any] = {
    "singbox_path": "",            # 留空则自动在 bin/ 与 PATH 里找
    # 界面选项（阶段1 的可选部分，见 options.py）
    "options": {
        "mode": "system_proxy",    # 默认系统代理
        "bypass_lan": True,
        "bypass_china": True,
        "tun_ipv6": True,
        "mixed_port": 20818,
        "log_level": "warn",
    },
}


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / CONFIG_DIR_NAME


CONFIG_DIR = _config_dir()
CONFIG_FILE = CONFIG_DIR / "client.json"

# 打包后 bin/ 在 exe 同级（_internal/bin）；源码运行时是 canoe-client/bin/
BIN_DIR = Path(__file__).resolve().parent.parent / "bin"


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
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def set_and_save(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if key in DEFAULTS:
                self._data[key] = value
        self.save()

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
