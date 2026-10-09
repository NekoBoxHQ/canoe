"""轻舟客户端入口：在「登舟页」和「主界面」之间切换。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from canoe_core import BRAND_CN, SLOGAN_CN

from . import sysproxy
from .config import BIN_DIR, CONFIG_DIR, CONFIG_FILE, config
from .kernel import kernel
from .options import RunOptions
from .session import session
from .ui.auth_view import AuthView
from .ui.main_view import MainView
from .ui.style import qss

# 打包后 assets 在 _internal/assets 下
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


class CanoeApp:
    def __init__(self) -> None:
        self.auth = AuthView()
        self.main = MainView()

        self.auth.logged_in.connect(self._on_logged_in)
        self.main.logged_out.connect(self._on_logged_out)

    def start(self) -> None:
        self.auth.show()

    def _on_logged_in(self, username: str) -> None:
        self.main.start_with_test_node(username)
        self.main.refresh()
        self.main.show()
        self.auth.hide()

    def _on_logged_out(self) -> None:
        self.auth.login_user.clear()
        self.auth.login_pass.clear()
        self.auth.login_err.setText("")
        self.auth.show()
        self.main.hide()

    def shutdown(self) -> None:
        """退出前兜底清理：系统代理必须还原、内核必须停。

        少这一步，用户关掉程序后会直接断网。

        ★ 顺序：**先还原系统代理，再停内核**。内核（TUN 模式）停起来要一两秒，
        先把代理还回去，这段时间用户也不会没网。理由同 `MainView._dock`。
        """
        try:
            if sysproxy.has_backup():
                sysproxy.clear_proxy()
            if session.sailing or kernel.running:
                kernel.stop()
        except Exception:  # noqa: BLE001 - 退出流程不能抛
            try:
                sysproxy.clear_proxy()
            except Exception:  # noqa: BLE001
                pass


def _app_icon() -> QIcon | None:
    for name in ("canoe.ico", "canoe.png"):
        path = ASSETS_DIR / name
        if path.is_file():
            return QIcon(str(path))
    return None


def run_selftest() -> int:
    """`Canoe.exe --selftest` —— 自检，不启动界面。

    打包后的程序没有控制台，用户报"打不开"时让他跑这个，
    结果会写到 %APPDATA%\\Canoe\\selftest.txt，一眼就能看出缺什么。
    """
    import json
    import platform
    import subprocess

    from canoe_core import VERSION

    report: dict = {
        "version": VERSION,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "python": platform.python_version(),
        "config_file": str(CONFIG_FILE),
        "bin_dir": str(BIN_DIR),
        "bin_dir_exists": BIN_DIR.is_dir(),
        "assets": {},
    }

    for name in ("canoe.ico", "canoe.png"):
        report["assets"][name] = (ASSETS_DIR / name).is_file()

    singbox = config.find_singbox()
    report["singbox"] = str(singbox) if singbox else None
    report["singbox_found"] = singbox is not None
    if singbox:
        try:
            proc = subprocess.run(
                [str(singbox), "version"], capture_output=True, text=True, timeout=20
            )
            report["singbox_version"] = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        except (OSError, subprocess.SubprocessError) as exc:
            report["singbox_version"] = f"<失败: {exc}>"

    ruleset_dir = BIN_DIR / "ruleset"
    report["ruleset"] = (
        {p.name: p.stat().st_size for p in ruleset_dir.glob("*.srs")}
        if ruleset_dir.is_dir()
        else {}
    )

    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)

    out = CONFIG_DIR / "selftest.txt"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"\n已写入 {out}")
    except OSError:
        pass

    ok = report["singbox_found"] and report["bin_dir_exists"]
    return 0 if ok else 2


def main() -> int:
    if "--selftest" in sys.argv:
        return run_selftest()

    app = QApplication(sys.argv)
    app.setApplicationName(BRAND_CN)
    app.setApplicationDisplayName(f"{BRAND_CN} · {SLOGAN_CN}")
    app.setStyleSheet(qss())

    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    controller = CanoeApp()

    # 启动自愈：上次如果是崩溃退出的，注册表里可能留着"代理开着但指向死端口"
    # 的脏状态 —— 那会让用户从打开程序到点启航的这段时间完全没网。先修好它。
    try:
        opts = RunOptions.from_dict(config["options"])
        if sysproxy.heal_on_start(int(opts.mixed_port)):
            print("[canoe] 已清理上次异常退出残留的系统代理设置")
    except Exception:  # noqa: BLE001 - 自愈失败不能拦住启动
        pass

    app.aboutToQuit.connect(controller.shutdown)
    controller.start()

    _ = BIN_DIR  # 保持 import 可见，便于排查内核目录
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
