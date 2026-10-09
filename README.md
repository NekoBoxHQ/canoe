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

---

## 架构：客户端拿不到真实节点

这是整个项目最重要的一条约束，其它设计都是围着它转的。

```
客户端 ──VLESS+WS+TLS──► 中转层（服务端）──► 真实节点
   │                          │
   │ 只知道：入口域名           │ 真实主机/端口/UUID 只存在于
   │         入口端口           │ 服务端数据库与中转层配置里
   │         入口 UUID          │
   │         自己的登录令牌      │
```

- 客户端**只**拿到：服务端地址（写死）+ 用户令牌 + 每次启航下发的中转入口。
- 真实节点由服务端在中转层决定和路由，客户端全程不参与。
- 客户端**没有**导出入口，**不落盘**任何节点信息，内核配置写到临时文件、读完即删。
- 管理端能看到的真实节点信息，永远不会出现在任何面向客户端的响应里
  （由 `canoe-core` 的 `assert_whitelisted()` 在模型层强制）。

对应测试：`canoe-client/tests/test_server.py`（33 项，含 4 条泄漏断言）、
`canoe-server/smoke_test.py`（97 项，含多条「响应里不许出现 real_」）。

---

## 目录结构

```
canoe/
├── canoe-core/            公共库（模型契约 / 常量 / 文案 / 密码哈希）
├── canoe-client/          桌面客户端
│   ├── canoe_client/
│   │   ├── app.py         入口 + 页面切换 + 退出兜底清理
│   │   ├── api.py         与服务端通话
│   │   ├── entry.py       中转入口 -> sing-box 出站
│   │   ├── events.py      SSE 长连接（服务端推送）
│   │   ├── kernel.py      sing-box 配置生成与进程管理
│   │   ├── sysproxy.py    Windows 系统代理（含自愈）
│   │   └── ui/            界面
│   ├── build.bat          一键打包成 Canoe.exe
│   └── tests/             5 套，共 215 项
│
├── canoe-server/          服务端
│   ├── serve.py           统一启动器（双端口，单进程）
│   ├── canoe_server/
│   │   ├── routers/       client.py / admin.py
│   │   └── services/      relay（中转配置）/ broadcast（推送）/ updates
│   ├── panel/             Web 管理面板
│   ├── relay/             中转层 sing-box 配置模板
│   ├── deploy/install.sh  交互式安装（域名 / 端口 / 证书）
│   └── smoke_test.py      端到端冒烟（97 项）
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

### 部署到服务器

```bash
sudo ./deploy/install.sh
```

安装向导会依次问：域名、客户端端口（默认 `58588`）、面板端口、证书方式
（Let's Encrypt / 自签 / 已有证书 / 不要 TLS）。细节见 `canoe-server/deploy/README.md`。

Web 管理面板在 `<域名>:<面板端口>/panel`，管用户、节点、会话、版本发布、中转层配置。

---

## 测试

全部通过后才算改完。改完任何一处请整套重跑。

```bash
# —— 客户端（canoe-client/）——
python tests/test_config.py     # 配置生成 + 安全断言（45 项）
python tests/test_sysproxy.py   # 系统代理与自愈（33 项）
python tests/test_tools.py      # 日志总线 / 版本 / TCping / URL 测试（31 项）
set QT_QPA_PLATFORM=offscreen
python tests/test_gui.py        # GUI 端到端，会真启航一次（73 项）

# 联调（要有一个在跑的服务端）
set CANOE_SERVER_URL=https://127.0.0.1:8443
set CANOE_CA_BUNDLE=..\canoe-server\data\certs\local-cert.pem
python tests/test_server.py     # 33 项

# —— 服务端（canoe-server/）——
python smoke_test.py https://127.0.0.1:8443 --insecure   # 97 项
cd panel && bun test_panel.mjs                            # 面板 DOM（24 项）
```

合计 **336 项**。

---

## 已知缺口

### 中转层的 ticket 目前没有被强制执行

服务端在 `/api/config` 里会签发一张短期的、绑设备的 ticket
（`ConfigResponse.token`），但中转层（sing-box 的 VLESS 入站）认的是
**节点固定的 `entry_uuid`**，不是这张 ticket：

```python
# canoe_server/services/relay.py
"users": [{"uuid": node.entry_uuid, "flow": ""}],
```

后果：知道「入口域名 + 路径 + entry_uuid」的人，即使账号已被封禁，
仍然能继续使用中转层 —— 因为这些值不会随封禁变化。ticket 现在是签了但没处验。

要真正闭环，可选（成本从低到高）：

1. **按用户分配 UUID**：中转层每个账号一个 UUID，封禁时重生成中转配置并热重载。
   能立刻让封禁生效，代价是用户变更时要 reload 一次。
2. **自建入口网关**：入口不用 sing-box，用一个能读 ticket 的网关终止 TLS，
   校验通过再转发到真实节点。最彻底，但要自己写数据面。

在决定之前，请不要把中转层入口当作「可撤销的凭据」来用。

---

## 外部依赖

| 组件 | 用途 | 获取 |
|---|---|---|
| **sing-box** | 代理内核（客户端与中转层共用） | [Releases](https://github.com/SagerNet/sing-box/releases)，本项目用 1.14.2 |
| **wintun.dll** | 仅全局(TUN)模式 | <https://www.wintun.net/> |
| Nginx + certbot | 服务端部署 | 系统包管理器 |

---

## 免责声明

本项目是代理软件的**客户端-服务端管理框架**，用于自建网络接入与流量管理。
请在法律允许的范围内使用。
