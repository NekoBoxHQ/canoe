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

## ⚠️ 当前进度：阶段 1 已完成，等确认

按你的要求分阶段开发，**每个阶段做完停下等确认**。

| 阶段 | 内容 | 状态 |
|---|---|---|
| **1** | **框架 + 测试节点 + 桌面端跑通** | ✅ **已完成（本次）** |
| 2 | 推送到 git 仓库，打 tag `v0.1.0-client` | ⏸ 等确认 |
| 3 | 服务端管理端（API + 数据库） | ⏸ 等确认 |
| 4 | 部署服务端管理界面（HTTPS） | ⏸ 等确认 |
| 5 | 客户端与服务端联调、同步测试 | ⏸ 等确认 |

---

## 阶段 1 做了什么

### 验收标准对照

| 验收项 | 状态 | 怎么验证的 |
|---|---|---|
| 能打开界面 | ✅ | `tests/test_gui.py`（46 项全过） |
| 能注册 / 登录（本地假账号） | ✅ | 含重复注册、错误密码、用户名过短的拒绝用例 |
| 跳过注册直接测 | ✅ | 登舟页有「直接体验」按钮，一键进主界面 |
| 主界面只有节点名 + 启航 + 靠岸 | ✅ | 测试断言界面上**不出现**地址/端口/协议/密钥 |
| **点启航能真的走代理** | ✅ | `tests/test_live.py` + `test_gui.py` 真实联网验证 |
| **点靠岸能停** | ✅ | 靠岸后再请求，代理确实失效 |
| 不显示任何节点地址、端口、协议、密码 | ✅ | 界面全文本扫描断言 |
| 能打包成 `Canoe.exe` | ✅ | 见下方「打包」 |

### 分流效果（内核日志实证，不是推测）

```
outbound/shadowsocks[proxy]:  outbound connection to api.ipify.org:443   ← 国外走代理
outbound/direct[direct]:      outbound connection to www.baidu.com:443   ← 国内走直连
```

### 可选设置（两排，居中，无标签）

```
        ○ 分流          ○ 全局        ← 二选一，默认分流
        ☑ 系统代理      ☐ TUN 模式     ← 可并存，默认只勾系统代理
```

| 需求 | 实现 |
|---|---|
| 默认系统代理 | ✅ 第二排「系统代理」默认勾选，「TUN 模式」默认不勾 |
| 系统代理与 TUN 可并存 | ✅ 第二排是两个独立复选框，不是单选 |
| 「绕过局域网 + 绕过大陆」是一个模式，与「全局」并列 | ✅ 合并成 `profile`，第一排就是「分流 / 全局」 |
| 不要「接管」「分流」两个标签 | ✅ 已去掉 |
| TUN 要 IPv4 + IPv6 | ✅ 恒为双栈（`172.19.0.1/30` + `fdfe:dcba:9876::1/126`），不暴露开关 |

---|---|
| 默认系统代理、TUN 默认关 | ✅ 第一排 |
| 「绕过局域网 + 绕过大陆」是一个模式，与「全局」并列 | ✅ 合并成 `profile`，第二排就是「分流 / 全局」 |
| TUN 要 IPv4 + IPv6 | ✅ TUN 网卡同时配 `172.19.0.1/30` + `fdfe:dcba:9876::1/126`，始终双栈（不再暴露开关） |

---

## 目录结构

```
canoe/
├── canoe-core/            公共库
│   └── canoe_core/
│       ├── constants.py   品牌名 / 界面文案 / 配色 / API 路径
│       ├── models.py      阶段3 的共享契约模型
│       ├── passwords.py   密码哈希（阶段1 本地账号与阶段3 服务端共用）
│       └── version.py
│
├── canoe-client/          桌面客户端（本期重点）
│   ├── run.py
│   ├── build.bat          ← 一键打包成 Canoe.exe
│   ├── canoe.spec
│   ├── assets/            图标（含可复现的生成脚本）
│   ├── bin/               ← sing-box.exe / wintun.dll / ruleset/
│   ├── tests/
│   │   ├── test_config.py 配置生成（32 项）
│   │   ├── test_live.py   真实联网验收（9 项）
│   │   └── test_gui.py    GUI 端到端（42 项）
│   └── canoe_client/
│       ├── app.py         入口 + 页面切换 + 退出兜底清理
│       ├── testnodes.py   ★ 阶段1 写死的测试节点（阶段3 删除）
│       ├── localauth.py   ★ 阶段1 本地假账号（阶段3 替换）
│       ├── kernel.py      sing-box 配置生成与进程管理
│       ├── options.py     运行选项
│       ├── sysproxy.py    Windows 系统代理
│       ├── tun.py         全局模式先决条件检查
│       ├── worker.py      线程池
│       └── ui/            界面
│
├── canoe-server/          服务端 —— 阶段 3 再做（见该目录 README）
└── docs/                  五份设计文档
```

---

## 快速开始（阶段 1）

### 1. 装依赖

```bash
cd canoe-core && pip install -e .
cd ../canoe-client && pip install -r requirements.txt
```

### 2. 放内核

`canoe-client/bin/` 下需要有：

| 文件 | 必需性 | 说明 |
|---|---|---|
| `sing-box.exe` | **必需** | 代理内核，推荐 1.12+ |
| `bin/ruleset/*.srs` | 绕过大陆需要 | 已内置 |
| `wintun.dll` | 全局(TUN)模式需要 | 从 <https://www.wintun.net/> 下载 |

### 3. 跑起来

```bash
python run.py
```

造舟 → 登舟 → 启航。

### 4. 打包

```bat
build.bat
```

产物 `dist\Canoe\Canoe.exe`，整个 `dist\Canoe\` 目录打 zip 发给用户，目标机器不需要装 Python。

---

## 测试

```bash
cd canoe-client

# 配置生成（不需要联网，32 项）
python tests/test_config.py

# 真实联网验收（需要节点可达，9 项）
python tests/test_live.py

# GUI 端到端（离屏，会真的启航一次，46 项）
set QT_QPA_PLATFORM=offscreen
python tests/test_gui.py
```

---

## 阶段 1 的诚实说明（重要）

### 1. 阶段1 客户端**确实持有真实节点**

这是刻意的临时状态，也是阶段1 与最终形态最大的差别：

```
阶段1：  客户端 ──直接连──► 测试节点          ← 客户端有节点信息 ⚠
阶段3+： 客户端 ──连──► 中转层 ──转发──► 真实节点  ← 客户端只有中转层入口 ✅
```

节点信息**只写在一个文件里**（`canoe_client/testnodes.py`），
并且只通过一个函数对外暴露（`build_proxy_outbound()`）。
阶段3 替换这一个函数即可，界面与内核代码都不用动。

**这份代码不能当正式版发布。**

### 2. 本地账号是假账号

`localauth.py` 把账号存在 `%APPDATA%\Canoe\accounts.json`，密码用 pbkdf2 哈希
（不存明文），但任何人都能改这个文件。阶段3 会被服务端的
`POST /api/register` 与 `POST /api/login` 取代。

### 3. 未在阶段1 覆盖的

- 流量统计、限速
- 多设备、封禁、到期 —— 这些依赖服务端，属于阶段3/5
- `wintun.dll` 需要你自行下载（没打进仓库）
- 代码签名（未签名的 exe 首次运行会有 SmartScreen 提示）

---

## 外部依赖

| 组件 | 用途 | 获取 |
|---|---|---|
| **sing-box** | 代理内核 | [Releases](https://github.com/SagerNet/sing-box/releases)，推荐 1.12+（本项目用 1.14.2 验证） |
| **wintun.dll** | 仅全局(TUN)模式 | <https://www.wintun.net/> |
| Nginx + certbot | 阶段4 服务端部署 | 系统包管理器 |

---

## 免责声明

本项目是代理软件的**客户端-服务端管理框架**，用于自建网络接入与流量管理。
请在法律允许的范围内使用。
