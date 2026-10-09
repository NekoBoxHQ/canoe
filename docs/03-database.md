# 03 · 数据库表设计

> **阶段状态**：中转层**已删除**。服务端只分发订阅、不转发流量；
> 节点就是管理员贴的一行链接，客户的订阅 = 他绑定节点的链接拼起来。

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

## 3. `nodes` — 节点（**核心就是一行链接**）

一行 = 一个节点。字段少到一句话说得完：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `link` | TEXT | ★ **节点链接原文**，如 `ss://…@host:port#名称` |
| `name` | VARCHAR(64) | 显示名（面板用；不指定就取链接里 `#` 后面那段） |
| `remark` | VARCHAR(255) | 管理员给自己看的备注，**不进订阅** |
| `enabled` | BOOLEAN | 需求里的 `status`；停用后不进订阅 |
| `sort_order` | INTEGER | 订阅正文里的排列顺序 |
| `created_at` / `updated_at` | DATETIME | |

链接里的协议、主机、端口、密钥、名称全都是现成的 —— 所以这里**不需要**
`entry_*` / `real_*` 那一堆字段（那是中转层时代服务端要自己渲染
sing-box 配置才需要的，现在服务端不碰流量，一个字段都不用填）。

面板列表里显示的协议 / 主机 / 端口是服务端**临时解析**出来的
（`services/nodes.link_summary()`），不入库。

> **`link` 是机密。** 它只允许出现在两个地方：加密订阅的信封里，
> 和 `/api/admin/nodes`（需 admin）。别的明文响应一律不许有 ——
> 每个响应出网前过一遍 `canoe_core.assert_no_leaks()`。

---

## 4. `user_nodes` — 客户与节点的绑定（需求指定）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | FK → users | |
| `node_id` | FK → nodes | |
| `assigned_at` | DATETIME | |

唯一约束 `(user_id, node_id)`。

一个客户可以绑多个节点，他的订阅就是这些节点的 `link` 按 `sort_order`
拼起来。**没有"自动分配"这种兜底了** —— 那会让"我没给他配节点"和
"配了但都停用了"变得无法区分，而这两种情况下服务端给出的订阅都该是空的。

绑定是**整体替换**的（`services/nodes.set_bound_nodes()`）：面板提交什么
就是什么，提交空列表 = 解绑全部。

---

## 5. `sessions` — 会话（需求指定）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) PK | 随机 hex |
| `user_id` | FK → users | |
| `node_id` | FK → nodes NULL | **恒为 NULL** —— 服务端不分配节点，留着只为兼容老库 |
| `device_id` | VARCHAR(64) | |
| `last_seen` | DATETIME | 需求里的 `last_seen` |
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

记录 `register`、`login`、`login_failed`、`config_issued`、`ban`、
`node_update`、`user_bind_nodes` 等事件。

### `config_meta`

`key / value / updated_at`。目前由 `revision`（订阅指纹）承担变更检测，
这张表留着备用。

---

## 7. ER 图

```
users ──1:N── tokens
  │
  ├──1:N── user_nodes ──N:1── nodes        nodes.link  ← 那行链接只在这
  │
  ├──1:N── sessions                        （sessions.node_id 恒为 NULL）
  │
  └──1:N── audit_logs
```

---

## 8. 迁移

开发期用 `Base.metadata.create_all()` 自动建表（`seed.py` 调用），
外加 `database.py` 里两段轻量迁移：

- `_ensure_columns()` —— 给老库补上后加的列（有了就跳过）；
- `_drop_stale_tables()` —— 老库里有些表**整张都不作数了**：SQLite 删不掉列，
  而 `nodes.entry_host` 那种字段还挂着 NOT NULL，新代码 INSERT 时必炸。
  检测到这些遗留列就把整张表重建（`sessions` 是易失数据，重建无妨）。

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
