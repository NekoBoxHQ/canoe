# 轻舟客户端 / Canoe Client

Windows 桌面程序。界面只有三页：**造舟（注册）、登舟（登录）、主界面**。

> **阶段状态**：阶段 1-6 已完成 —— 账号、订阅、更新全部走服务端。
> 订阅内容在传输中是密文，客户端**只在内存里**解密使用，不落盘。
> 关窗收进托盘，右键托盘 →「退出」才真退出。见根目录 `README.md`。

## 运行

```bash
pip install -e ../canoe-core
pip install -r requirements.txt

# bin/ 里需要有 sing-box.exe（见 bin/README.md）
python run.py
```

**最快试用路径**：双击 → 造舟（注册）→ 登舟 → 点「启航」。
账号在服务端，必须联网。

## 界面

夜色主题：深蓝渐变打底，远山 + 月亮 + 水波 + 一叶小舟全是 `QPainter` 矢量画的
（`ui/artwork.py`，不依赖任何图片资源，任意尺寸都清晰）。

窗口是**无边框 + 圆角**，标题栏自己画（`ui/window_base.py`：小徽标 +「轻舟 · 轻舟已过万重山」+ – ×，按住可拖动）。

```
登舟（380×658）                            主界面（420×577）
┌──────────────────────┐                 ┌──────────────────────┐
│ ⛵ 轻舟 · 轻舟已过万重山  – ×│             │ ⛵ 轻舟 · 轻舟已过万重山  – ×│
│         ⛵            │                 │    ──── 轻舟 ────     │
│        轻 舟          │                 │  🌍 日本家宽 🌍       │ ← 节点名
│   One boat, one tap. │                 │      已启航           │ ← 状态
│ ┌──────────────────┐ │                 │ [ 🚀 启 航 ][ 🚢 靠 岸 ]│
│ │ 登舟              │ │  登舟成功        │ ┌──────────────────┐ │
│ │ 报上名号，登陆渡江 │ │ ─────────────► │ │ ● 分流    ○ 全局  │ │
│ │ 👤 用户名         │ │                 │ │ ☑ 系统代理 ☐ TUN  │ │
│ │ 🔒 密码       👁  │ │                 │ └──────────────────┘ │
│ │ [  → 登 录  ]     │ │                 │ [更新][TCping][URL测试]│
│ │ [ 直接体验(跳过注册)]│ │                │ ┌──────────────────┐ │
│ │ 还没有渡口？去造舟 →│ │                │ │ 📄 输出结果       │ │
│ └──────────────────┘ │                 │ │ ● TCP 延迟：38ms  │ │
│   ～～～ ⛵ ～～～     │                 │ └──────────────────┘ │
└──────────────────────┘                 │  访客           离舟  │
                                          │   ～～～～～～～～～    │
                                          └──────────────────────┘
```

状态文案（`canoe_core.Text`）：

| 状态 | 文案 |
|---|---|
| 连接中 | 渡江中… |
| 连接成功 | 已启航 |
| 连接断开 | 已靠岸 |
| 错误 | 风浪太大，请重试 |

界面上**没有**节点地址、端口、协议、密码，也**没有**任何查看/导出配置的入口。

## 可选设置

界面是两排，整体居中，**不带行标签**：

```
        ○ 分流          ○ 全局
        ☑ 系统代理      ☐ TUN 模式
```

| 排 | 选项 | 默认 | 是否互斥 | 说明 |
|---|---|---|---|---|
| 第一排 | 分流 | ✅ | 二选一 | 局域网与大陆直连，其余走代理 |
| 第一排 | 全局 | | 二选一 | 所有流量都走代理 |
| 第二排 | 系统代理 | ✅ | **可并存** | 把 Windows 系统代理指向本机端口，不需要管理员权限 |
| 第二排 | TUN 模式 | ☐ | **可并存** | 接管全部流量，需要管理员权限 + `wintun.dll` |

### 两排的互斥性不一样，这是刻意的

- 第一排是**分流模式**：两种模式天然对立，用单选（`QButtonGroup`）
- 第二排是**接管方式**：系统代理和 TUN 可以同时开，所以用**两个独立的
  `QCheckBox`**，而不是单选

> 早先版本用一个 `mode` 字段表达第二排，结果是勾了 TUN 就把系统代理顶掉 ——
> 那是实现做错了，不是需求。现在 `RunOptions` 里是两个独立布尔
> `use_system_proxy` / `use_tun`，`_inbounds()` 按勾选"有就加"，
> 两个都勾时配置里会同时出现 mixed 入站和 tun 入站。
>
> （第一排的按钮必须分属一个 `QButtonGroup`：它们的父控件和别的控件同在一张卡片上，
> 不分组的话 Qt 会把所有按钮当成一个互斥组。这个 bug 渲染截图才发现。）

### TUN 恒为双栈

TUN 模式**始终同时接管 IPv4 和 IPv6**（`172.19.0.1/30` + `fdfe:dcba:9876::1/126`），
不暴露开关 —— 关掉只会让走 IPv6 的流量绕过代理，纯属坑。

### 两个都不勾会怎样

内核仍会起一个本地 mixed 入站（`127.0.0.1:20818`），只是不接管系统流量。
不会出现"内核起来了却没有任何入口"的情况。

---

## 工具按钮

主界面有三个测试/维护按钮：

| 按钮 | 做什么 | 实现 |
|---|---|---|
| 🔄 **更新** | 检查客户端是否有新版本 | `update.py` —— 从 `client.json` 里的 `update_url` 拉一个 JSON（`{version, url, notes}`），和当前版本比大小。地址默认为空，阶段4 指向自己的服务器即可 |
| 📡 **TCping** | 测**本机到节点**的 TCP 握手延迟（4 次，报最小/平均/最大/丢包） | `nettest.tcping()` —— 直接 connect 节点地址，不经过代理 |
| 🔗 **URL测试** | 测**端到端**：本机 → 节点 → 目标站 | `nettest.url_test()` —— 经本地代理请求 `generate_204`，测的是代理通道 |

两个测试的区别值得记住：

```
TCping   本机 ──TCP握手──► 节点          测"我到节点这条路通不通"
URL测试  本机 ──► 节点 ──► 目标站          测"整条链路能不能上网"
```

所以 **TCping 通但 URL 测试失败** → 多半是节点那边或它的出口有问题；
**两个都不通** → 大概率是本机到节点这一段断了。

URL 测试需要先启航（它要走本地代理），没启航时点它会提示而不是报错。

## 测试结果框（不是日志框）

主界面下方是一行结果（绿点 + 文本），**只显示最新一条**：

```
TCP 延迟：38ms
```

固定格式：`TCP 延迟：xx ms` / `URL 耗时：xx ms`。
只留一行是为了**永远不用滚动条**，底下也不会拖着一块空白。

### ★ 为什么不显示完整日志

**内核日志里带节点域名**，比如：

```
outbound/shadowsocks[proxy]: outbound connection to one.leycc.com:443
```

把它显示在界面上，等于把节点信息暴露给用户 —— 这违反项目的硬性要求
（客户端不显示节点地址/端口/协议/密码）。

所以 `logbus.py` 里做了两层：

| 层 | 作用 |
|---|---|
| `since(seq)` | 取全部行，**含内核日志** |
| `visible_since(seq)` | **只取可显示的行**（`结果` / `错误`），界面用这个 |

内核输出照读不误（不读的话子进程会写满管道阻塞），但**永远不进界面**。
`tests/test_gui.py` 和 `tests/test_tools.py` 里有断言守着这条。

另外还有一道兜底：`_mask_secrets()` 会把结果文本里出现的域名打码
（`https://api.example.com/x` → `https://＊＊＊/x`），
万一将来某条错误信息里带了域名，也不会漏出去。

### 其它实现细节

- 结果是 `logbus.py` 里的线程安全环形缓冲：内核输出在工作线程里逐行读出来塞进去，
  界面用 `QTimer` 每 300ms 拉一次增量。
- **ANSI 颜色码会被剥掉**。sing-box 即使输出到管道也带 `\x1b[36m` 这类转义，
  不处理的话日志里会出现 `[36mINFO[0m`。
- **关掉了 sing-box 自己的时间戳**（`log.timestamp: false`），避免重复。

想看完整内核日志时，把 `%APPDATA%\Canoe\client.json` 里
`options.log_level` 调成 `debug`，再用 `sing-box` 手动跑一次配置即可
（界面上不会显示，这是刻意的）。

`bypass_lan` / `bypass_china` 是 `profile` 的派生属性（分流时都为真，全局时都为假），
`kernel.py` 照旧读这两个名字。

## 模块

| 文件 | 职责 |
|---|---|
| `app.py` | 入口、页面切换、托盘接线、退出兜底清理、`--selftest` 自检 |
| `api.py` | 与服务端通话（注册 / 登录 / 会话 / 订阅解密 / 更新） |
| `links.py` | 订阅链接解析（实现在 `canoe-core`，这里只做 re-export） |
| `events.py` | SSE 长连接，接收服务端推送 |
| `config.py` | 本地配置 + 写死的服务端地址 + `CANOE_CA_BUNDLE` |
| `credstore.py` | ★ 「记住账号密码」：密码走 Windows DPAPI 加密，不存明文 |
| `kernel.py` | sing-box 配置生成与进程管理 |
| `options.py` | 运行选项 |
| `sysproxy.py` | Windows 系统代理（备份 / 还原 / 自愈） |
| `tun.py` | 全局模式先决条件检查（管理员、wintun） |
| `worker.py` | 线程池，避免网络请求卡住界面 |
| `ui/tray.py` | 任务栏托盘（关窗收起来，右键才退出） |
| `ui/auth_view.py` | 造舟 / 登舟 |
| `ui/main_view.py` | 主界面 |
| `ui/style.py` | QSS |
| `ui/artwork.py` | ★ 夜色山水背景 + 帆船徽标 + 线性图标，全矢量 |
| `ui/window_base.py` | ★ 无边框圆角窗口 + 自绘标题栏（拖动 / 最小化 / 关闭） |
| `ui/controls.py` | ★ 自绘的勾选框 / 单选框（QSS 画不出白勾和圆点） |

## 打包

```bat
build.bat                    :: -> dist\Canoe.exe
python scripts\package_release.py   :: -> dist\Canoe-<版本>-win64.zip
```

产物是**一个文件** `dist\Canoe.exe`（onefile）：用户下载解压出来就一个
exe，双击即用，不会有一堆 dll 铺在桌面上。运行的时候 PyInstaller 把内容
解到系统临时目录（`%TEMP%\_MEIxxxx`），进程退出就删掉。

代价：每次启动多花一点解压时间（80 多 MB），个别杀软对 onefile 更敏感。
换的是"发给普通用户只有一个文件"。

打包好的 zip 里也只有 `Canoe.exe` 一个文件。详见
[`../docs/05-packaging.md`](../docs/05-packaging.md)。

## 自检

```bat
Canoe.exe --selftest
```

检查内核路径、内核版本、规则集、图标是否就位，结果写到
`%APPDATA%\Canoe\selftest.txt`。退出码 `0` 正常、`2` 缺内核。
用户报"打不开"时让他跑这个。

## 测试

```bash
# 配置生成 —— 不需要联网（46 项）
python tests/test_config.py

# 订阅链接解析（37 项）
python tests/test_links.py

# 工具按钮：TCPing / URL 测试（31 项）
python tests/test_tools.py

# Windows 系统代理的备份 / 还原 / 自愈（33 项）
python tests/test_sysproxy.py

# ★ 「记住账号密码」：DPAPI 加解密、失效降级（26 项）
#   自己开临时 APPDATA 跑，不动你本机那份 client.json
python tests/test_remember.py

# GUI 端到端 —— 离屏，会真的登舟一次（93 项）
set QT_QPA_PLATFORM=offscreen
python tests/test_gui.py

# 与服务端联调 —— 要先起 canoe-server（51 项）
set CANOE_SERVER_URL=https://127.0.0.1:8443
python tests/test_server.py
```

## 规则集

「绕过大陆」依赖 `bin/ruleset/*.srs`。缺失时会退化成启动时去
GitHub 下载（国内大概率失败），所以正式发布前跑一次：

```bash
python scripts/fetch_rulesets.py          # 缺失才下载
python scripts/fetch_rulesets.py --check  # 只检查
```

## 两个 PySide6 的坑（已在 `worker.py` 里规避）

1. **`QRunnable` 没有 Python 引用时会被 GC 回收**，连带 `signals` 一起销毁，
   回调静默不触发。所以用 `_ACTIVE` 集合持有运行中的 Worker。
2. **释放引用不能在信号回调里立刻做**。如果在槽函数里 `_ACTIVE.discard(self)`，
   引用归零会让 `_Signals` 在信号**还在分发**的过程中被销毁，
   排在后面的回调就永远收不到。释放必须延后到下一个事件循环 tick。

这两个问题都只在运行时暴露，编译期和静态检查发现不了。
