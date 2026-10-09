# 03 · 数据库表设计

> **阶段状态**：本文档描述的是**最终形态**（客户端 + 服务端 + 中转层）。
> 当前处于**阶段 1**：只有桌面端 + 一个写死的测试节点，服务端与中转层尚未启用。
> 阶段 1 的客户端确实持有真实节点，阶段 3 起会换成这里描述的中转层模式。
> 见根目录 `README.md` 的「阶段 1 的诚实说明」。

**完整可执行的建表语句见 [`canoe-server/schema.sql`](../canoe-server/schema.sql)。**

那份文件不是手写的，是由 `seed.py --schema` 从 ORM 模型直接生成的：

```bash
cd canoe-server
python seed.py --schema > schema.sql
```

所以它和代码**不可能漂移** —— 改了模型重新生成即可。下面按表解释每个字段为什么这么设计。

默认 SQLite（开箱即用）；生产建议 PostgreSQL，把 `DATABASE_URL` 换成
`postgresql+psycopg://canoe:password@host/db` 即可，模型层不用改。

---

## 1. `users` — 账号

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `username` | VARCHAR(32) UNIQUE | 登录名，`[A-Za-z0-9_-]{3,32}` |
| `password_hash` | VARCHAR(255) | `pbkdf2_sha256$迭代数$salt$hash`，**永不存明文** |
| `status` | VARCHAR(16) | `active` / `banned` |
| `role` | VARCHAR(16) | `user` / `admin` |
| `expire_at` | DATETIME NULL | 到期时间；NULL = 永不过期 |
| `max_devices` | INTEGER | 同时登录设备数上限，默认 3 |
| `remark` | VARCHAR(255) | 管理员备注 |
| `created_at` | DATETIME | |
| `last_login_at` | DATETIME NULL | |

需求里的 `created_at / expire_at / status` 全部保留，另加了 `role`（区分管理员）
和 `max_devices`（设备限制）。

---

## 2. `tokens` — 登录令牌

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | FK → users | 级联删除 |
| `token_hash` | VARCHAR(64) UNIQUE | **令牌的 SHA-256** |
| `device_id` | VARCHAR(64) | 签发时绑定的设备 |
| `revoked` | BOOLEAN | 吊销标记 |
| `expire_at` | DATETIME | |
| `created_at` | DATETIME | |

### ⚠️ 与需求建议的一处**刻意不同**

需求写的是列名 `token`，这里用 `token_hash`。

**原因**：数据库里存令牌明文，等于库一泄漏所有人就能直接登录。
存 SHA-256 后，校验时对请求里的令牌做同样的哈希再比对，功能完全一致，
但拿到库也没用。

### 为什么用不透明令牌而不是 JWT

需求里有 `tokens` 表，用它比引 JWT 更贴合，而且：

1. **JWT 是无状态的，签发后无法撤销。** "管理员点了封禁，用户还能用到过期"
   对代理工具是致命的 —— 封禁必须立即生效。
2. 每次请求多一次索引查询，这个量级可以忽略。

---

## 3. `nodes` — 节点（**真实信息只在这张表**）

一行 = 一个「真实节点 + 对应的中转层入口」的绑定。

### 3.1 展示与状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | VARCHAR(64) | **客户端唯一能看到的节点信息**，如 `香港-01` |
| `enabled` | BOOLEAN | 需求里的 `status`；停用后不生成入口、不分配 |
| `sort_order` | INTEGER | 自动分配时的优先级 |
| `remark` | VARCHAR(255) | 仅管理员可见 |

### 3.2 中转入口 `entry_*` —— **允许下发给客户端**

| 字段 | 说明 |
|---|---|
| `entry_host` | 中转层公网域名，如 `canoe.example.com` |
| `entry_port` | 默认 443 |
| `entry_uuid` | 中转层 VLESS 入站 UUID（短期凭证的主体） |
| `entry_path` | WebSocket 路径，如 `/e/hk01`，**每个节点必须唯一** |
| `entry_sni` | TLS SNI，一般等于 `entry_host` |
| `entry_transport` | `ws` / `grpc` / `tcp` |
| `entry_tls` / `entry_insecure` | 生产 `insecure` 必须 false |

### 3.3 真实节点 `real_*` —— **绝不下发**

| 字段 | 对应需求 | 说明 |
|---|---|---|
| `real_protocol` | `protocol` | `vless` / `vmess` / `trojan` / `shadowsocks` |
| `real_host` | `address` | 真实 IP / 域名 |
| `real_port` | `port` | 真实端口 |
| `real_uuid` | `secret` | 真实 UUID / 密码 |
| `real_flow` | | 如 `xtls-rprx-vision` |
| `real_tls` / `real_sni` / `real_fingerprint` | | TLS 与 uTLS 指纹 |
| `real_network` / `real_ws_path` / `real_ws_host` / `real_grpc_service` | | 传输层参数 |
| `real_insecure` | | |
| `real_extra` | | JSON，协议特有参数（如 shadowsocks 的 method） |

> **命名约定本身就是安全边界。** 代码里凡是要返回给客户端的，只允许读 `entry_*`
> 和 `name`；`real_*` 只能被 `services/relay.py`（生成中转层配置）和 `/api/admin`
> 接口读取。`services/nodes.py` 里的 `to_entry_payload()` 是唯一的出口白名单。

---

## 4. `user_node` — 用户与节点绑定（需求指定）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | FK → users | |
| `node_id` | FK → nodes | |
| `assigned_at` | DATETIME | |

唯一约束 `(user_id, node_id)`。

一个用户可以绑多个节点。没绑任何节点的用户走**自动分配**（按 `sort_order`
取第一个可用节点）—— 所以这张表可以为空，系统照样能用。

分配优先级见 `services/nodes.pick_node()`：
请求指定 → `user_node` 绑定的（按 sort_order）→ 全局第一个可用。

---

## 5. `sessions` — 会话（需求指定）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) PK | 随机 hex |
| `user_id` | FK → users | |
| `node_id` | FK → nodes | 本次会话走的节点 |
| `device_id` | VARCHAR(64) | |
| `last_seen` | DATETIME | 需求里的 `last_seen` |
| `ticket_hash` | VARCHAR(64) | 入口凭证的哈希，**不存原文** |
| `client_ip` | VARCHAR(64) | |
| `mode` | VARCHAR(16) | `system_proxy` / `tun` |
| `revoked` | BOOLEAN | |
| `created_at` / `expire_at` / `ended_at` | DATETIME | |

### ⚠️ 需求里的 `online` 字段没有做成列

需求建议 `sessions` 带一个 `online` 布尔。这里改成由 `last_seen` **推算**：

```python
@property
def online(self) -> bool:
    return not self.revoked and self.ended_at is None \
           and self.last_seen >= utcnow() - timedelta(seconds=90)
```

存布尔字段的经典坑：客户端崩溃 / 被任务管理器强杀时来不及置 `false`，
那条记录会**永远显示在线**。按时间戳推算就没有这个问题。

---

## 6. 辅助表

### `audit_logs`

`id / user_id / action / detail / ip / created_at`

记录 `register`、`login`、`login_failed`、`config_issued`、`ban`、`node_update`、
`relay_reload` 等事件。

### `config_meta`

`key / value / updated_at`，目前只存 `nodes_version`。

节点增删改时自增，客户端心跳拿到版本变化就提示重新拉配置 —— 这就是需求里
「配置变更可同步到客户端」的实现方式。

---

## 7. ER 图

```
users ──1:N── tokens
  │
  ├──1:N── user_node ──N:1── nodes        nodes.real_*  ← 真实节点只在这
  │                              │
  └──1:N── sessions ─────N:1─────┘
        │
        └──1:N── audit_logs
```

---

## 8. 迁移

开发期用 `Base.metadata.create_all()` 自动建表（`seed.py` 调用）。

正式环境请引入 Alembic：

```bash
cd canoe-server
alembic init migrations
alembic revision --autogenerate -m "add xxx"
alembic upgrade head
```

## 9. 生产建议

| 项目 | 建议 |
|---|---|
| 数据库 | PostgreSQL；SQLite 只适合单机小规模 |
| 备份 | `pg_dump` 每日 + WAL 归档 |
| 索引 | 现有索引已覆盖 `username` / `token_hash` / `status` / `last_seen`；数据量大后给 `sessions(ended_at)` 补索引 |
| 清理 | `services/sessions.purge_expired_tokens()` 可挂定时任务，清过期令牌 |
