# 轻舟 / Canoe

> 轻舟已过万重山
> One boat, one tap.

Windows 桌面代理工具。客户端极简到只有「节点名 + 启航 + 靠岸」。

| | |
|---|---|
| 中文名 | 轻舟 |
| 英文名 | Canoe |
| 客户端 | `Canoe.exe`（`canoe-client/`） |
| 服务端 | Canoe Server（`canoe-server/`） |
| 公共库 | `canoe-core/` |
| 命名空间 | `com.canoe.client` / `com.canoe.server` |

---

## 进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 客户端框架 + 内核跑通 | ✅ |
| 2 | 推送到 git 仓库 | ✅ |
| 3 | 服务端（API + 数据库 + 更新通道 + 推送） | ✅ |
| 4 | 部署材料（Debian 12 + HTTPS + Web 管理面板） | ✅ |
| 5 | 客户端与服务端联调 | ✅ |
| 6 | 订阅分发 + 传输加密 + 托盘 | ✅ |

---

## 架构：服务端完全可控

模型的中心是一句话：**客户端手里有什么，完全由服务端在每次交互时决定。**

```
            ①登舟：拿令牌 + 一把会话级订阅密钥
   客户端  ─────────────────────────────────►  服务端
            ②每次启航 / 点更新：
              重新证明身份 -> 拉订阅（密文）-> 就地解密
                     ◄─────────────────────────
                        ③服务端随时可以不发（空信封）
                          -> 客户端就地销毁本地订阅
```

节点在**服务端后台的「订阅栏」**里逐用户配置（一行一个链接，
`ss:// vmess:// vless:// trojan://`，也可以整体 base64）。
你的节点服务器和 Canoe 服务端互不关联 —— Canoe 只负责"发不发"。

几条关键性质：

- **传输是密文**：订阅走 HTTPS 之外，服务端还用一把**登录时现发的会话密钥**
  再包一层 AES-256-GCM（`canoe_core/crypto.py`）。反向代理、日志、
  能碰服务端的中间环节看到的都只有密文。
- **密钥只在内存**：`sub_key` 不落盘，进程一退就没了；下次登舟重新拿。
  服务端不认这个会话了，客户端就什么都拉不到。
- **撤回立刻生效**：管理员清空订阅栏 / 封禁 / 到期 → 客户端拉回来是空的
  → **就地销毁**本地订阅；正在航行的会自动靠岸。
  （改订阅会走 SSE 定向推送给该账号，最迟下一次心跳也会发现。）
- **客户端不落节点**：订阅只在内存里，不写配置文件、不提供导出。
- **线路方向也是服务端定的**：同一个客户端，管理员可以把它设成
  「出国」（大陆直连，其余走代理 —— 默认）或「回国」（国外直连，
  国内走代理，给在国外的客户用）。客户端底下就一行只读的字，
  **没有任何可改的入口**；在航时被切会当场重连。

对应测试：`canoe-client/tests/test_server.py`（对着真服务端跑，含
"响应里没有订阅明文/域名/链接"等多条断言）、`canoe-server/smoke_test.py`。

> ⚠️ 说清楚边界：这一层加密**防不住拿到客户端的用户把节点扒出来** ——
> 客户端必须能解密，密钥就在它手上。它防的是传输链路上的中间环节；
> 真正决定"能不能用"的是服务端随时可以不发。

---

## 目录结构

```
canoe/
├── canoe-core/            公共库（模型契约 / 常量 / 加密信封 / 链接解析）
├── canoe-client/          桌面客户端
│   ├── canoe_client/
│   │   ├── app.py         入口 + 页面切换 + 托盘 + 退出兜底清理
│   │   ├── api.py         与服务端通话
│   │   ├── links.py       订阅链接解析（实现在 canoe-core）
│   │   ├── events.py      SSE 长连接（服务端推送）
│   │   ├── kernel.py      sing-box 配置生成与进程管理
│   │   ├── sysproxy.py    Windows 系统代理（含自愈）
│   │   └── ui/            界面（含 tray.py 托盘）
│   ├── build.bat          一键打包成 Canoe.exe
│   └── tests/             6 套，共 281 项
│
├── canoe-server/          服务端
│   ├── serve.py           统一启动器（双端口，单进程）
│   ├── canoe_server/
│   │   ├── routers/       client.py / admin.py
│   │   └── services/      nodes（节点与订阅正文）/ broadcast / updates
│   ├── panel/             Web 管理面板
│   ├── deploy/install.sh  交互式安装（域名 / 端口 / 证书）
│   └── smoke_test.py      端到端冒烟
│
└── docs/                  五份设计文档
```

---

## 客户端

### 跑起来

```bash
cd canoe-core && pip install -e .
cd ../canoe-client && pip install -r requirements.txt
python run.py
```

`bin/` 下需要有：

| 文件 | 必需性 | 说明 |
|---|---|---|
| `sing-box.exe` | **必需** | 代理内核，本项目用 1.14.2 验证 |
| `bin/ruleset/*.srs` | 绕过大陆需要 | 已内置 |
| `wintun.dll` | 全局(TUN)模式需要 | <https://www.wintun.net/> |

### 打包

```bat
build.bat
```

产物 `dist\Canoe\Canoe.exe`。整个 `dist\Canoe\` 目录打 zip 发给用户，
目标机器不需要装 Python。

### 服务端地址是写死的

`canoe_client/config.py` 里：

```python
SERVER_BASE = "https://canoe.s-ui.com:58588"
```

客户端是「下载即用」的，不给用户任何填地址的入口 —— 换服务器就重发一版客户端。
本机联调时可以用 `CANOE_SERVER_URL` 环境变量临时顶掉（不影响打包发布的行为）。

> 面板端口在部署时随便改；客户端更新与订阅用的 `58588` 是固定的，两者互不影响。

### 关窗 ≠ 退出

点右上角的关闭只是**收进托盘**，代理照常跑；要真正退出得
**右键托盘图标 → 退出**（退出时会先还原系统代理、再停内核）。
系统没有托盘时会自动退化成普通行为：关窗就是关窗。

---

## 服务端

### 本地起一个（自签 HTTPS）

```bash
cd canoe-server
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python seed.py              # 建管理员账号
python run_local_https.py   # https://127.0.0.1:8443
```

客户端联调时用 `CANOE_CA_BUNDLE` 指自签证书：

```bat
set CANOE_SERVER_URL=https://127.0.0.1:8443
set CANOE_CA_BUNDLE=canoe-server\data\certs\local-cert.pem
```

**不要**去关 TLS 校验 —— 关了就给了中间人伪造渡口、骗走用户令牌的机会。

下面命令都按**已经是 root**写 —— VPS 上装完就是。不是 root 的话先 `sudo -i` 切过去。

### 部署到服务器

**在服务器上一条命令** —— 出来就是管理菜单，选 `1` 安装：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/NekoBoxHQ/canoe/main/canoe-server/deploy/canoe.sh)"
```

整台机器只需要这一个脚本，代码和后续文件它自己去项目拉。装完以后直接：

```bash
canoe          # 菜单：安装 / 启动 / 停止 / 重启 / 状态 / 配置 / 升级 / 卸载
canoe status   # 也可以直接用子命令（写进脚本/定时任务）
```

**发一版客户端**（包挂在 GitHub Release 上，服务端自己去拉）：

```bash
# 开发机：出包，把 zip 和 .sha256 一起挂到 Release（tag 形如 v1.0.31）
python scripts/package_release.py

# 服务器：拉下来发布
canoe release            # 最新那个 Release
canoe release v1.0.31    # 指定 tag
canoe release --list     # 看已经发过哪些
```

> 用 `bash -c "$(curl …)"`，**不要**用 `curl … | bash` ——
> 管道会占掉 stdin，菜单和向导就再也读不到你敲的字了。

安装向导会依次问：域名、面板端口、管理员账号、证书方式
（Let's Encrypt / 自签 / 已有证书 / 不要 TLS）。
**不问客户端端口** —— 它固定 `58588`，客户端把它写死在代码里了。
细节见 `canoe-server/deploy/README.md`。

Web 管理面板在 `<域名>:<面板端口>/panel`，管用户、节点、会话、版本发布。
**给客户发节点**：先在「节点」页贴一行节点链接，再去「用户」页点「分配节点」勾给他。

> 装完之后**别再去跑 install.sh** —— 它检测到已经装过就会停下，把 `canoe`
> 那条命令指给你。安装向导只该在第一次、或者你明确要改域名/端口/证书时走
> （`canoe config` → 重新走安装向导）。

---

## 测试

全部通过后才算改完。改完任何一处请整套重跑。

```bash
# —— 客户端（canoe-client/）——
python tests/test_config.py     # 配置生成 + 安全断言（46 项）
python tests/test_links.py      # 订阅链接解析（37 项）
python tests/test_sysproxy.py   # 系统代理与自愈（33 项）
python tests/test_tools.py      # 日志总线 / 版本 / TCping / URL 测试（31 项）
set QT_QPA_PLATFORM=offscreen
python tests/test_gui.py        # GUI 端到端，会真启航一次（83 项）

# 联调（要有一个在跑的服务端）
set CANOE_SERVER_URL=https://127.0.0.1:8443
set CANOE_CA_BUNDLE=..\canoe-server\data\certs\local-cert.pem
python tests/test_server.py     # 49 项

# —— 服务端（canoe-server/）——
python smoke_test.py https://127.0.0.1:8443 --insecure   # 105 项
cd panel && bun test_panel.mjs                            # 面板 DOM（30 项）
bash deploy/test_canoe_sh.sh                              # 管理脚本（44 项）
```

合计 **458 项**。

---

## 外部依赖

| 组件 | 用途 | 获取 |
|---|---|---|
| **sing-box** | 代理内核（客户端用；服务端不碰流量） | [Releases](https://github.com/SagerNet/sing-box/releases)，本项目用 1.14.2 |
| **wintun.dll** | 仅全局(TUN)模式 | <https://www.wintun.net/> |
| Nginx + certbot | 服务端部署 | 系统包管理器 |

---

## 免责声明

本项目是代理软件的**客户端-服务端管理框架**，用于自建网络接入与流量管理。
请在法律允许的范围内使用。
