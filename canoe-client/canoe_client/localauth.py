"""★ 阶段1 专用 · 本地假注册 / 假登录 ★

阶段1 还没有服务端，所以账号先存在本地，用来把界面流程跑通。

════════════════════════════════════════════════════════════════
  这是**演示用途**，不是真实账号系统：
    · 账号文件在 %APPDATA%\\Canoe\\accounts.json
    · 密码用 pbkdf2 哈希存储（不存明文），但任何人都能改这个文件
    · 阶段3 会被服务端的 /api/register 与 /api/login 取代

  替换点很小：main_view 只调 login() / register() 两个函数，
  阶段3 把这两个函数换成 api 调用即可，界面代码不用动。
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass

from canoe_core import hash_password, verify_password

from .config import CONFIG_DIR

ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
MIN_PASSWORD_LEN = 8


class LocalAuthError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Account:
    username: str


def _load() -> dict:
    if not ACCOUNTS_FILE.exists():
        return {}
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def validate(username: str, password: str, confirm: str | None = None) -> None:
    """和阶段3 服务端保持一致的校验规则，免得以后对不上。"""
    if not username or not password:
        raise LocalAuthError("请填写用户名和密码")
    if not USERNAME_RE.match(username):
        raise LocalAuthError("用户名需为 3-32 位字母、数字、下划线或短横线")
    if len(password) < MIN_PASSWORD_LEN:
        raise LocalAuthError(f"密码至少 {MIN_PASSWORD_LEN} 位")
    if confirm is not None and password != confirm:
        raise LocalAuthError("两次输入的密码不一致")


def register(username: str, password: str) -> Account:
    validate(username, password)
    data = _load()
    if username in data:
        raise LocalAuthError("用户名已存在")
    data[username] = {"password_hash": hash_password(password)}
    _save(data)
    return Account(username=username)


def login(username: str, password: str) -> Account:
    if not username or not password:
        raise LocalAuthError("请填写用户名和密码")

    data = _load()
    record = data.get(username)
    if record is None or not verify_password(password, record.get("password_hash", "")):
        raise LocalAuthError("用户名或密码错误")
    return Account(username=username)


def list_accounts() -> list[str]:
    return sorted(_load().keys())


# --------------------------------------------------------------------------
# 阶段1 测试便利：跳过注册直接进主界面
# --------------------------------------------------------------------------

#: 「直接体验」用的账号名。密码是每次随机生成的，没人能用密码登进来 ——
#: 它只能通过下面的 login_as_guest() 进入。
GUEST_USERNAME = "访客"


def login_as_guest() -> Account:
    """跳过注册，直接体验。

    第一次点会创建一个"访客"账号，之后复用同一个。
    存在意义就是阶段1 测试时不用为了看界面先造个账号。
    """
    data = _load()
    if GUEST_USERNAME not in data:
        data[GUEST_USERNAME] = {"password_hash": hash_password(secrets.token_urlsafe(24))}
        _save(data)
    return Account(username=GUEST_USERNAME)
