# 01 · 项目结构与架构

> **阶段状态**：本文描述的中转层模式**已经落地**（阶段 1-5 完成）。
> 客户端只拿中转入口，真实节点只在服务端；中转层配置由
> `canoe_server/services/relay.py` 从数据库渲染。

> 轻舟已过万重山 · One boat, one tap.

## 1. 项目结构

```
canoe/
├── canoe-core/                  公共库：客户端与服务端共享的契约
│   ├── pyproject.toml
│   └── canoe_core/
│       ├── __init__.py          统一导出
│       ├── constants.py         品牌名 / 界面文案 / 配色 / API 路径 / 错误码
│       ├── models.py            ★ 共享请求响应模型（安全边界在类型层面）
│       └── version.py
│
├── canoe-server/                服务端（FastAPI）
│   ├── requirements.txt
│   ├── .env.example
│   ├── run.py                   开发启动脚本
│   ├── seed.py                  建表 / 管理员 / 示例节点；--schema 输出 DDL
│   ├── smoke_test.py            端到端冒烟测试（56 项）
│   ├── schema.sql               ★ 建表语句（由 ORM 模型自动生成，不会漂移）
│   ├── relay/                   中转层部署材料
│   │   ├── nginx.example.conf
│   │   └── sing-box.service
│   └── canoe_server/
│       ├── app.py               FastAPI 入口
│       ├── config.py            配置
│       ├── database.py          引擎与会话
│       ├── models.py            ORM 模型（users/tokens/nodes/user_node/sessions）
│       ├── security.py          密码哈希 / 令牌 / 入口凭证
│       ├── deps.py              鉴权依赖
│       ├── routers/
│       │   ├── auth.py          /api/register /api/login /api/logout /api/me
│       │   ├── client.py        ★ /api/config /api/heartbeat /api/session/stop
│       │   └── admin.py         /api/admin/*
│       └── services/
│           ├── nodes.py         ★★ 入口参数白名单（安全核心）
│           ├── sessions.py      令牌与会话生命周期
│           └── relay.py         ★★ 中转层配置渲染（真实节点唯一出口）
│
├── canoe-client/                客户端（PySide6）
│   ├── requirements.txt
│   ├── run.py
│   ├── canoe.spec               PyInstaller 打包配置
│   ├── build.bat                ★ 一键打包成 Canoe.exe
│   ├── assets/
│   │   ├── logo-source.png      设计稿原图（圆形徽章）
│   │   ├── make_icon.py         抠圆 + 生成 .ico / png
│   │   ├── canoe-logo.png       ★ 界面用的徽章（圆外透明）
│   │   ├── canoe.ico / .png
│   ├── bin/                     放 sing-box.exe / wintun.dll（自行下载）
│   ├── tests/
│   │   ├── client_test.py       安全性测试（28 项）
│   │   └── gui_test.py          GUI 端到端测试（36 项）
│   └── canoe_client/
│       ├── app.py               入口 + 页面切换 + 退出兜底清理
│       ├── api.py               与服务端通信（走 canoe_core 模型校验）
│       ├── session.py           内存态会话（★ 不落盘）
│       ├── kernel.py            ★★ sing-box 进程管理（配置只含中转入口）
│       ├── sysproxy.py          Windows 系统代理（备份/还原）
│       ├── tun.py               全局模式先决条件检查
│       ├── worker.py            线程池，避免卡住 UI
│       └── ui/
│           ├── style.py         QSS（配色取自 canoe_core.Palette）
│           ├── auth_view.py     登舟 / 造舟
│           └── main_view.py     ★ 主界面：节点名 + 启航 + 靠岸
│
└── docs/                        本目录
```

## 2. 三个组件各自负责什么

| 组件 | 职责 | 不该做的事 |
|---|---|---|
| **canoe-client** | 登录、开关、显示节点名；拉起/关闭本地内核 | 不存节点、不导出配置、不知道真实节点 |
| **canoe-server** | 账号、鉴权、节点管理、下发入口参数、渲染中转层配置 | 不直接承载用户流量 |
| **中转层** | 接收客户端连接，按绑定转发到真实节点 | 运行在用户碰不到的服务器上 |

## 3. 数据流（系统代理模式）

```
①  客户端填用户名密码 → POST /api/login
    ← 返回 token + 用户信息（含节点显示名、到期时间）

②  点「启航」→ GET /api/config?device_id=...&mode=system_proxy
    服务端校验：令牌有效 / 未封禁 / 未到期 / 设备数未超限
    ← 返回 { node_name, entry{...}, token(ticket), session_id, config_version }

③  客户端用 entry 在内存里生成 sing-box 配置
    （只有本地 mixed 入站 + 一个指向中转层入口的 vless 出站）

④  写临时文件 → 启动 sing-box → 2 秒后删除临时文件
    sing-box 在 127.0.0.1:20818 起一个混合代理（SOCKS + HTTP）
    状态变为「已启航」

⑤  写 Windows 系统代理指向 127.0.0.1:20818（同时刷新 WinINet）

⑥  每 30s POST /api/heartbeat
    服务端可在此拒绝续期 → 客户端自动「靠岸」并提示（用于封禁/到期踢下线）
    若 config_version 变化 → 提示「配置已更新，请重新启航」

⑦  点「靠岸」→ 停内核 → 还原系统代理 → POST /api/session/stop
    状态变为「已靠岸」
```

## 4. 中转层的工作原理

中转层是"客户端拿不到真实节点"的技术落点。

服务端从数据库渲染出中转层 sing-box 配置（`services/relay.py`）：

```jsonc
{
  "inbounds": [{
    "type": "vless",
    "tag": "entry-1",
    "listen": "127.0.0.1",          // 只接受 Nginx 反代
    "listen_port": 20001,
    "users": [{ "uuid": "<入口 UUID>", "flow": "" }],
    "transport": { "type": "ws", "path": "/e/hk01" }
  }],
  "outbounds": [{
    "type": "vless",
    "tag": "node-1",
    "server": "203.0.113.7",         // ← 真实 IP，客户端永远看不到
    "server_port": 8443,
    "uuid": "<真实 UUID>",
    "tls": { "enabled": true, "server_name": "real.example.com",
             "utls": { "enabled": true, "fingerprint": "chrome" } }
  }],
  "route": {
    "rules": [
      { "inbound": ["entry-1"], "outbound": "node-1" }   // ← 关键绑定
    ]
  }
}
```

客户端只拿到 `inbounds[0]` 那一层的 `host / port / uuid / path / sni`。
抓包、dump 内存，顶多拿到中转层的门牌号。

### 关于端口：为什么推荐 Nginx 前置

sing-box 的**多个入站不能共用同一个 listen_port**。所以要把多个节点都挂在
`canoe.example.com:443` 上，标准做法是 Nginx 在 443 终止 TLS，按 WS 路径
反代到本机不同端口：

```
客户端 ──wss://canoe.example.com/e/hk01──► Nginx:443 ──► sing-box 127.0.0.1:20001
客户端 ──wss://canoe.example.com/e/us01──► Nginx:443 ──► sing-box 127.0.0.1:20002
```

`canoe-server/relay/nginx.example.conf` 是完整示例。
若只有一个节点、不想引入 Nginx，把 `.env` 里 `RELAY_BEHIND_NGINX` 设为 `false`，
给每个节点配不同 `entry_port`，由 sing-box 自己终止 TLS。

## 5. 换节点为什么用户无感

真实节点换了（IP 被封、迁移机房），只需要：

1. 管理员在后台改 `nodes.real_*`；
2. 重新渲染中转层配置并热重载（`POST /api/admin/relay/reload` 或 `systemctl restart sing-box`）；
3. **客户端什么都不用做** —— 它连的始终是中转层入口。

如果把节点信息下发到客户端，换节点就得让所有用户重新同步，而且一旦有人抓包
或反编译，节点就全网暴露。

## 6. 两种接管模式

| 模式 | 原理 | 权限 | 覆盖 |
|---|---|---|---|
| 系统代理 | 本机 mixed 入站 + 写注册表 | 普通用户 | 支持系统代理的程序（浏览器等） |
| 全局(TUN) | 本机 tun 入站 + wintun | **管理员** | 全部 IPv4/IPv6 流量 |

两种模式客户端生成的 sing-box 配置**只有 `inbounds` 不同，出站完全相同**，
所以从"客户端拿不拿得到真实节点"这个角度看，两种模式安全性一致。
