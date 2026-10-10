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
import atexit
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QToolButton  # noqa: E402

from canoe_core import (  # noqa: E402
    VERSION,
    Palette as P,
    Route,
    Text,
    new_sub_key,
    seal,
    unseal,
)

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
        #: 当前账号 id。真 api 是登录响应里存的，界面靠它对 kick 事件的
        #: user_id 认名字（封 B 不该把在线的 A 一起踢下线）。
        self.user_id = 0
        self.calls: list[str] = []
        self.node_name = "测试节点-甲"
        self.revoked = False
        #: 当前订阅正文。置空 = 服务端停止分发。
        self.sub_text = self.DEFAULT_SUB
        self.revision = "rev-1"
        #: 线路方向。改它 = 管理员在面板上把这个客户改成回国了。
        self.route_mode = Route.OUT

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
        self.user_id = 1
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
        self.user_id = 0

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
            revision=self._revision(), route_mode=self.route_mode,
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
            expires_at=None, heartbeat_interval=30, route_mode=self.route_mode,
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
    # 跑完自己收掉：这个目录里有 84MB 的假 exe，不收的话每跑一轮
    # 就在 %TEMP% 里留一份（攒了 500 多 MB 才被发现）。
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    from canoe_client import config as cfg_mod
    cfg_mod.CONFIG_FILE = tmp / "client.json"
    cfg_mod.config._data["device_id"] = "test-device-0001"

    # ⚠ 光改 CONFIG_FILE 不够。`config` 是模块级单例，import 的时候就把
    #   **真实那份 client.json** 读进内存了 —— 改 CONFIG_FILE 只影响之后
    #   往哪写，内存里那份还在。于是开发机上真勾过「记住账号密码」的话，
    #   登舟页一构造就被回填了，下面"默认不勾""一个字都不留"两条必红。
    #   （更糟的是：那意味着测试把开发者真实的账号密码读进了内存。）
    #   所以内存里这份也要清干净，测试从确定的状态起步。
    cfg_mod.config["remember"] = {"enabled": False, "username": "", "secret": ""}

    # ⚠ 同一类坑，选项版：options 也是 import 时就从**真实那份**读进来的。
    #   开发机上真勾过 TUN 的话，「默认勾系统代理、不勾 TUN」那条断言必红 ——
    #   而且更糟：测试是拿开发者的真实设置跑的，结果随人而异。
    cfg_mod.config["options"] = dict(cfg_mod.DEFAULTS["options"])

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

    # 底部正中的版本号。用户报问题时第一句常常是"我装的是哪个版本"，
    # 而更新弹窗只在有新版本时才出来 —— 得有个地方随时能看。
    check("★ 底部有版本号，写作「版本:V{版本}」",
          view.version_label.text() == f"版本:V{VERSION}",
          view.version_label.text())
    check("★ 版本号居中",
          bool(view.version_label.alignment() & Qt.AlignHCenter),
          str(view.version_label.alignment()))
    # 比"最后一个控件"而不是比 y 坐标 —— 这里窗口还没 show 过，
    # 所有控件的 y 都是 0，比出来是 0 vs 0（踩过）。排版顺序才是这里要的。
    _widgets = [view.body_layout.itemAt(i).widget()
                for i in range(view.body_layout.count())]
    _widgets = [w for w in _widgets if w is not None]
    check("★ 版本号排在最后（在节点灯和水面之下）",
          bool(_widgets) and _widgets[-1] is view.version_label,
          f"最后一个控件是 {_widgets[-1].objectName() if _widgets else '（空）'}")

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
    # ★ 登录时得把账号 id 一起带进会话 —— 服务端推来的 kick 是"点名"的，
    #   界面靠这个 id 认名字。忘了带的话防线形同虚设（id 恒为 0），
    #   封别人照样把自己踢下线。
    check("★ 登录时把账号 id 带进了会话（kick 认名字要用）",
          session.user_id == fake_api.user_id == 1, f"{session.user_id}")
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
    # 全局模式：不挂任何规则，两个方向对它都没影响
    check("★ 全局模式下不挂分流规则、兜底走代理",
          not view._opts.bypass_lan
          and view._opts.final_outbound == "proxy")

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

    # 用户原话："首次使用 应该为空 现在有个 点 杠"。
    # 也就是刚打开、一条结果都还没出过的时候，那行必须是**空的** ——
    # 既不能预置一个 "—"，也不该先亮着一个绿点。
    check("★ 首次使用（还没出过结果）时输出行是空的",
          view.result_view.text() == "", repr(view.result_view.text()))
    check("★ 那会儿绿点也是藏着的（不是只把文字清空）", view.result_dot.isHidden())

    log_bus.result("TCP 延迟：65ms")
    pump(app, 0.4)
    shown = view.result_view.text()
    check("★ 结果行会显示", "TCP 延迟：65ms" in shown, shown[-160:])
    check("★ 有结果之后才把绿点放出来", not view.result_dot.isHidden())

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

    # 「更新」按钮一次查两条。
    # ⚠ 结果框只留最后一行，而"更新"完了还会顺手重载订阅（又往结果框里
    #   写一句「订阅已更新」）—— 所以不能等尘埃落定再读控件，得把这一轮
    #   发出去的话都录下来。上一版是碰巧过的：那句「订阅已更新」里正好
    #   有个"更新"二字，把断言蒙对了。
    fake_api.calls.clear()
    said: list[str] = []
    real_result = log_bus.result
    log_bus.result = lambda msg: (said.append(str(msg)), real_result(msg))[1]
    try:
        view.update_btn.click()
        pump(app, 1.0)
    finally:
        log_bus.result = real_result
    check("★ 「更新」同时查了客户端版本和订阅",
          "latest_release" in fake_api.calls and "subscription" in fake_api.calls,
          str(fake_api.calls))
    check("★ 两条结果拼成一行（版本 + 订阅）",
          any("最新版本" in s and "订阅" in s for s in said), str(said))
    # FakeApi 报的版本比当前低 -> 只能说"已是最新"，不许冒充"发现新版本"
    check("★ 远程版本不高于当前 -> 「当前已是最新版本 V…」",
          any(f"当前已是最新版本 V{VERSION}" in s for s in said), str(said))
    # 注意别写成 "新版本" not in s —— "当前已是**最新版本**"本来就有这三个字
    check("★ 这个情况下不许说『有新版本 / 发现新版本』",
          not any(("有新版本" in s or "发现新版本" in s) for s in said), str(said))

    # --- 8.6 版本提示：只在**真的更新**时才出现 ---
    # 用户撞上的：自己就是 V1.0.31，结果框里却跳一句「有新版本：1.0.31」。
    # 服务端一**发布**就广播 release，客户端以前是照单全收、不做比较。
    print("\n[8.6] 版本提示的措辞")
    before = view.result_view.text()
    view.on_push_event({"type": "release", "version": VERSION})
    pump(app, 0.3)
    check("★ 广播的版本跟自己一样 -> 结果框一个字都不动（不冒充『有新版本』）",
          view.result_view.text() == before,
          f"{before!r} -> {view.result_view.text()!r}")
    view.on_push_event({"type": "release", "version": "9.9.9"})
    pump(app, 0.3)
    check("★ 广播的版本比自己高 -> 「发现新版本：V9.9.9」",
          "发现新版本：V9.9.9" in view.result_view.text(), view.result_view.text())

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

    # 间距：6 盏要**均分整排**，不是固定间距挤在中间
    _, six = None, None
    feed(6)
    view.lights.resize(380, 46)
    boxes = view.lights._boxes()
    gaps = [round(boxes[i + 1].left() - boxes[i].left(), 1) for i in range(len(boxes) - 1)]
    check("★ 6 盏灯是均分的（相邻间距一致）",
          max(gaps) - min(gaps) < 1.0, str(gaps))
    edge_l, edge_r = boxes[0].left(), view.lights.width() - boxes[-1].right()
    check("★ 灯铺满整排，不是挤在中间留一大块空",
          abs(edge_l - edge_r) < 2 and edge_l < 40,
          f"左边距 {edge_l:.0f}，右边距 {edge_r:.0f}")

    # 用户明确不要 tooltip —— 鼠标扫过去弹一串节点名一样是"显示"
    check("★ 灯上没有任何 tooltip", view.lights.toolTip() == "", repr(view.lights.toolTip()))
    feed(2)

    # --- 5.6 全局 QSS：托盘菜单 ----------------
    print("\n[5.6] 托盘菜单的配色")
    # QSS 头上是 `* {{ color: 浅色 }}`（普配所有控件）。它会给 QMenu 的文字
    # 上近白色，但**不会**给弹出菜单铺底 —— 底还是系统默认的白，
    # 于是白字白底，右键托盘看起来就是一块空白方块（用户截图反馈的）。
    # 所以 QMenu 那几条规则必须存在，而且必须带 background。
    import re as _re

    sheet = qss()
    check("★ QSS 里有 QMenu 规则", "QMenu {" in sheet or "QMenu{" in sheet)
    m = _re.search(r"QMenu\s*\{(.*?)\}", sheet, _re.S)
    check("★ QMenu 指定了底色（只给字色不够，白字白底 = 一块空白）",
          bool(m) and "background" in m.group(1), (m.group(1)[:90] if m else "没找到 QMenu 规则"))
    check("★ QMenu::item 也给了字色",
          "QMenu::item " in sheet or "QMenu::item{" in sheet)
    check("★ QToolTip 也铺了底（Windows 上的应用内提示同理）",
          bool(_re.search(r"QToolTip\s*\{[^}]*background", sheet, _re.S)))

    # ★ 花括号必须配对。少一个 `}`，Qt 的样式表解析器从那行起**整段放弃** ——
    #   不报错、也不警告，只是后面的规则全部失效：输入框变回系统白底、
    #   主按钮没了渐变。写 QSS 时最容易犯，而且肉眼完全看不出来
    #   （今天就这么把 `QLabel#Brand {{` 写成了两行，输入框和按钮的样式
    #    一起没了；查了半天才用"数花括号"定位到）。
    check("★ QSS 花括号配对（少一个 } 会让后面的样式整段失效，且不报错）",
          sheet.count("{") == sheet.count("}"),
          f"{{ = {sheet.count('{')} 个，}} = {sheet.count('}')} 个")

    # ★ 带 letter-spacing 又居中的文字，必须补 `padding-left: <字距>px`。
    #
    # 字距会在**最后一个字后面**也留一截虚宽，而"居中"居的是含那截虚宽的
    # 文本框 —— 于是字形看着偏左。实测（离屏渲染数像素）：#Brand 的"轻舟"
    # 字距 10px 时墨迹中心偏左 5.5px，正好半个字距；#NodeName、#Status、
    # #Version 同样各偏 3 / 1 / 1px。
    #
    # padding-left 把内容区往右推，文字中心跟着右移"半个 padding"，
    # 所以 padding-left = 字距 正好抵掉。**漏一个，那一处就是偏的。**
    offenders = [
        sel.strip().splitlines()[-1]
        for sel, body in _re.findall(r"([^{}]+)\{([^}]*)\}", sheet)
        if "letter-spacing" in body and "padding-left" not in body and "padding:" not in body
    ]
    check("★ 带字距的文字都补了 padding-left（不然居中会偏左半个字距）",
          not offenders, f"这些漏了：{offenders}")

    # --- 5.7 全局 QSS：系统消息框 ----------------
    # 同一个病，QMessageBox 也一样。用户截图里那个"轻舟已经在运行了"
    # （单实例提示，app.py）整框的字都是浅灰，几乎看不见 —— 就是白字浅底。
    # 对话框底、正文、**按钮**三样都得给：按钮不给的话照样是白字浅底。
    print("\n[5.7] 系统消息框的配色")
    mb = _re.search(r"QMessageBox\s*\{(.*?)\}", sheet, _re.S)
    check("★ QSS 里有 QMessageBox 规则并铺了底色",
          bool(mb) and "background" in mb.group(1), (mb.group(1)[:90] if mb else "没找到"))
    check("★ 消息框正文给了字色", "QMessageBox QLabel" in sheet)
    check("★ 消息框的按钮也铺了底（不然白字浅底照样看不清）",
          bool(_re.search(r"QMessageBox QPushButton\s*\{[^}]*background", sheet, _re.S)))

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

    # --- 8.5 线路方向（出国 / 回国）---
    # ★ 它是**服务端定死**的，而且界面上**不显示**（用户改到第四版拍的板：
    #   "干脆不用显示，看着别闹"）。方向仍然生效，唯一看得见它的地方是
    #   「分流」那个提示。在航时被切要自动重连 —— 方向挂在分流规则上。
    print("\n[8.5] 线路方向（服务端定死，界面上不显示）")

    check("★ 界面上没有那行字（用户砍掉的）",
          not hasattr(view, "route_label"))
    check("★ QSS 里也没有它的规则",
          not _re.search(r"QLabel#RouteMode\s*\{", sheet))
    # ★ 两个窗口**逐像素一样大**。登录页和主界面切换时窗口忽大忽小，
    #   看着像换了个程序（用户点名要求统一）。两边都写死成 window_base 里
    #   那一对常量，这里钉住。
    check("★ 主界面就是共享尺寸 WINDOW_W × WINDOW_H",
          (view.width(), view.height()) == (mv.WINDOW_W, mv.WINDOW_H),
          f"{view.width()}x{view.height()} vs {mv.WINDOW_W}x{mv.WINDOW_H}")
    # 登录页**故意**比主界面矮 59px：去掉帆船徽标之后用户要求"底下的空间往上
    # 缩、整个高度变矮"（"否则删徽标意义何在"）。矮的这 59px 是从卡片下面那条
    # 山水带里让出来的（auth_view.SCENE_BAND），不是从内容里抠的。
    # ★ 宽度必须还是一样的；两个窗口现在**不一样高**，登录成功切主界面时窗口
    #   会长高 59px —— 这是"登录页要矮"和"主界面内容摆不下（自然高度 577 +
    #   底边 14 = 592）"之间唯一的选择。
    check("★ 登录页比主界面矮（用户要求），宽度一样",
          (auth.width(), auth.height()) == (mv.WINDOW_W, av.AUTH_WINDOW_H)
          and av.AUTH_WINDOW_H < mv.WINDOW_H,
          f"{auth.width()}x{auth.height()} vs {mv.WINDOW_W}x{av.AUTH_WINDOW_H}")
    # 造舟页比登舟页高，窗口得装得下更高的那一页 —— 装不下就是把注册表单切了。
    # 量的是**真东西**：切到造舟页之后，那张卡片的底边有没有超出窗口。
    auth._switch(1)
    pump(app, 0.05)
    reg_card = auth.stack.widget(1).findChild(QFrame, "Card")
    bottom = reg_card.mapTo(auth, reg_card.rect().bottomLeft()).y()
    check("★ 更高的那一页（造舟）也装得下（卡片底边没被切）",
          bottom < auth.height() - 10, f"卡片底边 {bottom}，窗口 {auth.height()}")
    auth._switch(0)
    # 主界面的高度是**写死**的（跟登录页同一个数），不再是内容撑出来的 ——
    # 内容多一分少一分，多出来的空当会被 Qt 摊到某个间隔上（踩着过：删掉
    # 「输出结果」那排标题之后，按钮和账号行之间从 8px 悄悄变成 37px）。
    # 所以不能拿 sizeHint 比（那个跟实际布局不是一回事），得量**真正的东西**：
    # 最后一行（版本号）的底边离窗口下沿，必须正好是 BOTTOM_PAD。
    # ⚠ 量之前先 show() + 走一轮事件：不 show 的话布局还停在"没打磨过"的
    #   状态，量出来是 479 而不是 578（踩过）。
    view.show()
    pump(app, 0.05)
    v_bottom = view.version_label.mapTo(
        view, view.version_label.rect().bottomLeft()).y()
    check("★ 内容正好填满窗口（版本号底下就是 BOTTOM_PAD，不空也不挤）",
          abs(view.height() - v_bottom - mv.BOTTOM_PAD) <= 2,
          f"窗口 {view.height()} - 版本底边 {v_bottom} = "
          f"{view.height() - v_bottom}，该是 {mv.BOTTOM_PAD}")

    # 服务端说改 -> 推送 -> 客户端跟着改（只是不在界面上写出来）
    fake_api.route_mode = Route.IN
    fake_api.revision = "rev-in"
    view.on_push_event({"type": "config_changed", "revision": "rev-in"})
    ok = wait_for(app, lambda: view._opts.route_mode == Route.IN, timeout=10)
    check("★ 收到推送后线路方向跟着变", ok, view._opts.route_mode)
    check("★「分流」的说明换成回国的口径（这是唯一能看见方向的地方）",
          Route.HINTS[Route.IN] in view.rb_split.toolTip(), view.rb_split.toolTip())
    check("★ 方向落了盘（冷启动没网时先用它顶着）",
          '"in"' in cfg_mod.CONFIG_FILE.read_text(encoding="utf-8"),
          cfg_mod.CONFIG_FILE.read_text(encoding="utf-8")[-200:])

    # 在航时被切：必须真的重连一次
    restarts: list[int] = []
    real_start, real_stop = kernel.start, kernel.stop
    kernel.start = lambda *a, **k: restarts.append(1)
    kernel.stop = lambda *a, **k: restarts.append(0)
    try:
        view._set_state(STATE_SAILED)
        fake_api.route_mode = Route.OUT
        fake_api.revision = "rev-out-2"
        view.on_push_event({"type": "config_changed", "revision": "rev-out-2"})
        wait_for(app, lambda: view._opts.route_mode == Route.OUT, timeout=10)
    finally:
        kernel.start, kernel.stop = real_start, real_stop
    check("★ 在航时被切方向会重连（内核确实重启了一次）",
          restarts.count(1) >= 1, str(restarts))
    check("方向回到出国", view._opts.route_mode == Route.OUT, view._opts.route_mode)

    # ★ 封 B 不该把在线的 A 一起踢下线（真机上出过：A 登录着，管理员封 B，
    #   A 被踹回登录页）。两个成因都得堵住 —— 服务端 notify_kick 漏传 user_id
    #   变成广播，客户端又"谁的 kick 都当自己的"。这里盯客户端这一半。
    kicked_out: list[int] = []
    view.logged_out.connect(lambda: kicked_out.append(1))

    # 别人的（99999）—— 还挑最狠的 permanent=True（封禁），照样一点反应没有
    view.on_push_event({"type": "kick", "user_id": 99999,
                        "reason": "账号已被封禁", "permanent": True})
    pump(app, 0.3)
    check("★ 别人的 kick 不动我：还在航", session.state == STATE_SAILED, session.state)
    check("★ 别人的封禁通知不把我踹回登录页", not kicked_out, str(kicked_out))

    # 不带 user_id 的（老服务端 / 手写脚本）：保守认下来，照旧靠岸
    view.on_push_event({"type": "kick", "reason": "管理员把你踢下线了", "permanent": False})
    pump(app, 0.4)
    check("★ 不带 user_id 的 kick 照旧靠岸（不认名字的老服务端也管用）",
          session.state == STATE_DOCKED, session.state)

    # 点名给自己的（permanent）—— 靠岸之后还得回登录页
    view.on_push_event({"type": "kick", "user_id": session.user_id,
                        "reason": "账号已被封禁", "permanent": True})
    pump(app, 0.4)
    check("★ 点名给自己的封禁通知才把人踹回登录页", kicked_out == [1], str(kicked_out))

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
    check("★ 托盘右键菜单里是「确认退出」", "确认退出" in acts, str(acts))
    check("托盘菜单里是「主界面」（不是「显示主界面」）",
          "主界面" in acts and "显示主界面" not in acts, str(acts))
    seen = []
    t.quit_requested.connect(lambda: seen.append("quit"))
    t._quit_action.trigger()
    check("★ 点「退出」发出 quit_requested", seen == ["quit"], str(seen))

    # ---- TUN：托盘红点 ----
    # 用户要求：TUN 模式时给任务栏小图标加一个红点。
    # TUN 接管**全部**流量，而关窗之后程序就缩在托盘里 —— 得让人一眼
    # 看出来现在不只是挂了个系统代理。
    print("\n[TUN 模式的红点]")
    from PySide6.QtGui import QColor, QIcon, QPixmap

    from canoe_client import kernel as k_mod
    from canoe_client.options import RunOptions
    from canoe_client.ui import tray as tray_mod

    # 网卡名字必须自己定。不写的话清理残局时挑不出"自己那张网卡"，
    # 只能按"描述里带 Wintun"去猜 —— 那会把用户的 WireGuard 也删了。
    check("★ TUN 网卡名字写死了（清理残局才敢下手）",
          k_mod.TUN_INTERFACE_NAME == "canoe", k_mod.TUN_INTERFACE_NAME)
    tun_in = next(i for i in k_mod._inbounds(RunOptions(use_tun=True, use_system_proxy=True))
                  if i["type"] == "tun")
    check("★ TUN 入站带上了 interface_name",
          tun_in.get("interface_name") == "canoe", str(tun_in))
    check("★ 系统代理和 TUN 同时勾选时两个入站都在（不是二选一）",
          [i["type"] for i in k_mod._inbounds(
              RunOptions(use_tun=True, use_system_proxy=True))] == ["mixed", "tun"])
    check("都不勾时兜底给一个 mixed（内核起来总得有个口子）",
          [i["type"] for i in k_mod._inbounds(
              RunOptions(use_tun=False, use_system_proxy=False))] == ["mixed"])

    # 「TUN 不生效」那个坑：上一次没收干净留下的孤儿内核 + 残留网卡。
    check("★ 认得「网卡已存在」这类报错",
          k_mod._tun_unavailable(
              "configure tun interface: set ipv4 address: The object already exists"))
    check("★ 认得「网卡迟迟收不回去」这类报错",
          k_mod._tun_unavailable("open interface take too much time to finish!"))
    check("别的报错不算网卡冲突（不然白清一次）",
          not k_mod._tun_unavailable("bad key length, required 16, got 3"))

    # 清理残局用 pnputil，**不能用 Remove-NetAdapter**。
    # 踩过：那台机器上 `Get-NetAdapter` 有、`Remove-NetAdapter` 根本没有，
    # 而旧代码给它挂了 -ErrorAction SilentlyContinue —— 每次"清理"都一声
    # 不吭地什么也没干，用户那边就是"TUN 怎么都开不起来"。
    check("★ 清理残局用 pnputil（Remove-NetAdapter 在有些机器上不存在）",
          "pnputil" in k_mod._HEAL_PS and "Remove-NetAdapter" not in k_mod._HEAL_PS,
          "脚本里出现了 Remove-NetAdapter")
    # 真正卡住 TUN 的是**幽灵 Wintun 设备**（进程没了、设备实例还挂着），
    # sing-box 再建网卡就撞 "Cannot create a file when that file already exists"
    check("★ 会删掉 Status 不是 OK 的 Wintun 幽灵设备",
          "SWD\\WINTUN" in k_mod._HEAL_PS and "$_.Status -ne 'OK'" in k_mod._HEAL_PS)
    # 幽灵设备**必须无条件清**，不能挂在那道"有没有实例在跑"的保险后面：
    # 它 Status 不是 OK，按定义就不可能被谁用着，而它恰恰是卡住 TUN 的那个。
    # 挂上去的话，只要用户开着任意一个轻舟（哪怕只是系统代理），
    # 残留就永远清不掉 —— 又回到"怎么点都开不起来"。
    check("★ 幽灵设备无条件清（不受「有没有实例在跑」那条保险影响）",
          k_mod._HEAL_PS.index("Get-PnpDevice") < k_mod._HEAL_PS.index("$alive"),
          "清幽灵设备那段排在了 $alive 后面")
    check("★ 只有那张叫 canoe 的活网卡才看有没有实例在跑",
          "$alive -eq 0" in k_mod._HEAL_PS)

    # 用户的原话："靠岸、再启航就正常了" —— 差别就在中间那几秒。
    # 起 TUN 之前也要等一拍，不能只在停 TUN 之后等。
    import inspect as _inspect

    start_src = _inspect.getsource(k_mod.SingBoxKernel.start)
    check("★ 起 TUN 之前会等一拍（切换那条路上补的）",
          "_ever_ran" in start_src and "TUN_TEARDOWN_GRACE" in start_src,
          "start() 里没找到起 TUN 前的等待")
    check("首次启航不白等（没跑过内核就不睡）",
          "self._had_tun and self._ever_ran" in start_src)
    # 清过一遍还撞，说明是别人占着 —— 要给能照着做的话，不是内核原话
    check("★ 起不来时给的是人话（能照着做），不是那句 Cannot create a file",
          "轻舟窗口" in start_src or "重启" in start_src,
          "没找到给用户看的说明")

    # --selftest 要把 TUN 现场一起报出来。TUN 出问题的时候，隔着屏幕靠
    # 一轮轮问"你那儿网卡列表长什么样"太慢，用户也描述不准。
    from canoe_client.tun import diagnostics

    diag = diagnostics()
    check("★ 自检能报出 TUN 现场（省掉一轮轮来回问）",
          {"admin", "wintun_dll", "adapter_exists", "phantom_wintun",
           "singbox_procs"} <= set(diag),
          str(sorted(diag)))
    check("自检里带上网卡名，方便对上",
          diag.get("interface_name") == k_mod.TUN_INTERFACE_NAME, str(diag.get("interface_name")))
    # ★ wintun_dll 这一项以前恒为 false —— diagnostics() 没把 bin 目录传下去，
    #   wintun_present() 只能猜 cwd 和 System32，而打包后的进程 cwd 是
    #   用户启动它的地方（桌面）。dll 明明在包里，报告却说没有。
    #   下面这条钉住"报告说的 = bin 目录里到底有没有"。
    from canoe_client import config as cfg_mod

    _dll = cfg_mod.BIN_DIR / "wintun.dll"
    check("★ wintun_dll 问的是真 bin 目录（不是 cwd —— 打包后恒 false）",
          diag.get("wintun_dll") is _dll.is_file(),
          f"报告 {diag.get('wintun_dll')} / 文件 {_dll.is_file()} @ {cfg_mod.BIN_DIR}")

    pm = QPixmap(32, 32)
    pm.fill(QColor("#2E8BFF"))
    base = QIcon(pm)
    dotted = tray_mod.with_dot(base)
    img_plain = base.pixmap(32, 32).toImage()
    img_dot = dotted.pixmap(32, 32).toImage()
    dot_px = img_dot.pixelColor(16, 16)
    check("★ 正中真的点上了红点",
          dot_px.red() > 150 and dot_px.green() < 110, dot_px.name())
    check("原图同一个位置不是红的",
          img_plain.pixelColor(16, 16).name() != dot_px.name(),
          img_plain.pixelColor(16, 16).name())

    t2 = Tray(base)
    plain_key = t2.icon.icon().cacheKey()
    t2.set_tun(True)
    check("★ TUN 起效时图标换成带红点的那张",
          t2.icon.icon().cacheKey() != plain_key)
    check("★ 提示语点明在接管全部流量", "TUN" in t2.icon.toolTip(), t2.icon.toolTip())
    t2.set_tun(False)
    check("★ 不跑了就换回去（红点不能一直挂着）",
          t2.icon.icon().cacheKey() == plain_key and "TUN" not in t2.icon.toolTip())

    # 信号口径：是"TUN 真的在跑"，不是"勾选框被勾上了"
    print("\n[TUN 红点的触发口径]")
    fired: list[bool] = []
    view.tun_active_changed.connect(lambda on: fired.append(on))
    saved_state = session.state
    view._opts.use_tun = True
    view._set_state(STATE_SAILED)
    check("★ 在航 + TUN -> 发 True（红点亮）", fired == [True], str(fired))
    view._set_state(STATE_DOCKED)
    check("★ 靠岸 -> 发 False（红点灭）", fired == [True, False], str(fired))
    check("★ 只是勾着但没启航，不算「TUN 在跑」（点红点就是骗人）",
          fired[-1] is False, str(fired))
    view._set_state(saved_state)
    view._opts.use_tun = False
    view._sync_tun_indicator()

    # ---- 更新弹窗 ----
    # 以前"有新版本"是个 QMessageBox：只有 OK，点完什么也不发生，
    # 而且浅色底配近白字，整框的字都糊了。现在换成自家的无边框窗口，
    # 整条链子（下载 -> 校验 -> 替换 -> 重启）都在这一个框里走完。
    print("\n[更新弹窗]")
    from canoe_client.ui.update_dialog import UpdateDialog
    from canoe_client.update import UpdateInfo

    dlg = UpdateDialog(UpdateInfo(latest="1.1.0", current="1.0.0", size=63 * 1024 * 1024,
                                  notes="修好了点更新没反应"))
    # 真 show 出来 —— 没 show 过的窗口里 isVisible() 一律是 False，
    # 拿它断言"按钮在不在"只会永远失败（踩过）
    dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
    dlg.show()
    app.processEvents()
    check("弹窗标题带版本跨度", "1.1.0" in dlg.titlebar.sub.text(),
          dlg.titlebar.sub.text())
    check("★ 弹窗不给最小化（它是个对话框）", dlg.titlebar.min_btn.isHidden())
    check("空闲态：两个按钮都在", dlg.action_btn.isVisible() and dlg.later_btn.isVisible())
    check("空闲态：进度条藏着（一上来就摆个空条会让人以为在下）", dlg.bar.isHidden())
    check("空闲态按钮写着「立即更新」", dlg.action_btn.text() == "立即更新")

    fired = []
    dlg.install_requested.connect(lambda: fired.append("go"))
    dlg.action_btn.click()
    app.processEvents()
    check("★ 点「立即更新」发出 install_requested", fired == ["go"], str(fired))
    check("★ 进入下载态：进度条出来了", not dlg.bar.isHidden())
    check("★ 下载态只剩「取消」可点（避免重复触发）", dlg.action_btn.isHidden())

    dlg.set_progress(21 * 1024 * 1024, 63 * 1024 * 1024)
    check("★ 进度条真的在动", 0 < dlg.bar.value() < 1000, str(dlg.bar.value()))
    check("★ 状态行报出百分比", "33%" in dlg.status.text(), dlg.status.text())

    cancel = []
    dlg.cancel_requested.connect(lambda: cancel.append("stop"))
    dlg.later_btn.click()
    app.processEvents()
    check("★ 下载中点「取消」发出 cancel_requested", cancel == ["stop"], str(cancel))

    dlg.fail("", cancelled=True)
    check("取消后回到原样（不是报错）", dlg.stage == "idle" and dlg.error.isHidden())

    dlg.fail("摘要对不上")
    check("★ 失败时给出可读原因", dlg.error.isVisible() and "摘要" in dlg.error.text())
    check("★ 失败后能重试", dlg.action_btn.text() == "重试")

    closed = []
    dlg.closed.connect(lambda: closed.append(1))
    dlg.close()
    app.processEvents()
    check("★ 关掉时发 closed（主界面好把引用放掉）", closed == [1], str(closed))

    forced = UpdateDialog(UpdateInfo(latest="2.0.0", current="1.0.0", min_version="2.0.0"))
    check("★ 强制升级时不给「稍后再说」", forced.later_btn.isHidden())

    # ---- 单实例 ----
    # 轻舟占着一个固定的本地端口和一张固定名字的 TUN 网卡，两个实例没法共存：
    # 后来者绑不上端口，启航只会报 "Only one usage of each socket address"，
    # 而两个窗口长得一模一样 —— 用户关掉一个还有一个，报成"点 × 关不掉"。
    print("\n[单实例保护]")
    from PySide6.QtCore import QLockFile

    from canoe_client import single as single_mod
    from canoe_client.tun import relaunch_as_admin as tun_relaunch

    single_mod.CONFIG_DIR = tmp / "single"
    single_mod._lock = None
    check("★ 第一个实例能拿到锁", single_mod.acquire())
    other = QLockFile(str(tmp / "single" / "canoe.lock"))
    check("★ 第二个实例抢不到同一把锁（这就是保护生效）",
          not other.tryLock(300), "第二个居然也拿到了")
    single_mod.release()
    check("★ 放锁之后别人能拿到", QLockFile(str(tmp / "single" / "canoe.lock")).tryLock(300))

    # 强制关窗 = 真的退出。少这一步的话，托盘在时
    # setQuitOnLastWindowClosed(False) 会让进程活下来变成只有托盘的僵尸 ——
    # "以管理员身份重启"正好走 force_close，于是留下两个实例。
    import inspect as _ins2

    check("★ 强制关窗会真的退出进程（不然提权重启会留下僵尸实例）",
          "QApplication.quit()" in _ins2.getsource(type(view).closeEvent),
          "closeEvent 里没有 quit")
    check("★ 提权重启前先放锁（不然新进程会以为自己撞上别人）",
          "release()" in _ins2.getsource(tun_relaunch),
          "relaunch_as_admin 没放锁")

    # ------------------------------------------------------------------
    # 标题栏那个小徽标：画出来的东西必须**在框里**（不贴边、大致居中、够大）。
    #
    # 这条是给一个真出过的 bug 立的：`_boat_pixmap` 把**设备像素**
    # （size*dpr）传进了 paint_boat，而 Qt 画在设过 devicePixelRatio 的
    # QPixmap 上时**会自己乘 dpr** —— 于是船被画成两倍大、位置也偏出去，
    # 18px 的小标只剩左下角一块。用户原话："根本看不出是船，就一个尖尖"。
    # 光看代码看不出来（两处都对，错在单位混用），只能靠渲染一版来钉。
    # ------------------------------------------------------------------
    print("\n[徽标] 小标得画在框里")
    from canoe_client.ui import artwork as _art  # noqa: PLC0415

    for _size in (18, 20, 24):
        _pm = _art.app_mark(_size)
        _img = _pm.toImage().convertToFormat(QImage.Format_ARGB32)
        _w, _h = _img.width(), _img.height()
        _cols = [x for x in range(_w)
                 if any(_img.pixelColor(x, y).alpha() > 8 for y in range(_h))]
        _rows = [y for y in range(_h)
                 if any(_img.pixelColor(x, y).alpha() > 8 for x in range(_w))]
        if not _cols or not _rows:
            check(f"★ {_size}px 小标画出了东西", False, "整张透明")
            continue
        _l, _r, _t, _b = min(_cols), max(_cols), min(_rows), max(_rows)
        _bw, _bh = _r - _l + 1, _b - _t + 1
        _clipped = _l < 1 or _t < 1 or _r > _w - 2 or _b > _h - 2
        _off = max(abs((_l + _r) / 2 - (_w - 1) / 2) / _w,
                   abs((_t + _b) / 2 - (_h - 1) / 2) / _h)
        _big = _bw >= _w * 0.5 and _bh >= _h * 0.5
        check(f"★ {_size}px 小标不贴边、居中、够大",
              (not _clipped) and _off < 0.15 and _big,
              f"bbox=({_l},{_t})-({_r},{_b}) 画布={_w}x{_h} 偏移={_off:.2f} "
              f"贴边={_clipped} 够大={_big}")

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
