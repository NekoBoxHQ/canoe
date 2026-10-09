"""轻舟 · GUI 端到端测试（离屏运行，不碰真实系统代理）。

这一版对应用户端接线后的形态：账号在服务端、启航从服务端拿配置。

所以这里**把 api 层换成假的** —— 界面逻辑和真实服务端之间隔了一层，
拿假 api 驱动界面，既不依赖网络，也能把界面状态机跑完整：

    登录页校验 -> 登录 -> 主界面 -> 选项 -> 启航 -> 靠岸 -> 离舟

真正的客户端↔服务端联调在 tests/test_server.py（那个要对着真服务端跑）。

订阅走的是**真加密**（canoe_core.crypto），不是假的 —— 假 api 用真
seal/unseal 包一遍，所以"解密"这段代码在 GUI 测试里也是真的被执行到的。
唯一替换掉的是最后一步：把订阅里那条链接换成 direct 出站，
好让"启航后真的能通过代理上网"这条断言还能测（链接本身怎么解析由
test_links.py 覆盖，端到端由 test_server.py 覆盖）。

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

from canoe_core import Text, new_sub_key, seal, unseal  # noqa: E402

from canoe_client.api import CanoeApiError  # noqa: E402
from canoe_client.kernel import kernel  # noqa: E402
from canoe_client.session import STATE_DOCKED, STATE_SAILED, session  # noqa: E402
from canoe_client.ui import main_view as mv  # noqa: E402
from canoe_client.ui import auth_view as av  # noqa: E402
from canoe_client.ui.auth_view import AuthView, validate_credentials  # noqa: E402
from canoe_client.ui.main_view import MainView  # noqa: E402
from canoe_client.ui.nodelights import NodeLights  # noqa: E402
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

    #: 假服务端下发的订阅内容。改它就能模拟"管理员改了订阅 / 清空了订阅"。
    DEFAULT_SUB = "ss://2022-blake3-aes-128-gcm:AAAA:BBBB@node.example.com:33222#测试节点-甲"
    SUB_KEY = new_sub_key()

    def __init__(self) -> None:
        self.token = ""
        self.calls: list[str] = []
        self.node_name = "测试节点-甲"
        self.revoked = False
        #: 当前订阅正文。置空 = 服务端停止分发。
        self.sub_text = self.DEFAULT_SUB
        self.revision = "rev-1"

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
            sub_key=self.SUB_KEY,
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
        # ★ 这里**没有**任何节点字段 —— 和真服务端一致。
        #   节点从订阅里来，见下面的 subscription_text()。
        return SimpleNamespace(
            protocol=1, session_id="sess-0001", node_name=None,
            expires_at=1790000000, heartbeat_interval=30,
            revision=self._revision(),
        )

    def _revision(self) -> str:
        return "rev-empty" if not self.sub_text else self.revision

    def heartbeat(self, session_id: str) -> dict:
        self.calls.append("heartbeat")
        return {"ok": True, "expires_at": 0, "revision": self._revision(),
                "revoked": self.revoked, "node_name": None}

    def stop_session(self, session_id: str) -> None:
        self.calls.append("stop_session")

    # -- 更新 / 订阅 --
    def subscription(self):
        """和真服务端一样：内容是**密文**。"""
        self.calls.append("subscription")
        return SimpleNamespace(
            node_name=None, revision=self._revision(),
            expires_at=None, heartbeat_interval=30,
            envelope=seal(self.sub_text, self.SUB_KEY, revision=self._revision()),
        )

    def subscription_text(self):
        """真解密一遍 —— 走的是和生产同一份 canoe_core.crypto。"""
        resp = self.subscription()
        return resp, unseal(resp.envelope, self.SUB_KEY)

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
    # 订阅里那条链接指向一台真实机器，本机连不上；这里把**出站换成直连**，
    # 好让"启航后真的能上网"那条断言还能测（内核会把 DNS 的 detour 去掉，
    # 见 kernel._dns_config）。链接怎么解析由 tests/test_links.py 覆盖。
    #
    # ⚠ 钩的是 `parse` 不是 `pick`。客户端原来每次现挑第一个（links.pick），
    #   换成"留一份节点列表好切节点"之后就只走 parse 了 —— 还钩 pick 的话
    #   这个替身根本不会被调用，真链接直接送进内核，报
    #   "bad key length, required 16, got 3"（SS2022 的密钥是假的）。
    #   名字和条数照旧用真解析出来的，只换出站。
    _real_parse = mv.links.parse

    def _fake_parse(text):
        result = _real_parse(text)
        for link in result.links:
            link.outbound = {"type": "direct", "tag": "proxy"}
        return result

    mv.links.parse = _fake_parse

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

    # 记住账号密码。密码在本地是 DPAPI 密文（细测见 test_remember.py），
    # 这里只管界面接线：勾选框在不在、回填对不对、勾/取消有没有落盘。
    check("★ 登舟页有「记住账号密码」勾选框", hasattr(auth, "remember_box"))
    check(f"勾选框文案 = 「{Text.LABEL_REMEMBER}」",
          auth.remember_box.text() == Text.LABEL_REMEMBER, auth.remember_box.text())
    check("默认不勾", not auth.remember_box.isChecked())

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
    check("★ 没勾「记住账号密码」时，本地一个字都不留",
          cfg_mod.config.remembered_credentials() == ("", ""),
          str(cfg_mod.config.remembered_credentials()))

    # --- 3.5 勾上之后 ---
    print("\n[3.5] 记住账号密码")
    auth.remember_box.setChecked(True)
    auth.login_user.setText(username)
    auth.login_pass.setText("canoe-pass-123")
    logged.clear()
    auth.login_btn.click()
    ok = wait_for(app, lambda: bool(logged), timeout=10)
    check("第二次登舟成功", ok, auth.login_err.text())

    remembered = cfg_mod.config.remembered_credentials()
    check("★ 勾上之后本地记下了账号密码",
          remembered == (username, "canoe-pass-123"), str(remembered))
    check("★ 密码不是明文躺在配置里",
          "canoe-pass-123" not in cfg_mod.CONFIG_FILE.read_text(encoding="utf-8"))

    # 下次开客户端：应当自动回填 + 保持勾选
    again = AuthView()
    check("★ 重新打开时账号密码自动填好",
          again.login_user.text() == username and again.login_pass.text() == "canoe-pass-123",
          f"{again.login_user.text()!r}/{len(again.login_pass.text())}")
    check("★ 重新打开时勾选框还是勾着的", again.remember_box.isChecked())

    # 取消勾选：本地那份要**当场**清掉，不能等下次登录成功
    again.remember_box.setChecked(False)
    check("★ 取消勾选立刻清掉本地的账号密码",
          cfg_mod.config.remembered_credentials() == ("", ""),
          str(cfg_mod.config.remembered_credentials()))

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

    # --- 5.5 底部那排节点灯 ---
    print("\n[5.5] 节点灯")
    check("★ 主界面底部有那排灯", hasattr(view, "lights"))
    check(f"★ 一共 {NodeLights.SLOTS} 个位置", NodeLights.SLOTS == 6)

    def feed(n: int) -> None:
        """喂 n 个节点进去，等价于服务端订阅里有 n 行能认出来的链接。"""
        text = "\n".join(
            f"ss://2022-blake3-aes-128-gcm:AAAA:BBBB@node{i}.example.com:33222#节点{i}"
            for i in range(1, n + 1)
        )
        view._apply_subscription(text)
        view._active = 0
        view._sync_active()

    feed(0)
    check("一个节点都没有时全灭", view.lights.count() == 0)

    feed(1)
    check("★ 1 个节点 -> 亮 1 盏", view.lights.count() == 1)
    check("节点名跟着显示出来", view.node_label.text() == "节点1", view.node_label.text())

    feed(2)
    check("★ 2 个节点 -> 亮 2 盏", view.lights.count() == 2)
    check("当前在第 1 盏", view.lights.active() == 0)

    # 点第二盏：切过去
    view.lights.node_selected.emit(1)
    pump(app, 0.1)
    check("★ 点第 2 盏 -> 切到第 2 个节点",
          view.lights.active() == 1 and view.node_label.text() == "节点2",
          f"{view.lights.active()} / {view.node_label.text()}")
    check("靠岸状态下切换不需要起内核", not mv.kernel.running)

    # 点当前这盏 / 点暗着的：都不该有反应
    view._switch_node(1)
    check("点当前这盏不重复切", view.lights.active() == 1)
    view._switch_node(4)
    check("★ 点没点亮的格子不切（第 5 个本来是暗的）",
          view.lights.active() == 1 and view.node_label.text() == "节点2")

    feed(5)
    check("★ 5 个节点 -> 亮 5 盏，只暗 1 个",
          view.lights.count() == 5 and view.lights.count() < NodeLights.SLOTS)

    feed(9)
    check("★ 超过 6 个只点前 6 盏（位置就这么多）",
          view.lights.count() == NodeLights.SLOTS, str(view.lights.count()))

    # 订阅变短了，当前那盏不能指向不存在的格子
    feed(1)
    view._active = 0
    view._sync_active()
    view._switch_node(3)
    check("★ 订阅变短后点空格子不越界", view.lights.active() == 0, str(view.lights.active()))

    # 服务端停止分发 -> 灯全灭 + 节点名清空
    view._apply_subscription("")
    check("★ 订阅清空后灯全灭", view.lights.count() == 0, str(view.lights.count()))
    check("订阅清空后节点名回到占位符", view.node_label.text() == "—", view.node_label.text())

    feed(2)
    check("恢复订阅后灯又亮起来", view.lights.count() == 2)

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
    view._revision = "old"
    view.on_push_event({"type": "config_changed", "revision": fake_api.revision})
    wait_for(app, lambda: view._revision == fake_api.revision, timeout=10)
    check("★ 收到推送会重新拉订阅并更新指纹",
          view._revision == fake_api.revision, view._revision)

    # 管理员清空订阅 -> 在航的客户端必须当场销毁并靠岸
    view._set_state(STATE_SAILED)
    fake_api.sub_text = ""
    view.on_push_event({"type": "config_changed", "revision": fake_api._revision()})
    wait_for(app, lambda: session.state == STATE_DOCKED and not view._sub_text, timeout=40)
    check("★ 订阅被清空后就地销毁", view._sub_text == "", repr(view._sub_text))
    check("★ 订阅被清空后在航的客户端自动靠岸", session.state == STATE_DOCKED)
    check("★ 节点名也跟着清掉了", view._node_name == "" and session.node_name == "")

    # 恢复：下面还要测踢下线
    fake_api.sub_text = fake_api.DEFAULT_SUB
    view._set_state(STATE_SAILED)

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

    # --- 10. 托盘：关窗只收起来，退出要右键 ---
    print("\n[10] 托盘")
    docked: list[int] = []
    view._dock = lambda: docked.append(1)

    # 有托盘：关窗只是收起来，不许动内核/代理
    view.close_to_tray = True
    view.show()
    view.close()
    check("★ 关窗只是收起来（窗口不可见）", not view.isVisible())
    check("★ 收进托盘时没有靠岸（代理继续跑）", not docked, str(docked))
    check("★ 收进托盘时日志定时器仍在跑", view._log_timer.isActive())

    # 真退出：force_close 走完整清理
    view.show()
    view.force_close()
    check("★ 退出时真的关掉（日志定时器停了）", not view._log_timer.isActive())

    # 没有托盘的环境：关窗就是关窗，行为不能变
    view.close_to_tray = False
    view.show()
    view.close()
    check("没有托盘时关窗照旧（不会卡在托盘里）", not view.isVisible())

    # 托盘模块本身：能构造、菜单有「退出」
    from canoe_client.ui.tray import Tray
    t = Tray(None)
    acts = [a.text() for a in t.icon.contextMenu().actions() if a.text()]
    check("★ 托盘右键菜单里有「退出」", "退出" in acts, str(acts))
    check("托盘菜单里也有「显示主界面」", "显示主界面" in acts, str(acts))
    seen = []
    t.quit_requested.connect(lambda: seen.append("quit"))
    t._quit_action.trigger()
    check("★ 点「退出」发出 quit_requested", seen == ["quit"], str(seen))

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
