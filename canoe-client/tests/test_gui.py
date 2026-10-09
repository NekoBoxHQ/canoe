"""阶段1 · GUI 端到端测试（离屏运行，不弹窗）。

会真的拉起 sing-box 并真的走一次代理，但**系统代理用 mock 替换**，
不会改你机器的注册表。

覆盖阶段1 的验收标准：
    1. 界面能打开，三页都在
    2. 能注册 / 登录（本地假账号）
    3. 主界面只有节点名 + 启航 + 靠岸（外加状态与可选设置）
    4. 点启航能真的走代理
    5. 点靠岸能停，且代理确实失效
    6. 不显示节点地址、端口、协议、密码

用法：
    set QT_QPA_PLATFORM=offscreen
    python tests/test_gui.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton  # noqa: E402

from canoe_client import localauth  # noqa: E402
from canoe_client.config import config  # noqa: E402
from canoe_client.kernel import kernel  # noqa: E402
from canoe_client.session import STATE_DOCKED, STATE_SAILED, session  # noqa: E402
from canoe_client.ui import main_view as mv  # noqa: E402
from canoe_client.ui.auth_view import AuthView  # noqa: E402
from canoe_client.ui.main_view import MainView  # noqa: E402
from canoe_client.ui.style import qss  # noqa: E402
from canoe_core import Text  # noqa: E402

passed = failed = skipped = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def skip(label: str, why: str) -> None:
    global skipped
    skipped += 1
    print(f"  [跳过] {label} —— {why}")


class FakeSysProxy:
    """记录调用，不碰真实注册表。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        # 和真实 sysproxy 一样：设过代理后 has_backup() 为真，靠岸清理后为假。
        # MainView._dock 靠它决定要不要还原，所以要如实模拟。
        self._active = False

    def set_proxy(self, host: str, port: int, bypass: str = "") -> None:
        self.calls.append(("set", host, port))
        self._active = True

    def clear_proxy(self) -> None:
        self.calls.append(("clear",))
        self._active = False

    def has_backup(self) -> bool:
        return self._active

    def current_proxy(self) -> dict:
        return {}


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(app: QApplication, predicate, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def curl_through_proxy(port: int, url: str = "https://api.ipify.org", timeout: int = 20) -> str:
    from shutil import which

    proc = subprocess.run(
        [which("curl") or "curl", "-sS", "-m", str(timeout),
         "--socks5-hostname", f"127.0.0.1:{port}", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip()


def main() -> int:
    print("\n== 轻舟 · 阶段1 GUI 端到端测试 ==\n")

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(qss())

    fake_proxy = FakeSysProxy()
    mv.sysproxy = fake_proxy  # type: ignore[assignment]

    # 用独立的账号文件，别污染真实用户数据
    tmp_accounts = Path(tempfile.mkdtemp(prefix="canoe-test-")) / "accounts.json"
    localauth.ACCOUNTS_FILE = tmp_accounts

    # --- 1. 界面构造 ---
    print("[1] 界面")
    auth = AuthView()
    check("造舟/登舟页构造成功", auth is not None)
    check("含用户名/密码/确认密码控件",
          all(hasattr(auth, n) for n in ("login_user", "login_pass", "reg_user", "reg_pass", "reg_pass2")))
    check("两页都在（登舟 / 造舟）", auth.stack.count() == 2)
    check(f"按钮文案 = 「{Text.BTN_LOGIN}」「{Text.BTN_REGISTER}」",
          auth.login_btn.text() == Text.BTN_LOGIN and auth.reg_btn.text() == Text.BTN_REGISTER,
          f"{auth.login_btn.text()!r} {auth.reg_btn.text()!r}")

    view = MainView()
    check("主界面构造成功", view is not None)
    check("★ 有节点名称", hasattr(view, "node_label"))
    check(f"★ 有启航按钮（文案「{Text.BTN_LAUNCH}」）",
          view.launch_btn.text() == Text.BTN_LAUNCH, view.launch_btn.text())
    check(f"★ 有靠岸按钮（文案「{Text.BTN_DOCK}」）",
          view.dock_btn.text() == Text.BTN_DOCK, view.dock_btn.text())
    check(f"初始状态 = 「{Text.ST_DISCONNECTED}」",
          view.status_label.text() == Text.ST_DISCONNECTED, view.status_label.text())
    check("初始靠岸按钮不可用",
          not view.dock_btn.isEnabled() and view.launch_btn.isEnabled())

    # --- 2. 界面不该出现的东西 ---
    print("\n[2] 界面不该出现节点的敏感信息")
    button_texts = [b.text() for b in view.findChildren(QPushButton)]
    button_texts += [b.text() for b in view.findChildren(QToolButton)]
    forbidden = ("导出", "查看", "配置", "地址", "端口", "复制", "链接", "二维码", "分享")
    check("★ 没有导出/查看配置类按钮",
          not any(any(f in t for f in forbidden) for t in button_texts), f"{button_texts}")

    all_text = " ".join(lbl.text() for lbl in view.findChildren(QLabel))
    secrets = ("one.leycc.com", "33222", "shadowsocks", "2022-blake3",
               "hlKPbKui", "vless", "vmess", "AES", "密码")
    leaked = [s for s in secrets if s in all_text]
    check("★ 界面上不出现地址/端口/协议/密钥", not leaked, f"泄漏: {leaked}")
    check("★ 界面上没有 hidden 状态泄露", view.node_label.text() in ("—", ""))

    # --- 3. 本地注册 / 登录 ---
    print("\n[3] 造舟与登舟（阶段1 本地假账号）")
    username = f"canoe_{uuid.uuid4().hex[:8]}"
    password = "canoe-pass-123"

    try:
        localauth.register(username, password)
        check("注册成功", True)
    except localauth.LocalAuthError as exc:
        check("注册成功", False, exc.message)

    try:
        localauth.register(username, password)
        check("重复注册应当失败", False)
    except localauth.LocalAuthError:
        check("重复注册被拒绝", True)

    try:
        localauth.login(username, "wrong-password")
        check("错误密码应当失败", False)
    except localauth.LocalAuthError:
        check("错误密码被拒绝", True)

    try:
        localauth.validate("ab", password)
        check("用户名过短应当失败", False)
    except localauth.LocalAuthError:
        check("用户名过短被拒绝", True)

    # 「直接体验」：跳过注册
    print("\n[3.1] 直接体验（跳过注册）")
    check("登录页有「直接体验」按钮", hasattr(auth, "guest_btn"),
          "缺 guest_btn")
    if hasattr(auth, "guest_btn"):
        check(f"按钮文案 = 「{Text.BTN_GUEST}」",
              auth.guest_btn.text() == Text.BTN_GUEST, auth.guest_btn.text())

        guest_logged: list[str] = []
        auth.logged_in.connect(guest_logged.append)
        auth.stack.setCurrentIndex(0)
        auth.guest_btn.click()
        ok = wait_for(app, lambda: bool(guest_logged), timeout=20)
        check("★ 点「直接体验」能跳过注册直接进入", ok, auth.login_err.text())
        if ok:
            check("访客账号名正确", guest_logged[0] == localauth.GUEST_USERNAME,
                  guest_logged[0])
            # 复位，继续走正常登录流程
            auth.logged_in.disconnect(guest_logged.append)

    # 走真实的界面流程
    auth.stack.setCurrentIndex(0)
    auth.login_user.setText(username)
    auth.login_pass.setText(password)
    logged_in: list[str] = []
    auth.logged_in.connect(logged_in.append)
    auth.login_btn.click()
    ok = wait_for(app, lambda: bool(logged_in), timeout=20)
    check("登舟成功并触发 logged_in", ok, auth.login_err.text())
    if not ok:
        return 1

    # --- 4. 主界面显示节点名 ---
    print("\n[4] 主界面显示节点名")
    view.start_with_test_node(logged_in[0])
    view.refresh()
    check("★ 显示了节点名称", view.node_label.text() not in ("", "—"), view.node_label.text())
    check("显示了账号名", view.account_label.text() == username, view.account_label.text())

    # --- 5. 两排选项 ---
    print("\n[5] 两排选项（上：分流/全局　下：系统代理/TUN，可并存）")
    check("★ 上排默认选「分流」",
          view.rb_split.isChecked() and not view.rb_global.isChecked())
    check("★ 下排默认勾「系统代理」、不勾「TUN 模式」",
          view.cb_system.isChecked() and not view.cb_tun.isChecked())
    check("第一排是单选（QButtonGroup）", view._group_profile is not None)
    check("第二排是复选（QCheckBox，非互斥）",
          hasattr(view, "cb_system") and hasattr(view, "cb_tun"))
    check("没有行标签（界面只留选项本身）",
          not any(lbl.text() in ("接管", "分流模式") for lbl in view.findChildren(QLabel)))
    check("TUN 标签带「模式」二字", view.cb_tun.text() == "TUN 模式", view.cb_tun.text())

    # 这一条是用户明确要求的行为：两者可以同时开
    view.cb_tun.setChecked(True)
    pump(app, 0.3)
    check("★ 勾上 TUN 后，系统代理**仍然保持勾选**（两者可并存）",
          view.cb_tun.isChecked() and view.cb_system.isChecked(),
          f"TUN={view.cb_tun.isChecked()} 系统代理={view.cb_system.isChecked()}")
    check("两者都存进了配置",
          config["options"]["use_tun"] and config["options"]["use_system_proxy"],
          str(config["options"]))

    # 两排之间也互不干扰
    view.rb_global.setChecked(True)
    pump(app, 0.3)
    check("★ 切到「全局」不影响下排的 TUN",
          view.rb_global.isChecked() and view.cb_tun.isChecked())
    check("全局模式下 bypass 都为假",
          not view._opts.bypass_lan and not view._opts.bypass_china)

    # 取消 TUN，系统代理保持
    view.cb_tun.setChecked(False)
    pump(app, 0.3)
    check("取消 TUN 后系统代理不受影响", view.cb_system.isChecked())

    # 复位回默认：系统代理开、TUN 关、分流
    view.rb_split.setChecked(True)
    view.cb_system.setChecked(True)
    view.cb_tun.setChecked(False)
    pump(app, 0.3)
    check("复位回默认（系统代理 + 分流，TUN 关）",
          view.cb_system.isChecked() and not view.cb_tun.isChecked()
          and view.rb_split.isChecked())

    # --- 5.5 工具按钮与结果框 ---
    print("\n[5.5] 工具按钮与测试结果框")
    check("★ 有「更新」按钮", hasattr(view, "update_btn") and "更新" in view.update_btn.text(),
          getattr(view, "update_btn", None) and view.update_btn.text())
    check("★ 有「TCping」按钮", hasattr(view, "tcping_btn") and "TCping" in view.tcping_btn.text(),
          getattr(view, "tcping_btn", None) and view.tcping_btn.text())
    check("★ 有「URL测试」按钮", hasattr(view, "urltest_btn") and "URL测试" in view.urltest_btn.text(),
          getattr(view, "urltest_btn", None) and view.urltest_btn.text())
    check("★ 有输出结果行", hasattr(view, "result_view") and view.result_view is not None)
    check("结果行是只读展示（QLabel，不可编辑）",
          isinstance(view.result_view, QLabel) and not hasattr(view.result_view, "setPlainText"))
    check("★ 启航/靠岸带图标", "🚀" in view.launch_btn.text() and "🚢" in view.dock_btn.text(),
          f"{view.launch_btn.text()!r} {view.dock_btn.text()!r}")

    # 三个按钮的 objectName 决定样式，不能串
    names = [view.update_btn.objectName(), view.tcping_btn.objectName(), view.urltest_btn.objectName()]
    check("三个按钮样式名各不相同且正确",
          names == ["ToolUpdate", "ToolPing", "ToolUrl"], str(names))
    check("★ 三个工具按钮等宽",
          len({view.update_btn.width(), view.tcping_btn.width(), view.urltest_btn.width()}) == 1,
          f"{view.update_btn.width()}/{view.tcping_btn.width()}/{view.urltest_btn.width()}")

    # --- 结果框的可见性规则（这是安全要求，不只是 UI 偏好）---
    from canoe_client.logbus import bus as log_bus
    log_bus.result("TCP 延迟：65ms")
    pump(app, 0.4)
    shown = view.result_view.text()
    check("★ 结果行会显示", "TCP 延迟：65ms" in shown, shown[-160:])

    log_bus.system("这行是系统日志，不该显示")
    log_bus.kernel("outbound/shadowsocks[proxy]: to one.leycc.com:443")
    log_bus.error("URL测试  需要先启航")
    pump(app, 0.6)

    shown = view.result_view.text()
    check("★ 失败提示会显示", "先启航" in shown, shown[-160:])
    check("★ 输出框只留一行（上一条被顶掉）",
          "TCP 延迟：65ms" not in shown, shown[-160:])
    check("★ 系统日志不显示", "这行是系统日志" not in shown, shown[-160:])
    check("★ 内核日志不显示", "outbound" not in shown and "inbound" not in shown, shown[-160:])
    check("★★ 结果框里不出现节点域名（防止泄漏）",
          "leycc" not in shown and "one." not in shown, shown[-200:])

    # 结果行就一行高，不拖空白
    check("★ 结果行只有一行高（<= 46px）", view.result_view.height() <= 46,
          str(view.result_view.height()))
    check("★ 结果行在卡片里，卡片本身也不高",
          view.result_view.parentWidget() is not None)

    # URL 测试在未启航时应当给出提示而不是崩
    view.urltest_btn.click()
    pump(app, 0.6)
    check("★ 未启航时点 URL 测试有提示且不崩",
          "先启航" in view.result_view.text(),
          view.result_view.text()[-160:])

    # --- 6. 启航（真实） ---
    print("\n[6] 启航")
    if config.find_singbox() is None:
        skip("启航/靠岸", "bin/sing-box.exe 不存在")
    else:
        test_port = 21929
        view._opts.mixed_port = test_port
        view._opts.log_level = "warn"
        view.launch_btn.click()

        ok = wait_for(app, lambda: session.state == STATE_SAILED, timeout=40)
        check("状态变为「已启航」", ok, f"err={view.error_label.text()}")
        if ok:
            check(f"★ 状态文案 = 「{Text.ST_CONNECTED}」",
                  view.status_label.text() == Text.ST_CONNECTED, view.status_label.text())
            check("★ 内核进程在运行", kernel.running)
            check("★ 系统代理被设置",
                  ("set", "127.0.0.1", test_port) in fake_proxy.calls, str(fake_proxy.calls))
            check("启航按钮变为不可用", not view.launch_btn.isEnabled())
            check("靠岸按钮变为可用", view.dock_btn.isEnabled())

            # 真的走一次代理
            body = curl_through_proxy(test_port)
            check("★ 启航后真的能通过代理上网", bool(body) and "." in body, body[:120])

            # --- 7. 靠岸 ---
            print("\n[7] 靠岸")
            view.dock_btn.click()
            ok = wait_for(app, lambda: session.state == STATE_DOCKED, timeout=20)
            check("状态回到「已靠岸」", ok)
            check(f"★ 状态文案 = 「{Text.ST_DISCONNECTED}」",
                  view.status_label.text() == Text.ST_DISCONNECTED, view.status_label.text())
            check("★ 内核进程已停止", not kernel.running)
            check("★ 系统代理被还原", ("clear",) in fake_proxy.calls, str(fake_proxy.calls))
            check("启航按钮恢复可用", view.launch_btn.isEnabled())

            after = curl_through_proxy(test_port, timeout=6)
            check("★ 靠岸后代理确实失效了", not after, f"仍然返回: {after[:80]}")
        else:
            check("内核已启动", False)

    # --- 8. 离舟 ---
    print("\n[8] 离舟")
    out: list[bool] = []
    view.logged_out.connect(lambda: out.append(True))
    view._do_logout()
    pump(app, 0.3)
    check("触发 logged_out", bool(out))
    check("会话已清空", not session.logged_in and session.username == "")

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
