# 02 · API 约定

> **阶段状态**：本文描述的中转层模式**已经落地**（阶段 1-5 完成）。
> 客户端只拿中转入口，真实节点只在服务端；中转层配置由
> `canoe_server/services/relay.py` 从数据库渲染。

- **Base URL**：`https://<your-domain>`（生产必须 HTTPS；本地调试 `http://127.0.0.1:8000`）
- **编码**：`application/json; charset=utf-8`
- **鉴权**：除 `/api/register`、`/api/login`、`/api/health` 外，全部需要
  `Authorization: Bearer <token>`
- **时间**：全部是 **Unix 秒级时间戳（UTC）**
- **路径常量**：唯一来源是 `canoe_core.Api`，客户端和服务端都从这里取

---

## 0. 端点总览

| 方法 | 路径 | 说明 | 需求来源 |
|---|---|---|---|
| POST | `/api/register` | 注册 | 需求指定 |
| POST | `/api/login` | 登录，返回 token | 需求指定 |
| GET | `/api/config` | 拉取配置（入口 + token + 节点名） | 需求指定 |
| POST | `/api/heartbeat` | 心跳，保持在线 | 需求指定 |
| POST | `/api/logout` | 登出（吊销令牌） | 需求指定 |
| POST | `/api/session/stop` | **本项目补充**：结束会话但保留令牌 | 见下方说明 |
| GET | `/api/me` | 当前用户信息 | 辅助 |
| GET | `/api/health` | 健康检查 | 辅助 |
| GET | `/api/client/latest` | **客户端更新**：最新版本 + 安装包地址（**不需要登录**） | 更新通道 |
| GET | `/api/subscription` | **订阅更新**：当前订阅 + 变更指纹 | 更新通道 |
| GET | `/api/events` | **推送**：SSE 长连接 | 推送 |
| — | `/api/admin/*` | 管理后台 | 辅助 |
| — | `/downloads/*` | 安装包静态下载 | 更新通道 |

> **关于 `/api/session/stop`**
> 需求里只有 5 个端点。但「靠岸」和「登出」是两件事：靠岸只结束这次代理会话，
> 登出才吊销登录令牌。如果靠岸也走 `/api/logout`，用户每次靠岸都得重新登舟。
> 所以补了这一个端点。不想要的话，把 `main_view._dock` 里的
> `api.stop_session(...)` 删掉即可（服务端会在 90 秒后按心跳超时判定离线）。

### 统一错误格式

```json
{ "detail": { "code": "banned", "detail": "账号已被封禁" } }
```

| HTTP | 含义 |
|---|---|
| 400 | 参数错误 |
| 401 | 未登录 / 令牌失效 → 客户端回登舟页 |
| 403 | 无权限 / 被封禁 / 已到期 / 设备被禁 |
| 404 | 资源不存在 |
| 409 | 冲突（用户名已存在、设备数超限） |
| 422 | 请求体校验失败（FastAPI 默认） |
| 429 | 频率限制 |
| 503 | 当前没有可用节点 |

错误码常量见 `canoe_core.ErrorCode`。

---

## 1. POST /api/register

```json
{ "username": "canoe", "password": "canoe-pass-123" }
```

约束：`username` 3–32 位 `[A-Za-z0-9_-]`；`password` 8–128 位。

**201**

```json
{ "id": 12, "username": "canoe", "created_at": 1790000000 }
```

`409 username_taken` / `422` 校验失败。

---

## 2. POST /api/login

```json
{
  "username": "canoe",
  "password": "canoe-pass-123",
  "device_id": "3f9c1e0a-....",
  "device_name": "DESKTOP-ABC"
}
```

`device_id` 由客户端首次运行生成并持久化在本机。

**200**

```json
{
  "token": "Xk7f...Q2",
  "expires_in": 86400,
  "user": {
    "id": 12,
    "username": "canoe",
    "status": "active",
    "expire_at": 1799000000,
    "node_name": "香港-01"
  }
}
```

`node_name` 在登录时就会返回 —— 主界面的「节点名称」要在点启航之前就显示出来，
不能等启航才知道连哪个。

`403 banned` / `403 expired` / `409 device_limit` 时客户端应停在登舟页并提示。

---

## 3. GET /api/config  ★ 核心端点

```
GET /api/config?device_id=3f9c...&mode=system_proxy
Authorization: Bearer <token>
```

服务端在这里做全套校验：令牌有效 → 未封禁 → 未过期 → 设备数未超限 → 选出节点。

**200**

```json
{
  "protocol": 1,
  "session_id": "7d2a...b1",
  "node_name": "香港-01",
  "token": "eyJ1IjoxMiwibiI6MywiZCI6ImFiYyIsImV4cCI6MTc5MDAwMDMwMH0.9mQ...",
  "expires_at": 1790000300,
  "heartbeat_interval": 30,
  "config_version": 4,
  "entry": {
    "transport": "ws",
    "host": "canoe.example.com",
    "port": 443,
    "uuid": "b8f1c2d4-....",
    "path": "/e/hk01",
    "sni": "canoe.example.com",
    "tls": true,
    "insecure": false
  }
}
```

### 这个响应里**没有**什么

没有 `address`、没有 `port`(真实端口)、没有 `protocol`(真实协议)、
没有 `secret`、没有 `real_*`。需求的原文是：

> /api/config 返回里不能包含真实节点地址/端口/密码，
> 只返回服务端入口 + token + 节点显示名。

这条要求由 `canoe_core.ConfigResponse` 的**类型定义**保证，而不是靠写代码时的自觉。
详见 `docs/04-security.md`。

响应体里 `token` 是**短期入口凭证**（默认 300 秒有效），绑定 `user_id + node_id + device_id`，
服务端可随时吊销。它和中转层入口一起构成本次会话的凭据。

`503 no_node` 表示没有可用节点；`403 banned/expired` 表示账号级拒绝。

---

## 4. POST /api/heartbeat

```json
{ "session_id": "7d2a...b1" }
```

**200**

```json
{ "ok": true, "expires_at": 1790000600, "config_version": 4, "revoked": false, "node_name": "香港-01" }
```

客户端行为：

- `revoked=true` 或 `403` → **立即靠岸**（管理员封禁/踢下线）
- `config_version` 变化 → 提示「配置已更新，请重新启航」
- `node_name` 变化 → 更新主界面显示的节点名

心跳同时是"续期"：服务端每次都把会话的 `last_seen` 和 `expire_at` 往后推。

---

## 5. POST /api/session/stop

```json
{ "session_id": "7d2a...b1" }
```

**200** `{ "ok": true }`

只结束这次代理会话（写 `sessions.ended_at`），**不动登录令牌**。
幂等：会话不存在也返回 ok。

---

## 6. POST /api/logout

```json
{ "session_id": "7d2a...b1", "all_devices": false }
```

吊销**登录令牌**。`all_devices=true` 时吊销该用户全部令牌并结束所有会话。

幂等：无令牌 / 令牌已失效都返回 `{ "ok": true }`，避免客户端卡在登出流程里。

---

## 7. GET /api/me

```json
{
  "id": 12, "username": "canoe", "status": "active",
  "expire_at": 1799000000, "node_name": "香港-01",
  "devices": [{ "device_id": "3f9c...", "name": "", "last_seen_at": 1790000000 }]
}
```

---

## 8. 管理后台 `/api/admin/*`（需要 role=admin）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/users?page=1&size=50&q=` | 用户列表（含在线状态） |
| POST | `/api/admin/users` | 建号 |
| PATCH | `/api/admin/users/{id}` | 改密码 / 到期 / 设备上限 / 备注 / 角色 |
| POST | `/api/admin/users/{id}/ban` | 封禁 → 立即吊销令牌与会话 |
| POST | `/api/admin/users/{id}/unban` | 解封 |
| DELETE | `/api/admin/users/{id}` | 删号 |
| GET | `/api/admin/nodes` | 节点列表（**含真实节点字段**） |
| POST | `/api/admin/nodes` | 建节点 |
| PATCH | `/api/admin/nodes/{id}` | 改节点 |
| DELETE | `/api/admin/nodes/{id}` | 删节点 |
| POST | `/api/admin/nodes/{id}/bind` | 把节点绑定给一批用户（写 `user_node`） |
| GET | `/api/admin/sessions?online=true` | 在线会话 |
| DELETE | `/api/admin/sessions/{id}` | 踢下线 |
| GET | `/api/admin/stats` | 统计 |
| GET | `/api/admin/relay/config?fmt=singbox\|nginx` | 渲染中转层配置 |
| POST | `/api/admin/relay/reload` | 重新渲染并执行 reload hook |

节点对象（含真实节点，**只对管理员可见**）：

```json
{
  "id": 3,
  "name": "香港-01",
  "enabled": true,
  "sort_order": 10,
  "entry": { "transport": "ws", "host": "canoe.example.com", "port": 443,
             "uuid": "b8f1...", "path": "/e/hk01", "sni": "canoe.example.com",
             "tls": true, "insecure": false },
  "real":  { "protocol": "vless", "host": "203.0.113.7", "port": 8443,
             "uuid": "9a7e...", "flow": "xtls-rprx-vision", "tls": true,
             "sni": "real.example.com", "fingerprint": "chrome",
             "network": "tcp", "ws_path": "", "ws_host": "",
             "grpc_service": "", "insecure": false, "extra": {} }
}
```

---

## 8.5 GET /api/client/latest —— 客户端更新

**不需要登录。** 客户端得先能检查更新，才谈得上登舟 —— 所以这个响应里
只有版本号和安装包元信息，没有半点与账号相关的东西。

**200**

```json
{
  "version": "1.1.0",
  "url": "https://api.canoe.example.com/downloads/Canoe-1.1.0-win64.zip",
  "notes": "修复 TUN 快速重连卡顿",
  "published_at": 1790000000,
  "size": 83276159,
  "sha256": "9f2c...ab",
  "min_version": "1.0.0"
}
```

- `url` 由服务端自托管（挂在 `/downloads/` 上），文件名来自管理端上传时的原始文件名。
- `sha256` 供客户端校验下载完整性。
- `min_version` 非空且当前版本低于它时，客户端应引导**强制**升级。
- 没发布过任何版本时返回 `404 not_found`。

管理端发布：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/releases` | 版本列表 + 当前最新 |
| POST | `/api/admin/releases` | 只登记版本（包自己放进 `releases/`） |
| POST | `/api/admin/releases/upload` | multipart 上传安装包并发布 |
| GET | `/api/admin/releases/latest-preview` | 预览客户端会拿到什么 |
| DELETE | `/api/admin/releases/{id}?delete_file=true` | 撤版本 |

---

## 8.6 GET /api/subscription —— 订阅更新

需要登录。**不建会话、不发凭证**，只读 —— 客户端可以在没启航的时候随手调。

**200**

```json
{
  "protocol": 1,
  "config_version": 7,
  "node_name": "香港-01",
  "entry": { "transport": "ws", "host": "canoe.example.com", "port": 443,
             "uuid": "b8f1...", "path": "/e/hk01", "sni": "canoe.example.com",
             "tls": true, "insecure": false },
  "expires_at": 1799000000,
  "heartbeat_interval": 30,
  "revision": "3f9c1e0a2b7d4e51"
}
```

`revision` 是**订阅指纹**：`config_version`、节点、入口域名/端口/UUID/路径/SNI
任一变化都会让它变。客户端存住上一轮的字符串比一比即可：

```
revision 没变 -> 订阅已是最新
revision 变了 -> 订阅有更新，提示"配置已更新，请重新启航"
```

和 `/api/config` 一样，`entry` 走 `EntryPayload` 白名单，没有真实节点字段。

---

## 8.7 GET /api/events —— 推送（SSE）

需要登录。`Authorization: Bearer <token>` 走请求头（客户端是桌面程序，能设头）。

一条 `text/event-stream` 长连接，事件体是 JSON：

```
data: {"type":"hello","config_version":7,"revision":"3f9c...","node_name":"香港-01"}

data: {"type":"config_changed","config_version":8,"revision":"","node_name":""}

data: {"type":"release","version":"1.1.0","url":"...","notes":"..."}

data: {"type":"kick","user_id":12,"reason":"账号已被封禁"}
```

| 事件 | 客户端该做什么 |
|---|---|
| `hello` | 连上就发。对齐 `config_version` / `revision` |
| `config_changed` | 提示"配置已更新，请重新启航"，或自动重拉 `/api/subscription` |
| `release` | 结果框提示有新版本 |
| `kick` | **立即靠岸**，不用等心跳 |
| `ping` | 保活注释，忽略 |

约定：

- `config_changed` **不带节点名** —— 每个用户分配到的节点不同，带上就把别人的
  节点泄露给所有人了。客户端收到后自己去拉 `/api/subscription`。
- 断线要自动重连（指数退避）。重连后先靠 `hello` 对齐，漏掉的事件不必补。
- 服务端有连接数上限（默认全局 2000 / 单账号 5），超了返回 `429`。
- 空闲时服务端每 20 秒发一个注释帧保活，中间的反代会掐掉沉默连接。

> **服务端只能开 1 个 worker** —— 推送中心是进程内的内存结构，多 worker 时
> 管理端的广播只在它自己那个进程里扩散。见 `canoe-server/deploy/README.md`。

---

## 9. 客户端状态机

```
        造舟成功
          │
          ▼
   ┌──────────┐  登舟成功  ┌────────────┐  点启航  ┌──────────┐
   │ 登舟页   │ ────────► │  主界面     │ ──────► │ 已启航   │
   │ (含造舟) │           │ 显示节点名  │ ◄────── │ 渡江中…  │
   └──────────┘           └────────────┘  点靠岸  └──────────┘
         ▲                      │                    │
         │ 401 / banned         │ 风浪太大，请重试    │ 心跳 revoked
         └──────────────────────┴────────────────────┘
```

界面四态文案（`canoe_core.Text`）：

| 状态 | 文案 |
|---|---|
| 连接中 | 渡江中… |
| 连接成功 | 已启航 |
| 连接断开 | 已靠岸 |
| 错误 | 风浪太大，请重试 |

---

## 10. 安全约定（实现层）

1. `/api/admin/*` 必须二次校验 `role == "admin"`，不能只靠前端隐藏入口。
2. `real_*` 只出现在 `/api/admin/nodes` 的响应里。客户端可见的序列化一律走
   `services/nodes.to_entry_payload()`，**不要用 `model_dump()` 全量序列化**。
3. 生产必须 HTTPS + HSTS；`entry.insecure` 保持 `false`。
4. 登录接口建议加频率限制（每 IP 10 次/分钟）。
