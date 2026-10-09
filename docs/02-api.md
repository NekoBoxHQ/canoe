# 02 · API 约定

> **阶段状态**：中转层**已删除**。服务端只分发订阅、不转发流量。
> 节点 = 管理员贴的一行链接；客户的订阅 = 他绑定节点的链接拼起来。

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
| POST | `/api/login` | 登录，返回 token + 会话级订阅密钥 `sub_key` | 需求指定 |
| GET | `/api/config` | 启航：建一条会话（**不下发节点**） | 需求指定 |
| POST | `/api/heartbeat` | 心跳，保持在线 + 带回订阅指纹 | 需求指定 |
| POST | `/api/logout` | 登出（吊销令牌） | 需求指定 |
| POST | `/api/session/stop` | **本项目补充**：结束会话但保留令牌 | 见下方说明 |
| GET | `/api/me` | 当前用户信息 | 辅助 |
| GET | `/api/health` | 健康检查 | 辅助 |
| GET | `/api/client/latest` | **客户端更新**：最新版本 + 安装包地址（**不需要登录**） | 更新通道 |
| GET | `/api/subscription` | **订阅**：加密的订阅载荷 + 变更指纹 | 更新通道 |
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
  "sub_key": "9f3Ka...==",
  "user": {
    "id": 12,
    "username": "canoe",
    "status": "active",
    "expire_at": 1799000000,
    "node_name": null
  }
}
```

`sub_key` 是**会话级订阅密钥**（32 字节随机数的 base64）：
客户端拿它解密 `/api/subscription` 的响应。它和令牌同生命周期 ——
令牌一吊销，服务端就再也取不到这把钥匙，客户端也就拉不到能解开的内容了。

客户端**只把它放在内存里**，不落盘、不写配置文件。

> `user.node_name` 是上一版模型（服务端分配节点）的遗留字段，
> 现在恒为 `null`；节点名由客户端从解密出来的订阅里解析。

`403 banned` / `403 expired` / `409 device_limit` 时客户端应停在登舟页并提示。

---

## 3. GET /api/config  ★ 启航时建会话

```
GET /api/config?device_id=3f9c...&mode=system_proxy
Authorization: Bearer <token>
```

服务端在这里做全套校验：令牌有效 → 未封禁 → 未过期 → 设备数未超限，
然后记一条会话（管理端靠它看谁在线、靠它踢下线）。

**这个接口不下发任何节点信息** —— 节点从 `/api/subscription` 的加密信封来。

**200**

```json
{
  "protocol": 1,
  "session_id": "7d2a...b1",
  "node_name": null,
  "expires_at": 1790000300,
  "heartbeat_interval": 30,
  "revision": "fca8e285fa5cbc53"
}
```

### 这个响应里**没有**什么

没有 `entry`、没有 `token`、没有 `address`/`port`/`protocol`/`secret`、
没有任何一行节点链接。节点一律走订阅那条加密通道。

`revision` 是订阅指纹（明文，只取哈希，不含任何订阅内容片段）——
客户端拿它和手里的比一比就知道要不要重新拉订阅。

---

## 3.1 历史：`entry` 与 `token`（已删除）

早先版本在这里下发过 `entry`（中转层入口的参数白名单）和 `token`
（绑定 `user_id + node_id + device_id` 的短期入口凭证，默认 300 秒有效）。
中转层删掉之后客户端不再需要它们，这两个字段、相关的类型，
以及 `/api/admin/relay/*` 两个端点都已一并移除。

`403 banned/expired` 表示账号级拒绝。**不再有 `503 no_node`** ——
服务端不替客户端挑节点，"没节点可用"是客户端从空订阅里自己看出来的。

---

## 4. POST /api/heartbeat

```json
{ "session_id": "7d2a...b1" }
```

**200**

```json
{ "ok": true, "expires_at": 1790000600, "revision": "fca8e285fa5cbc53", "revoked": false }
```

客户端行为：

- `revoked=true` 或 `403` → **立即靠岸**（管理员封禁/踢下线）
- `revision` 变化 → 重新拉 `/api/subscription`；解出来是空就销毁本地订阅

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
| PATCH | `/api/admin/users/{id}` | 改用户名 / 密码 / 到期 / 设备上限 / 备注 / 角色 |
| POST | `/api/admin/users/{id}/ban` | 封禁 → 立即吊销令牌与会话 |
| POST | `/api/admin/users/{id}/unban` | 解封 |
| DELETE | `/api/admin/users/{id}` | 删号 |

### 改账号

```http
PATCH /api/admin/users/12
{ "username": "新名字", "password": "新密码", "remark": "备注" }
```

字段全部可选，只改传上来的那几项。**用户名是可以改的**，包括管理员把自己
改名 —— 令牌按 `user_id` 记，改完不用重新登录，只是 `/api/me` 里显示新名字。

- 改名会查重：撞上已有的名字返回 `409 username_taken`（不是静默变成两个同名账号）
- 格式 `3-32` 位字母、数字、下划线或减号，不合规 `422`
- 改名 / 改密码 / 改角色都写审计（`audit_logs.detail` 里是「用户名 A → B」）

服务端上还有个不用开面板的入口：`sudo canoe passwd`，或者菜单里
「6 Canoe 配置 → 3 改管理员账号」—— 改的就是管理员自己的用户名和密码。

### 节点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/nodes` | 节点列表 |
| POST | `/api/admin/nodes` | 建节点（贴一行链接 + 备注） |
| PATCH | `/api/admin/nodes/{id}` | 改节点 |
| DELETE | `/api/admin/nodes/{id}` | 删节点 |
| PUT | `/api/admin/users/{id}/nodes` | **把这个客户绑定的节点整体换掉** |
| GET | `/api/admin/sessions?online=true` | 在线会话 |
| DELETE | `/api/admin/sessions/{id}` | 踢下线 |
| GET | `/api/admin/stats` | 统计 |

### 节点对象

管理员看到的节点。`link` 是原文（**只对管理员可见**），
`protocol`/`host`/`port` 是服务端替前端解析出来的展示信息：

```json
{
  "id": 3,
  "name": "日本-家宽",
  "remark": "买的那个 100M 的",
  "link": "ss://2022-blake3-aes-128-gcm:AAAA:BBBB@one.example.com:33222#日本",
  "protocol": "shadowsocks",
  "host": "one.example.com",
  "port": 33222,
  "valid": true,
  "enabled": true,
  "sort_order": 10,
  "created_at": 1790000000,
  "updated_at": 1790000000
}
```

`valid=false` 表示这行链接解析不了（管理员可能粘了一半）——
存是存下来了，但发给客户端的订阅里不会有它。

### 绑定

```http
PUT /api/admin/users/12/nodes
{ "node_ids": [3, 5] }
```

**整体替换**（不是追加）：传什么就是什么，传 `[]` 就是解绑全部 ——
解绑后这个客户拉到的是**空信封**，客户端就地销毁本地订阅。

绑定 / 改节点都会向该账号**定向推送** `config_changed`
（`hub.publish(..., user_id=...)`），在航的客户端当场重新拉订阅；
最迟下一次心跳也会靠 `revision` 发现。

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
  "node_name": null,
  "expires_at": 1799000000,
  "heartbeat_interval": 30,
  "revision": "3f9c1e0a2b7d4e51",
  "envelope": {
    "alg": "AES-256-GCM",
    "salt": "9f3K...",
    "nonce": "Q2x...",
    "data": "b8FI...",
    "revision": "3f9c1e0a2b7d4e51"
  }
}
```

`revision` 是**订阅指纹**：订阅内容 / 账号状态 / 到期时间任一变化都会让它变。
客户端存住上一轮的字符串比一比即可：

```
revision 没变 -> 订阅已是最新
revision 变了 -> 重新拉一次并解密（内容可能是空的 = 服务端停止分发了）
```

> ⚠️ 指纹本身是**明文**发给客户端的。所以它只取哈希，绝不把订阅内容的
> 任何片段拼进去 —— 否则等于绕开加密把内容泄出去。这条由
> `services/updates.subscription_revision()` 保证，冒烟测试里有对应断言。

### 内容为什么是密文

`envelope.data` 是 AES-256-GCM 密文，密钥由**登录时下发的 `sub_key`**
（每个会话一把）经 HKDF-SHA256 派生。信封里带了随机 `salt` 和 `nonce`，
所以同一条订阅两次拉取密文不同。

服务端在三种情况下回**空信封**（`data` 为空）：
账号被封、账号到期、管理员把这个客户绑定的节点全取消了。
客户端见到空信封必须**销毁本地订阅** —— 这是"服务端完全可控"的落点。

---

## 8.7 GET /api/events —— 推送（SSE）

需要登录。`Authorization: Bearer <token>` 走请求头（客户端是桌面程序，能设头）。

一条 `text/event-stream` 长连接，事件体是 JSON：

```
data: {"type":"hello","revision":"3f9c...","server_time":1791519900}

data: {"type":"config_changed","config_version":0,"revision":"fca8..."}

data: {"type":"release","version":"1.1.0","url":"...","notes":"..."}

data: {"type":"kick","user_id":12,"reason":"账号已被封禁"}
```

| 事件 | 客户端该做什么 |
|---|---|
| `hello` | 连上就发。对齐 `revision` |
| `config_changed` | 重新拉 `/api/subscription` 并解密；**是空的就销毁本地订阅** |
| `release` | 结果框提示有新版本 |
| `kick` | **立即靠岸**，不用等心跳 |
| `ping` | 保活注释，忽略 |

约定：

- `config_changed` 是**广播**，所以**不带订阅内容**（带了就是把一个人的订阅
  发给所有人）。客户端收到后自己去拉自己那份。
- 管理员改某一账号的绑定时，走的是**定向推送**（`hub.publish(..., user_id=...)`），
  不会把无关的人叫醒。
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
2. **`nodes.link`（那行节点链接）只允许出现在两个地方**：加密订阅的信封里，
   和 `/api/admin/nodes`（需 admin）。每个明文响应出网前都过一遍
   `canoe_core.assert_no_leaks()` —— 扫到一行链接直接 500。
3. **订阅内容只以密文出网**：`/api/subscription` 回的是 `envelope`，
   明文只能被登录时下发的 `sub_key` 解开。往这个响应里加任何明文节点
   字段都是破坏这条约定。
4. 订阅指纹 `revision` 是明文，**只取哈希**，不能拼入订阅内容的片段。
5. 生产必须 HTTPS + HSTS。客户端的 TLS 校验默认是**开的**；
   联调自签证书要用 `CANOE_CA_BUNDLE` 指一张 CA，**不要**关校验。
6. 登录接口建议加频率限制（每 IP 10 次/分钟）。
