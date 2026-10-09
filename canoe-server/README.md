# canoe-server

轻舟的服务端：用户系统、节点管理、配置下发、心跳、**更新通道**与**推送**。

> 阶段状态：**阶段 3 已完成**（在原有草稿上按新要求扩展），阶段 4 的本地
> HTTPS 部署已跑通，部署材料在 [`deploy/`](deploy/)。服务端与客户端的
> 正式联调是阶段 5。

---

## 快速开始（本地）

```bash
python3 -m venv .venv
.venv/bin/pip install -e ../canoe-core -r requirements.txt
cp .env.example .env          # 至少改掉 TICKET_SECRET
.venv/bin/python seed.py      # 建库 + 管理员 + 示例节点 + 测试用户

.venv/bin/python serve.py               # 按 .env 里的 PORT / TLS_CERT 起
.venv/bin/python serve.py --port 8000 --no-tls    # 临时覆盖
```

默认端口 **58588**（客户端固定拿它取更新和订阅）。`.env` 里同时填了
`TLS_CERT` 和 `TLS_KEY` 就直接跑 HTTPS，不需要 Nginx。

管理面板在 <https://127.0.0.1:58588/panel>（自签证书浏览器会警告）。

`run_local_https.py` 仍然可用 —— 它会自动生成一张自签证书，
方便本地让客户端真的连上来（客户端只在 HTTPS 下工作）。

接口文档：<http://127.0.0.1:8000/docs>（生产记得在 Nginx 上关掉）。

---

## 端点

| 方法 | 路径 | 说明 | 登录 |
|---|---|---|---|
| POST | `/api/register` | 注册 | 否 |
| POST | `/api/login` | 登录，返回 token + 会话级订阅密钥 `sub_key` | 否 |
| GET | `/api/config` | ★ 启航：建会话（不下发节点） | 是 |
| POST | `/api/heartbeat` | 心跳 / 续期（带回订阅指纹） | 是 |
| POST | `/api/session/stop` | 靠岸（保留登录令牌） | 是 |
| POST | `/api/logout` | 登出（吊销令牌） | 是 |
| GET | `/api/me` | 当前用户 | 是 |
| GET | `/api/health` | 健康检查 | 否 |
| **GET** | **`/api/client/latest`** | **客户端更新**：最新版本 + 安装包地址 | **否** |
| **GET** | **`/api/subscription`** | **订阅**：**加密的**订阅载荷 + 变更指纹 | **是** |
| **GET** | **`/api/events`** | **推送**：SSE 长连接 | **是** |
| — | `/api/admin/*` | 管理端（用户 / 节点 / 会话 / 统计 / 中转层 / 发布） | admin |
| — | `/downloads/*` | 安装包静态下载 | 否 |
| — | `/panel` | **Web 管理面板**（纯静态前端） | 页面本身不需要，API 需要 |
| — | `/` | 跳转到 `/panel` | 否 |

完整约定见 [`../docs/02-api.md`](../docs/02-api.md)。

---

## Web 管理面板

`/panel` 是一套**无构建步骤**的原生 HTML + CSS + JS，和 API 共用同一个端口。

- **登录**复用 `/api/login`，拿到的是同一个管理员令牌；`role != admin` 会被拒。
- 六个页签：概览 / 用户 / 节点 / 会话 / 发布 / 中转层。
- **不引任何外部 CDN** —— 一个代理服务的后台不该在打开时去 ping 第三方，
  何况离线/内网环境也得能用。所有资源都在 `panel/` 里。
- 令牌放 `sessionStorage`（关标签页即失效），不放 cookie，省掉 CSRF 面。

> ⚠ 面板只是前端。**权限完全由服务端 `/api/admin/*` 二次校验**，
> 面板文件本身是不需要登录就能下载的 —— 把 HTML 藏起来不算防护。

改面板：`panel/` 下直接改，刷新页面即可，**不需要重新打包或重启服务**
（StaticFiles 每次读盘；生产环境浏览器可能有缓存，Ctrl+F5 一下）。

面板的 DOM 冒烟测试（登录 + 六个页签渲染 + 弹窗构造，24 项）：

```bash
cd panel
bun add -d linkedom      # 只为这条测试装个 JS 运行时
bun run test_panel.mjs
```

### 客户端「更新」按钮对接的就是这两条

```
更新按钮
  ├─ GET /api/client/latest   客户端更新：有没有新版本的 Canoe.exe
  └─ GET /api/subscription    订阅：我这条订阅变了没有（内容是密文）
```

`/api/subscription` **不建会话、不发凭证**，只读，返回：

- `revision` —— 明文指纹，客户端拿它判断要不要重新拉；
- `envelope` —— **密文**，只有登录时下发的那把 `sub_key` 解得开。

### 订阅分发（阶段 6 起的模型）

管理员在面板的**用户 → 订阅**栏里逐账号贴节点链接
（`ss:// vmess:// vless:// trojan://`，一行一个，也可以整体 base64）；
你的节点服务器和 Canoe 服务端**互不关联**，这边只管"发不发"。

服务端在三种情况下回**空信封**（客户端收到就地销毁本地订阅）：

| 情况 | 怎么做 |
|---|---|
| 账号被封 | 面板点「封禁」，或 `POST /api/admin/users/{id}/ban` |
| 账号到期 | 面板把到期日改到过去 |
| 主动停发 | 把订阅栏清空并保存（会定向推送给该账号，在航的当场靠岸） |

加密用的是 `canoe_core/crypto.py`：HKDF-SHA256 派生 + AES-256-GCM，
每次响应随机 `salt`/`nonce`，`alg`/`revision` 作为 AAD 参与认证。

> ⚠️ 这一层防的是传输链路上的中间环节（反向代理、日志），
> **不防拿到客户端的用户** —— 客户端必须能解密。真正决定"能不能用"的
> 是服务端随时可以不发。

---

## 推送（SSE）

客户端启航后挂着 `GET /api/events`，服务端主动推事件：

| 事件 | 触发时机 | 客户端该做什么 |
|---|---|---|
| `hello` | 一连上就发 | 对齐 `config_version` / `revision` |
| `config_changed` | 节点/绑定变更 | 提示"配置已更新，请重新启航"（或自动刷新订阅） |
| `release` | 发布了新客户端版本 | 结果框提示有新版本 |
| `kick` | 管理员踢下线 / 封禁 | **立即靠岸** |
| `ping` | 空闲保活 | 忽略 |

> ⚠ **只能开 1 个 worker。** 推送中心是进程内的（`services/broadcast.py`），
> 多 worker 时管理端的广播只在它自己那个进程里扩散。详见
> [`deploy/README.md`](deploy/README.md) 开头。

---

## 安全约定（改代码前必读）

这一节是整个项目的安全底线，**不是可选的**：

1. **`/api/config` 与 `/api/subscription` 只能回 `EntryPayload`。**
   序列化一律走 `services/nodes.to_entry_payload()`，
   **绝不要用 `model_dump()` 全量序列化节点** —— 那样新加的 `real_*` 字段会顺带泄漏。
2. 每个下发响应出网前都会调 `assert_no_real_fields()` / `assert_whitelisted()` 自检，
   越界直接 500 而不是静默泄漏。新加下发接口时照做。
3. `real_*` 只允许出现在 `/api/admin/nodes`，且需要 `role=admin`。
4. 生产必须 HTTPS + HSTS；`entry.insecure` 保持 false。
5. `/api/admin/*` 二次校验角色，不能只靠前端隐藏入口。

详见 [`../docs/04-security.md`](../docs/04-security.md)。

---

## 测试

```bash
.venv/bin/python run.py &                  # 或 run_local_https.py
.venv/bin/python smoke_test.py http://127.0.0.1:8000
.venv/bin/python smoke_test.py https://127.0.0.1:8443 --insecure   # 自签证书
```

**85 项**，覆盖：注册 → 登录 → `/api/config`（核心安全断言）→ 心跳 →
封禁踢下线 → 登出 → 中转层配置 → 客户端更新 → 订阅更新 → SSE 推送 → 鉴权。

其中一批断言专门盯着"客户端可见的响应里绝不出现 `real_*`"。

---

## 目录

```
canoe-server/
├── canoe_server/
│   ├── app.py              FastAPI 入口（含推送事件循环绑定、静态挂载）
│   ├── config.py           配置（.env 覆盖）
│   ├── models.py           ORM：users/tokens/nodes/user_node/sessions
│   │                              + audit_logs/config_meta/client_releases
│   ├── security.py         密码哈希、令牌、入口凭证
│   ├── deps.py             鉴权（authenticate 可被长连接复用）
│   ├── routers/            auth.py / client.py / admin.py
│   └── services/
│       ├── nodes.py        入口白名单序列化 ★安全核心
│       ├── sessions.py     会话与心跳
│       ├── relay.py        中转层配置渲染
│       ├── updates.py      ★客户端更新 + 订阅更新
│       └── broadcast.py    ★SSE 推送中心
├── panel/                  ★Web 管理面板（纯静态，无构建步骤）
│   ├── index.html / app.js / style.css
│   ├── logo.png
│   └── test_panel.mjs      面板的 DOM 冒烟测试（需 bun + linkedom）
├── deploy/                 ★部署材料（systemd / Nginx / 一键脚本）
├── relay/                  中转层部署材料
├── releases/               上传的客户端安装包（不进仓库）
├── data/                   SQLite 与证书（不进仓库）
├── serve.py                ★统一启动器（读 .env 决定端口与 TLS）
├── seed.py                 建库 + 种子数据
├── smoke_test.py           端到端冒烟测试（96 项）
└── run_local_https.py      HTTPS 启动（自签证书，本地联调用）
```

---

## 与客户端的接线点（阶段 5）

客户端接线情况（阶段 5 已完成）：

| 客户端位置 | 现在 |
|---|---|
| 账号 | 走 `api.login()` / `api.register()`，本地不落账号 |
| 出站 | 用 `api.fetch_config()` 的 `entry` 经 `entry.build_entry_outbound()` 拼 |
| 更新 / 订阅 | 「更新」按钮同时查 `api.latest_release()` 与 `api.subscription()` |
| 推送 | 登录后挂 `events.stream`，收 `config_changed` / `release` / `kick` |
| 心跳 | 启航期间按 `heartbeat_interval` 打 `/api/heartbeat`，被吊销即自动靠岸 |

见 `canoe-client/README.md` 与根目录 `README.md` 的「架构」一节。
