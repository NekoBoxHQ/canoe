# canoe-server

**状态：阶段 3 才做。当前目录里有一份上一轮写的草稿，尚未按本次阶段计划review。**

按你的阶段计划：

| 阶段 | 内容 |
|---|---|
| 1 | 框架 + 测试节点 + 桌面端 ✅ 已完成 |
| 2 | 推送到 git |
| **3** | **服务端管理端（本目录）** |
| 4 | 部署服务端管理界面 |
| 5 | 客户端与服务端联调 |

---

## 目录里现在有什么

```
canoe-server/
├── canoe_server/          服务端代码（草稿）
│   ├── app.py             FastAPI 入口
│   ├── models.py          ORM：users / tokens / nodes / user_node / sessions
│   ├── security.py        密码哈希、令牌、入口凭证
│   ├── routers/           auth.py / client.py / admin.py
│   └── services/          nodes.py（入口白名单）/ sessions.py / relay.py
├── relay/                 中转层部署材料（Nginx / systemd）
├── schema.sql             建表语句（由 ORM 模型自动生成）
├── seed.py                建表 + 管理员 + 示例节点
├── smoke_test.py          端到端冒烟测试（56 项）
└── run.py
```

这份草稿已经实现了你阶段3 里要求的全部内容：

| 你的阶段3 要求 | 草稿里的实现 |
|---|---|
| 用户系统：注册、登录、token 鉴权 | `routers/auth.py` + `deps.py` |
| 管理客户端：在线状态、封禁、到期 | `routers/admin.py` |
| 管理节点：后台配置真实节点 | `routers/admin.py`（`real_*` 字段） |
| 配置下发 | `GET /api/config` |
| 心跳 / 同步 | `POST /api/heartbeat` + `config_version` |
| 数据库表 | `models.py`（5 张表都在，字段有取舍，见 `docs/03-database.md`） |
| API 约定 | `docs/02-api.md` |

---

## 阶段 3 开始时的三个选项

1. **直接 review 这份草稿**，按你的新要求调整后进入阶段4 —— 最省事
2. **推倒重写** —— 如果你想自己掌控服务端的每一行
3. **删掉重来** —— 说一声我就清空这个目录

在你确认之前，我不会动这个目录。

---

## 与阶段1 的接口

阶段1 的客户端里，这两个位置是为阶段3 留的接线点：

| 阶段1 的位置 | 阶段3 替换成 |
|---|---|
| `canoe_client/testnodes.build_proxy_outbound()` | 用 `api.fetch_config()` 返回的 `entry` 构造出站（指向**中转层入口**） |
| `canoe_client/localauth.login()` / `register()` | 调 `api.login()` / `api.register()` |
| `canoe_client/testnodes.py` | 删除 |

界面代码（`ui/`）不用改 —— 这正是阶段1 把这两个函数单独抽出来的原因。

客户端侧的 `canoe_client/api.py` 也已经写好了，现在没接线，阶段3 直接用。
