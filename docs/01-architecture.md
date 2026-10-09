# 01 · 项目结构与架构

> **阶段状态**：中转层**已删除**。服务端只分发订阅、不转发流量；
> 节点服务器与 Canoe 服务端互不关联。详见第 4 节。

> 轻舟已过万重山 · One boat, one tap.

## 1. 项目结构

```
canoe/
├── canoe-core/                  公共库：客户端与服务端共享的契约
│   ├── pyproject.toml
│   └── canoe_core/
│       ├── __init__.py          统一导出
│       ├── constants.py         品牌名 / 界面文案 / 配色 / API 路径 / 错误码
│       ├── models.py            ★ 共享请求响应模型
│       ├── crypto.py            ★ 订阅加密信封（seal / unseal）
│       ├── links.py             ★ 节点链接解析 + 明文泄漏自检
│       ├── passwords.py         密码哈希与令牌
│       └── version.py
│
├── canoe-server/                服务端（FastAPI）
│   ├── requirements.txt
│   ├── .env.example
│   ├── run.py                   开发启动脚本
│   ├── seed.py                  建表 / 管理员；--schema 输出 DDL
│   ├── release.py               ★ 命令行发布客户端安装包
│   ├── smoke_test.py            端到端冒烟测试（104 项）
│   └── test_release.py          发布命令的测试（自带临时库）
│   ├── schema.sql               ★ 建表语句（由 ORM 模型自动生成，不会漂移）
│   ├── deploy/                  ★ 部署材料（install.sh / canoe.sh / systemd / Nginx）
│   └── canoe_server/
│       ├── app.py               FastAPI 入口
│       ├── config.py            配置
│       ├── database.py          引擎、会话、老库轻量迁移
│       ├── models.py            ORM 模型（users/tokens/nodes/user_nodes/sessions）
│       ├── security.py          密码哈希 / 令牌
│       ├── deps.py              鉴权依赖
│       ├── routers/
│       │   ├── auth.py          /api/register /api/login /api/logout /api/me
│       │   ├── client.py        ★ /api/config /api/heartbeat /api/subscription /api/events
│       │   └── admin.py         /api/admin/*
│       └── services/
│           ├── nodes.py         ★★ 节点、绑定关系、订阅正文（安全核心）
│           ├── sessions.py      令牌与会话生命周期
│           ├── updates.py       ★ 客户端更新 + 订阅更新
│           └── broadcast.py     ★ SSE 推送中心
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
│   │   └── canoe.ico / .png
│   ├── bin/                     放 sing-box.exe / wintun.dll（自行下载）
│   ├── tests/                   6 套，共 281 项
│   └── canoe_client/
│       ├── app.py               入口 + 页面切换 + 托盘
│       ├── api.py               与服务端通信（走 canoe_core 模型校验）
│       ├── links.py             订阅链接解析（实现在 canoe-core，这里只 re-export）
│       ├── session.py           内存态会话（★ 不落盘，含 sub_key）
│       ├── kernel.py            ★★ sing-box 进程管理（配置来自解密后的订阅）
│       ├── sysproxy.py          Windows 系统代理（备份/还原）
│       ├── tun.py               全局模式先决条件检查
│       ├── events.py            SSE 长连接
│       ├── update.py            客户端更新
│       ├── worker.py            线程池，避免卡住 UI
│       └── ui/
│           ├── style.py         QSS（配色取自 canoe_core.Palette）
│           ├── artwork.py       背景画与图标
│           ├── auth_view.py     登舟 / 造舟
│           ├── tray.py          系统托盘
│           └── main_view.py     ★ 主界面：节点名 + 启航 + 靠岸
│
└── docs/                        本目录
```

## 2. 三个角色各自负责什么

| 角色 | 职责 | 不该做的事 |
|---|---|---|
| **canoe-client** | 登录、拉订阅、解密、开关；拉起/关闭本地内核 | 不落盘订阅、不导出配置、不知道自己能拿到几个节点 |
| **canoe-server** | 账号、鉴权、节点管理、**只分发订阅** | 不承载用户流量、不渲染代理配置、不替客户端选节点 |
| **节点服务器** | 真正跑代理、承接流量 | 与 Canoe 服务端**互不关联**；Canoe 这边不知道它的存在 |

节点服务器的地址、协议、密钥只以**一行链接**的形式存在：管理员在面板上
贴进来，原样（加密后）发给客户。

## 3. 数据流

```
①  客户端填用户名密码 → POST /api/login
    ← 返回 token + 用户信息 + **会话级订阅密钥 sub_key**（只放内存）

②  点「启航」
    ├─ GET /api/config?device_id=...&mode=system_proxy
    │   服务端校验：令牌有效 / 未封禁 / 未到期 / 设备数未超限
    │   ← 返回 { node_name, session_id, revision, heartbeat_interval }
    │   （**不含任何节点信息** —— 服务端不替客户端选节点）
    └─ GET /api/subscription
        ← 返回 { revision, envelope }，envelope 是密文

③  客户端用 sub_key 在内存里解开 envelope → 得到几行节点链接
    解析成 sing-box 出站（一行一个，客户端挑第一个能用的）

④  写临时文件 → 启动 sing-box → 2 秒后删除临时文件
    sing-box 在 127.0.0.1:20818 起一个混合代理（SOCKS + HTTP）
    状态变为「已启航」

⑤  写 Windows 系统代理指向 127.0.0.1:20818（同时刷新 WinINet）

⑥  每 30s POST /api/heartbeat
    服务端可在此拒绝续期 → 客户端自动「靠岸」并提示（用于封禁/到期踢下线）
    若 revision 变化 → 客户端重新拉订阅；拉回来是空就地销毁

⑦  点「靠岸」→ 停内核 → 还原系统代理 → POST /api/session/stop
    状态变为「已靠岸」（登录令牌保留，不用重新登舟）
```

## 4. 订阅分发是怎么回事

服务端**从不下发节点参数**（主机、端口、UUID、密钥），也不渲染任何
sing-box 配置。它只做一件事：把该客户绑定的节点链接拼起来，加密发过去。

- **节点** = 一行链接（`ss://` / `vmess://` / `vless://` / `trojan://`）+ 备注。
  备注只是给管理员自己看的，不进订阅。
- **绑定** = `user_nodes` 表里的一行。管理员在面板上勾选。
- **订阅正文** = 绑定节点的 `link` 用换行拼起来。
- **下发** = `canoe_core.crypto.seal()` 加密成信封，附一个明文 `revision` 指纹。

密文里是什么，只有拿着 `sub_key` 的客户端解得开；而 `sub_key` 是每次登录
现发的，服务端不认这个会话了客户端就什么都拿不到。

### 空信封 = 停止分发

账号被封、已到期、或管理员把绑定全取消 → 服务端回一个**空信封**。
客户端见到它必须销毁本地订阅（内存里的那几行）并断开。
这是"服务端完全可控"的落点。

## 5. 换节点为什么用户无感

节点换了（IP 被封、迁移机房），管理员在面板上把那一行链接改掉即可：

1. 改 `nodes.link`；
2. 变更会**定向推送**给绑了这个节点的账号（SSE `config_changed`）；
3. 客户端收到就重新拉订阅 —— 拉回来的密文里已经是新链接。

客户端什么都不用配置，也不知道"节点换了一台"。

> 注意：这一版**不保证**客户端能拿到多个节点里"自动选最快"的那个 ——
> 客户端保持极简，只挑第一个能解析出来的。要多节点择优是以后的事。

## 6. 两种接管模式

| 模式 | 原理 | 权限 | 覆盖 |
|---|---|---|---|
| 系统代理 | 本机 mixed 入站 + 写注册表 | 普通用户 | 支持系统代理的程序（浏览器等） |
| 全局(TUN) | 本机 tun 入站 + wintun | **管理员** | 全部 IPv4/IPv6 流量 |

两种模式客户端生成的 sing-box 配置**只有 `inbounds` 不同，出站完全相同**。
