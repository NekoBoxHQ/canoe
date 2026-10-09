"""轻舟 · GUI 端到端测试（离屏运行，不碰真实系统代理）。

这一版对应用户端接线后的形态：账号在服务端、启航从服务端拿配置。

所以这里**把 api 层换成假的** —— 界面逻辑和真实服务端之间隔了一层，
拿假 api 驱动界面，既不依赖网络，也能把界面状态机跑完整：

    登录页校验 -> 登录 -> 主界面 -> 选项 -> 启航 -> 靠岸 -> 离舟

真正的客户端↔服务端联调在 tests/test_server.py（那个要对着真服务端跑）。

**为什么把 build_entry_outbound 也换掉**：出的那个 vless 出站指向的是
中转层入口，本机没有那个东西，会连不上网。这里换成 direct，好让
"启航后真的能通过代理上网"这条断言还能测 —— 出站怎么拼由 test_server.py 覆盖。

用法：
    set QT_QPA_PLATFORM=offscreen
    python tests/test_gui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton  # noqa: E402

from canoe_core import Text  # noqa: E402

from canoe_client.api import CanoeApiError  # noqa: E402
from canoe_client.kernel import kernel  # noqa: E402
from canoe_client.session import STATE_DOCKED, STATE_SAILED, session  # noqa: E402
from canoe_client.ui import main_view as mv  # noqa: E402
from canoe_client.ui import auth_view as av  # noqa: E402
from canoe_client.ui.auth_view import AuthView, validate_credentials  # noqa: E402
from canoe_client.ui.main_view import MainView  # noqa: E402
from canoe_client.ui.style import qss  # noqa: E402

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {extra}")


def skip(label: str, why: str) -> None:
    global skipped
    skipped += 1
    print(f"  [跳过] {label} —— {why}")


skipped = 0


# ---------------------------------------------------------------------------
# 假件
# ---------------------------------------------------------------------------


class FakeSysProxy:
    """记录调用，不碰真实注册表。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
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


class FakeApi:
    """假的服务端。界面只依赖这几个方法。"""

    def __init__(self) -> None:
        self.token = ""
        self.calls: list[str] = []
        self.node_name = "测试节点-甲"
        self.revoked = False

    # -- 认证 --
    def register(self, username: str, password: str) -> dict:
        self.calls.append("register")
        if username == "taken":
            raise CanoeApiError("conflict", "用户名已存在")
        return {"id": 1, "username": username}

    def login(self, username: str, password: str):
        self.calls.append("login")
        if password != "canoe-pass-123":
            raise CanoeApiError("bad_credentials", "用户名或密码不对")
        self.token = f"tok-{username}"
        return SimpleNamespace(
            token=self.token,
            expires_in=86400,
            user=SimpleNamespace(id=1, username=username, status="active",
                                 expire_at=None, node_name=self.node_name),
        )

    def logout(self, session_id=None) -> None:
        self.calls.append("logout")
        self.token = ""

    def me(self) -> dict:
        return {"id": 1, "username": "u", "status": "active"}

    # -- 配置 / 心跳 --
    def fetch_config(self, mode: str):
        self.calls.append(f"config:{mode}")
        if not self.token:
            raise CanoeApiError("unauthorized", "令牌无效或已过期", 401)
        return SimpleNamespace(
            protocol=1, session_id="sess-0001", node_name=self.node_name,
            token="ticket-abc", expires_at=1790000000, heartbeat_interval=30,
            config_version=7,
            # 只用来占位 —— 出站怎么拼由 test_server.py 覆盖
            entry=SimpleNamespace(host="entry.example.com", port=443, uuid="u-1",
                                  path="/e/x", sni="entry.example.com", tls=True,
                                  insecure=False, transport="ws"),
        )

    def heartbeat(self, session_id: str) -> dict:
        self.calls.append("heartbeat")
        return {"ok": True, "expires_at": 0, "config_version": 7,
                "revoked": self.revoked, "node_name": self.node_name}

    def stop_session(self, session_id: str) -> None:
        self.calls.append("stop_session")

    # -- 更新 / 订阅 --
    def subscription(self):
        self.calls.append("subscription")
        return SimpleNamespace(
            config_version=7, node_name=self.node_name, revision="rev-1",
            expires_at=None, heartbeat_interval=30,
            entry=SimpleNamespace(host="entry.example.com", port=443, uuid="u-1",
                                  path="/e/x", sni="entry.example.com", tls=True,
                                  insecure=False, transport="ws"),
        )

    def latest_release(self):
        self.calls.append("latest_release")
        return SimpleNamespace(version="1.0.0", url="", notes="", published_at=0,
                               size=0, sha256="", min_version="")


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
    import subprocess
    from shutil import which

    proc = subprocess.run(
        [which("curl") or "curl", "-sS", "-m", str(timeout),
         "--socks5-hostname", f"127.0.0.1:{port}", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip()


def main() -> int:
    print("\n== 轻舟 · GUI 端到端测试 ==\n")

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(qss())

    fake_proxy = FakeSysProxy()
    fake_api = FakeApi()
    mv.sysproxy = fake_proxy            # type: ignore[assignment]
    mv.api = fake_api                   # type: ignore[assignment]
    av.api = fake_api                   # type: ignore[assignment]
    # 真出站指向中转层入口，本机连不上；这里换直连，
    # 好让"启航后真的能上网"那条断言还能测（内核会把 DNS 的 detour 去掉，
    # 见 kernel._dns_config）。出站怎么拼由 tests/test_server.py 覆盖。
    mv.build_entry_outbound = lambda entry, tag="proxy": {"type": "direct", "tag": tag}

    # 用独立的配置文件，别污染真实用户数据
    tmp = Path(tempfile.mkdtemp(prefix="canoe-test-"))
    from canoe_client import config as cfg_mod
    cfg_mod.CONFIG_FILE = tmp / "client.json"
    cfg_mod.config._data["device_id"] = "test-device-0001"

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
    check("★ 没有「直接体验」按钮了（账号一律走服务端注册）",
          not hasattr(auth, "guest_btn"))

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
    check("★ 界面上也没有「服务器地址」这类入口（地址是写死的）",
          not any("服务器" in t or "渡口地址" in t for t in button_texts))

    # --- 3. 注册 / 登录的本地校验 ---
    print("\n[3] 造舟与登舟")
    check("用户名过短被拦下", bool(validate_credentials("ab", "canoe-pass-123")))
    check("用户名带非法字符被拦下", bool(validate_credentials("a b", "canoe-pass-123")))
    check("密码过短被拦下", bool(validate_credentials("canoe", "123")))
    check("两次密码不一致被拦下", bool(validate_credentials("canoe", "canoe-pass-123", "x")))
    check("合法输入放行", validate_credentials("canoe", "canoe-pass-123", "canoe-pass-123") == "")

    username = "canoe-gui"
    # 走真实的注册按钮
    auth.reg_user.setText(username)
    auth.reg_pass.setText("canoe-pass-123")
    auth.reg_pass2.setText("canoe-pass-123")
    logged: list[str] = []
    auth.logged_in.connect(logged.append)
    auth.reg_btn.click()
    ok = wait_for(app, lambda: bool(logged), timeout=10)
    check("★ 注册成功后自动登舟", ok, auth.reg_err.text())
    check("注册走了服务端", "register" in fake_api.calls and "login" in fake_api.calls,
          str(fake_api.calls))

    logged.clear()
    auth.login_user.setText(username)
    auth.login_pass.setText("wrong-password")
    auth.login_btn.click()
    ok = wait_for(app, lambda: bool(auth.login_err.text()), timeout=10)
    check("★ 密码错时给出服务端返回的提示", "密码" in auth.login_err.text(), auth.login_err.text())

    auth.login_user.setText(username)
    auth.login_pass.setText("canoe-pass-123")
    auth.login_btn.click()
    ok = wait_for(app, lambda: bool(logged), timeout=10)
    check("★ 登舟成功并触发 logged_in", ok, auth.login_err.text())

    view.start_with_node(username, auth.last_node_name)
    check("★ 显示了服务端给的节点名称", view.node_label.text() == fake_api.node_name,
          view.node_label.text())
    view.account_label.setText(username)
    check("显示了账号名", view.account_label.text() == username)

    # --- 4. 选项 ---
    print("\n[4] 可选项")
    check("★ 上排默认选「分流」",
          view.rb_split.isChecked() and not view.rb_global.isChecked())
    check("★ 下排默认勾「系统代理」、不勾「TUN 模式」",
          view.cb_system.isChecked() and not view.cb_tun.isChecked())
    check("第一排是单选（QButtonGroup）", view._group_profile is not None)
    check("TUN 标签带「模式」二字", view.cb_tun.text() == "TUN 模式", view.cb_tun.text())

    view.cb_tun.setChecked(True)
    pump(app, 0.2)
    check("★ 勾上 TUN 后系统代理**仍然保持勾选**（两者可并存）",
          view.cb_system.isChecked() and view.cb_tun.isChecked())
    check("两者都存进了配置",
          view._opts.use_system_proxy and view._opts.use_tun)

    view.rb_global.setChecked(True)
    pump(app, 0.2)
    check("★ 切到「全局」不影响下排的 TUN", view.cb_tun.isChecked())
    check("全局模式下 bypass 都为假",
          not view._opts.bypass_lan and not view._opts.bypass_china)

    view.cb_tun.setChecked(False)
    view.rb_split.setChecked(True)
    pump(app, 0.2)
    check("复位回默认（分流 + 系统代理，TUN 关）",
          view.rb_split.isChecked() and view.cb_system.isChecked() and not view.cb_tun.isChecked())

    # --- 5. 工具按钮与结果框 ---
    print("\n[5] 工具按钮与结果框")
    check("★ 有「更新」按钮", "更新" in view.update_btn.text(), view.update_btn.text())
    check("★ 有「TCping」按钮", "TCping" in view.tcping_btn.text(), view.tcping_btn.text())
    check("★ 有「URL测试」按钮", "URL测试" in view.urltest_btn.text(), view.urltest_btn.text())
    check("★ 有输出结果行", hasattr(view, "result_view") and view.result_view is not None)
    check("结果行是只读展示（QLabel，不可编辑）",
          isinstance(view.result_view, QLabel) and not hasattr(view.result_view, "setPlainText"))
    check("★ 启航/靠岸带图标", "🚀" in view.launch_btn.text() and "🚢" in view.dock_btn.text())

    names = [view.update_btn.objectName(), view.tcping_btn.objectName(), view.urltest_btn.objectName()]
    check("三个按钮样式名各不相同且正确",
          names == ["ToolUpdate", "ToolPing", "ToolUrl"], str(names))
    check("★ 三个工具按钮等宽",
          len({view.update_btn.width(), view.tcping_btn.width(), view.urltest_btn.width()}) == 1,
          f"{view.update_btn.width()}/{view.tcping_btn.width()}/{view.urltest_btn.width()}")

    # 结果框的可见性规则（安全要求）
    from canoe_client.logbus import bus as log_bus
    log_bus.result("TCP 延迟：65ms")
    pump(app, 0.4)
    shown = view.result_view.text()
    check("★ 结果行会显示", "TCP 延迟：65ms" in shown, shown[-160:])

    log_bus.system("这行是系统日志，不该显示")
    log_bus.kernel("outbound/shadowsocks[proxy]: to secret.example.com:443")
    log_bus.error("URL测试  需要先启航")
    pump(app, 0.6)

    shown = view.result_view.text()
    check("★ 失败提示会显示", "先启航" in shown, shown[-160:])
    check("★ 输出框只留一行（上一条被顶掉）", "TCP 延迟：65ms" not in shown, shown[-160:])
    check("★ 系统日志不显示", "这行是系统日志" not in shown, shown[-160:])
    check("★ 内核日志不显示", "outbound" not in shown and "inbound" not in shown, shown[-160:])
    check("★★ 结果框里不出现域名（防止泄漏）",
          "secret.example.com" not in shown and "example" not in shown, shown[-200:])

    check("★ 结果行只有一行高（<= 46px）", view.result_view.height() <= 46,
          str(view.result_view.height()))

    # 「更新」按钮一次查两条
    fake_api.calls.clear()
    view.update_btn.click()
    pump(app, 0.8)
    check("★ 「更新」同时查了客户端版本和订阅",
          "latest_release" in fake_api.calls and "subscription" in fake_api.calls,
          str(fake_api.calls))
    check("★ 两条结果拼成一行", "更新" in view.result_view.text() and "订阅" in view.result_view.text(),
          view.result_view.text())

    # 未启航时点 URL 测试
    view.urltest_btn.click()
    pump(app, 0.5)
    check("★ 未启航时点 URL 测试有提示且不崩",
          "先启航" in view.result_view.text(), view.result_view.text())

    # --- 6. 启航（真起内核）---
    print("\n[6] 启航")
    if mv.config.find_singbox() is None:
        skip("启航/靠岸", "bin/sing-box.exe 不存在")
    else:
        test_port = 22929
        view._opts.mixed_port = test_port
        view._opts.log_level = "warn"
        view.launch_btn.click()

        ok = wait_for(app, lambda: session.state == STATE_SAILED, timeout=40)
        check("状态变为「已启航」", ok, f"err={view.error_label.text()}")
        check("★ 启航向服务端要了配置", "config:system_proxy" in fake_api.calls,
              str(fake_api.calls))
        check("★ 内核进程在运行", kernel.running)
        check("★ 系统代理被设置", ("set", "127.0.0.1", test_port) in fake_proxy.calls,
              str(fake_proxy.calls))
        check("启航按钮变为不可用", not view.launch_btn.isEnabled())
        check("靠岸按钮变为可用", view.dock_btn.isEnabled())
        check("★ 心跳定时器已启动", view._hb_timer.isActive())

        body = curl_through_proxy(test_port, timeout=15)
        if body:
            check(f"★ 启航后真的能通过代理上网：{body}", "." in body)
        else:
            skip("通过代理上网", "本机 curl 拿不到结果（可能没外网）")

        # --- 7. 靠岸 ---
        print("\n[7] 靠岸")
        view.dock_btn.click()
        ok = wait_for(app, lambda: session.state == STATE_DOCKED, timeout=40)
        check("状态回到「已靠岸」", ok)
        check("★ 内核进程已停止", not kernel.running)
        check("★ 系统代理被还原", ("clear",) in fake_proxy.calls, str(fake_proxy.calls))
        check("★ 告诉了服务端会话结束", "stop_session" in fake_api.calls, str(fake_api.calls))
        check("★ 心跳定时器已停", not view._hb_timer.isActive())
        check("启航按钮恢复可用", view.launch_btn.isEnabled())

    # --- 8. 服务端推送 ---
    print("\n[8] 服务端推送")
    view._set_state(STATE_SAILED)
    view.on_push_event({"type": "config_changed", "config_version": 9})
    pump(app, 0.3)
    check("★ 收到配置变更会提示重新启航", "重新启航" in view.result_view.text(),
          view.result_view.text())
    check("★ 把本地配置版本清掉了（下次启航会重新对齐）", view._config_version == 0)

    view.on_push_event({"type": "kick", "reason": "管理员把你踢下线了", "permanent": False})
    pump(app, 0.4)
    check("★ 被踢下线后自动靠岸", session.state == STATE_DOCKED, session.state)

    # --- 9. 离舟 ---
    print("\n[9] 离舟")
    out: list[bool] = []
    view.logged_out.connect(lambda: out.append(True))
    view._logout()
    pump(app, 0.6)
    check("触发 logged_out", bool(out))
    check("会话已清空", not session.logged_in and session.username == "")
    check("★ 离舟时也吊销了服务端令牌", "logout" in fake_api.calls, str(fake_api.calls))

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
