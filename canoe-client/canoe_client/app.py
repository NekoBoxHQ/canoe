"""轻舟客户端入口：在「登舟页」和「主界面」之间切换。"""
from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from canoe_core import BRAND_CN, SLOGAN_CN

from . import sysproxy
from .api import api
from .config import ASSETS_DIR, BIN_DIR, CONFIG_DIR, CONFIG_FILE, config
from .events import stream
from .kernel import kernel
from .options import RunOptions
from .session import session
from .ui.auth_view import AuthView
from .ui.main_view import MainView
from .ui.style import qss
from .ui.tray import Tray, tray_available


class CanoeApp:
    def __init__(self, icon: QIcon | None = None) -> None:
        self.auth = AuthView()
        self.main = MainView()

        self.auth.logged_in.connect(self._on_logged_in)
        self.main.logged_out.connect(self._on_logged_out)
        # 服务端推来的事件（配置变更 / 踢下线 / 新版本）交给主界面处理
        stream.event.connect(self.main.on_push_event)

        # 托盘：关窗收起来而不是退出，只有右键托盘 ->「退出」才真退。
        # 没有托盘的环境（少见）保持原样：关窗就是关窗。
        self.tray: Tray | None = None
        if tray_available():
            self.tray = Tray(icon)
            self.tray.show_requested.connect(self._show_window)
            self.tray.quit_requested.connect(self.quit)
            self.main.close_to_tray = True
            self.tray.show()

    def start(self) -> None:
        self.auth.show()

    def _show_window(self) -> None:
        """把界面叫回来（托盘菜单 / 双击图标）。"""
        if self.main.isVisible():
            self.main.raise_()
            self.main.activateWindow()
            return
        if api.token:
            self.main.show()
            self.main.raise_()
            self.main.activateWindow()
        else:
            self.auth.show()
            self.auth.raise_()

    def quit(self) -> None:
        """真正的退出 —— 托盘右键菜单里那一条。

        shutdown() 挂在 aboutToQuit 上，所以这里只需要让 Qt 退出。
        """
        self.main.force_close()
        QApplication.instance().quit()

    def _on_logged_in(self, username: str) -> None:
        # 节点名是登录时服务端一起给的（主界面要在启航前就显示它）
        self.main.start_with_node(username, self.auth.last_node_name)
        self.main.refresh()
        self.main.show()
        self.auth.hide()
        # 挂上推送长连接 —— 管理员改节点 / 封禁时能立刻知道，不用等心跳
        if api.token:
            stream.start(api.token)

    def _on_logged_out(self) -> None:
        stream.stop()
        self.auth.login_user.clear()
        self.auth.login_pass.clear()
        self.auth.login_err.setText("")
        self.auth.last_node_name = ""
        self.main.hide()
        # 可能是被"踢下线"而从托盘里叫回来的，所以这里要 raise 一下
        self.auth.show()
        self.auth.raise_()
        self.auth.activateWindow()

    def shutdown(self) -> None:
        """退出前兜底清理：系统代理必须还原、内核必须停。

        少这一步，用户关掉程序后会直接断网。

        ★ 顺序：**先还原系统代理，再停内核**。内核（TUN 模式）停起来要一两秒，
        先把代理还回去，这段时间用户也不会没网。理由同 `MainView._dock`。
        """
        try:
            stream.stop()
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

    # 订阅加解密能不能跑起来。
    # 打包后这条路最容易坏（cryptography 带 Rust 绑定，PyInstaller 漏一个
    # 动态库就是"登录成功但订阅永远解不开"），而它在界面上只表现为一句
    # 看不懂的报错。这里主动跑一遍，坏了直接写在报告里。
    try:
        from canoe_core import Envelope, new_sub_key, seal, unseal

        key = new_sub_key()
        probe = "canoe-selftest"
        report["crypto"] = {
            "ok": unseal(seal(probe, key, revision="selftest"), key) == probe,
            "empty_envelope_ok": Envelope().is_empty,
        }
    except Exception as exc:  # noqa: BLE001 - 自检不能因为这一步失败就崩
        report["crypto"] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}

    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)

    out = CONFIG_DIR / "selftest.txt"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"\n已写入 {out}")
    except OSError:
        pass

    ok = (
        report["singbox_found"]
        and report["bin_dir_exists"]
        and bool(report["crypto"].get("ok"))
    )
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

    controller = CanoeApp(icon)
    if controller.tray is not None:
        # 关窗只是收起来，窗口全没了也不许 Qt 自己退出 —— 退出由托盘菜单说了算
        app.setQuitOnLastWindowClosed(False)

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
