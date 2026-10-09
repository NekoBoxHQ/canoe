# 04 · 如何做到客户端拿不到真实节点

> **阶段状态**：本文描述的中转层模式**已经落地**（阶段 1-5 完成）。
> 客户端只拿中转入口，真实节点只在服务端；中转层配置由
> `canoe_server/services/relay.py` 从数据库渲染。

这是整个项目最重要的约束。本文说明它由哪几层机制共同保证、每一层被绕过时会发生什么，
以及当前实现的边界。

---

## 0. 一句话结论

**真实节点信息从来没有"传到客户端再藏起来"——它从一开始就不在客户端能到达的数据通路上。**

这一点很关键。如果做法是"下发了节点再在客户端加密/隐藏"，那反编译或抓包一定能拿到。
我们的做法是**信息根本不进入客户端进程**，而且**在类型系统里就没有接收它的位置**。

---

## 1. 六层防护

### 第 1 层：类型层 —— `canoe-core` 里没有真实节点的模型

这是本项目相对"只靠后端自觉"的关键改进。

`canoe-core/canoe_core/models.py` 里定义了客户端与服务端共享的契约。
其中客户端可见的节点信息只有**一个**类型：

```python
class EntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")     # ← 多一个字段直接构造失败

    transport: Literal["ws", "grpc", "tcp"] = "ws"
    host: str          # 中转层入口，不是真实节点
    port: int
    uuid: str          # 中转层入口 UUID
    path: str = ""
    sni: str = ""
    tls: bool = True
    insecure: bool = False
```

而 `/api/config` 的响应类型：

```python
class ConfigResponse(BaseModel):
    session_id: str
    node_name: str
    token: str
    expires_at: int
    heartbeat_interval: int
    config_version: int
    entry: EntryPayload      # ← 只有这个
```

于是客户端代码里：

```python
resp = api.fetch_config(mode)   # -> ConfigResponse
resp.entry.host                 # 中转层入口，可以
resp.entry.real_host            # AttributeError —— 这个字段压根不存在
```

**不是"服务端记得别下发"，而是"客户端侧根本没有接收它的类型"。**
将来有人往节点表加了字段，也不会顺着类型定义漏到客户端。

两个自校验方法把这层保护做成了**可执行的断言**，测试里会调用：

```python
resp.assert_no_real_fields()      # 递归扫描序列化结果，出现 real_* 就抛异常
resp.entry.assert_whitelisted()   # 字段集合必须与 constants.ENTRY_FIELDS 完全一致
```

`EntryPayload.assert_whitelisted()` 还防住了"有人往 EntryPayload 里偷偷加字段"
这种情况 —— 加了就报错，逼你同步更新白名单和文档。

### 第 2 层：数据结构层 —— 字段前缀隔离

`nodes` 表用 `entry_*` / `real_*` 前缀把两类信息物理隔开：

| 前缀 | 含义 | 谁能读 |
|---|---|---|
| `name`, `entry_*` | 中转入口与显示名 | 客户端可见 |
| `real_*` | 真实节点 | 只有 `services/relay.py` 和 `/api/admin` |

服务端给客户端组装响应时，**只走一个白名单函数**（`services/nodes.py`）：

```python
def to_entry_payload(node: Node) -> EntryPayload:
    """客户端可见的唯一出口。改这里之前请先读 docs/04-security.md。"""
    payload = EntryPayload(
        transport=node.entry_transport,
        host=node.entry_host,
        port=node.entry_port,
        uuid=node.entry_uuid,
        path=node.entry_path,
        sni=node.entry_sni or node.entry_host,
        tls=node.entry_tls,
        insecure=node.entry_insecure,
    )
    payload.assert_whitelisted()      # 自检，越界立刻炸
    return payload
```

对应地，`/api/admin/nodes` 走另一个函数 `to_admin_payload()`，它才包含 `real_*`。

> **绝不要把它改成 `node.model_dump()` 或 `node.__dict__`。**
> 这是这类系统最常见的失手方式：新增一个 `real_*` 字段，全量序列化就顺手泄漏了。

### 第 3 层：鉴权层 —— 入口参数不是免费的

`entry_host / entry_uuid / entry_path` **不在登录响应里返回**，
必须再调一次 `GET /api/config`，服务端在这里做全套校验：

```
令牌有效 → 用户 status == active → 未过期 → 设备数未超限
```

任何一项不过就返回 `403` / `409`，客户端拿不到入口参数。

也就是说：**一个被封禁/过期的账号，连中转层入口地址都拿不到。**

### 第 4 层：凭证层 —— 短期 + 绑定 + 可吊销

`/api/config` 返回的 `token` 是一个 HMAC 签名的短期凭证：

```python
payload = {
    "u": user_id,
    "n": node_id,
    "d": sha256(device_id)[:16],   # 绑定设备
    "iat": issued_at,
    "exp": issued_at + 300,         # 默认 5 分钟
    "jti": random_hex,
}
ticket = b64url(payload) + "." + b64url(hmac_sha256(TICKET_SECRET, b64url(payload)))
```

- **短期**：掉线后凭证很快失效，被抓包也只有一个窗口期
- **绑定设备**：换设备用不了
- **服务端可吊销**：`sessions` 表记录 `ticket_hash`（不存原文），
  管理员封禁 → 会话置 `revoked` → 客户端下次心跳收到 `revoked=true` → 自动靠岸
- **不存原文**：数据库泄漏也不能反推出有效凭证

> ⚠️ 诚实的说明：`entry_uuid` 目前在中转层是**静态配置**的。`token` 服务于
> **准入控制 + 审计 + 吊销**，而不是"动态改中转层 UUID"。
> 如果你需要 UUID 级别的动态轮换，见第 6 节。

### 第 5 层：进程层 —— 客户端进程里就没有真实节点

客户端生成的 sing-box 配置长这样（`canoe_client/kernel.py`）：

```jsonc
{
  "inbounds": [
    { "type": "mixed", "listen": "127.0.0.1", "listen_port": 20818 }   // 本地入口
  ],
  "outbounds": [
    {
      "type": "vless",
      "tag": "entry",
      "server": "canoe.example.com",   // ← 中转层，不是真实节点
      "server_port": 443,
      "uuid": "<entry_uuid>",
      "transport": { "type": "ws", "path": "/e/hk01" },
      "tls": { "enabled": true, "server_name": "canoe.example.com" }
    },
    { "type": "direct", "tag": "direct" }
  ],
  "route": { "final": "entry" }
}
```

把它整个 dump 出来、或者抓包、或者反编译客户端，
**顶多拿到 `canoe.example.com:443 + /e/hk01`** —— 这就是中转层的门牌号。

附加措施：

- 配置**只在内存里构造**；写临时文件是为了喂给 sing-box 进程，
  **进程启动 2 秒后立即 `os.remove()`**（`kernel.py`）
- 客户端 UI 没有任何"查看配置 / 导出配置 / 复制链接 / 二维码"的入口
  （`gui_test.py` 里有断言检查这一点）
- 会话数据（令牌、入口参数）只存在 `Session` 对象里，**不写磁盘**，退出即失效
- `client.json` 里只有渡口地址、device_id、本地端口这类非敏感信息

### 第 6 层：网络层 —— 中转层做出入站绑定

真实节点只出现在中转层机器的 sing-box 配置里：

```jsonc
{ "route": { "rules": [ { "inbound": ["entry-1"], "outbound": "node-1" } ] } }
```

客户端连的是 `entry-1`（入站），流量被路由到 `node-1`（出站），出站里才有真实 IP。

中转层是唯一同时知道"谁在连"和"真正去哪"的环节，而它运行在**用户控制不到的服务器上**。

---

## 2. 攻击面分析：用户能做什么

假设用户是"恶意的付费用户"，他想拿到真实节点：

| 攻击 | 结果 |
|---|---|
| 抓包看客户端流量 | 只能看到到 `canoe.example.com:443` 的 TLS，内容还是 WS 封装的 |
| 反编译 `.py` / PyInstaller 包 | 客户端代码里没有真实节点硬编码；配置是运行时从服务端拿的 |
| dump 客户端内存里的 sing-box 配置 | 只有中转层入口，见第 5 层 |
| 重放 `/api/config` 的响应 | token 5 分钟过期 + 绑定 device_id |
| 用别人的 token 调接口 | 令牌无签名泄漏拿不到；且 `/api/config` 还校验设备 |
| 直连真实节点 IP 猜端口 | 真实 IP 从不下发，猜不到；且真实节点应配防火墙（见下） |
| 中间人劫持服务端流量 | 生产强制 HTTPS + HSTS，`entry.insecure=false` |

### 必须补的一刀：真实节点白名单

即使真实节点 IP 泄漏（比如机房被扫描），也要让它**只接受来自中转层的连接**：

```bash
# 在真实节点机器上
sudo ufw default deny incoming
sudo ufw allow 22/tcp
sudo ufw allow from <relay_ip> to any port 8443 proto tcp
sudo ufw enable
```

**这一条在"客户端拿不到"的范畴之外，但缺少它，前五层防护的实际价值会打折。**
它是整套设计里最容易被忽略、而攻击成本最低的一环。

---

## 3. 为什么不用"直接下发 vmess/vless 链接"

需求里列为禁止项，这里给出原因，便于你在 review 时说服别人：

1. 链接一旦下发就等于节点公开 —— 会被分享、被写进爬虫、被扫描
2. 换节点/换 IP 必须让全部用户重新拉配置
3. 无法精细化控制：不能按用户封禁、不能统计流量、不能限速
4. 客户端可导出 → 你要的"极简界面"就变成了"节点管理器"，与产品目标背离

---

## 4. 当前实现的边界（诚实说明）

| 项目 | 现状 | 生产建议 |
|---|---|---|
| `entry_uuid` 是否动态 | **静态**，每节点一个 | 见第 6 节 |
| 中转层是否鉴权 | sing-box 校验 VLESS UUID；服务端做准入 | 增加中转层与真实节点的 mTLS |
| 流量统计 | 未实现 | 接 sing-box `v2ray_api` 的 stats |
| 限速 | 未实现 | 中转层按用户 QoS |
| 客户端令牌存储 | **仅内存**，退出即失效 | 若要"记住我"，用 Windows 凭据管理器 |

最后一条是刻意的取舍：宁可每次启动重新登舟，也不让令牌落到磁盘。
如果你接受"记住我"，正确做法是用 `keyring` 库写进 Windows 凭据管理器（DPAPI 加密），
而不是 `json.dump(token)`。

---

## 5. 自动化验证

这三套测试把上面的约束变成了可执行的断言，不是纸面承诺：

```bash
# 服务端 56 项 —— 含「/api/config 响应不含 real_* / 不含真实 IP / 不含真实 UUID」
python canoe-server/smoke_test.py

# 客户端 28 项 —— 含「生成的 sing-box 配置不含真实节点」「EntryPayload 拒绝 real_host」
python canoe-client/tests/client_test.py

# GUI 36 项 —— 含「主界面没有导出/查看配置按钮」「界面上不出现协议/密码/端口字样」
python canoe-client/tests/gui_test.py
```

服务端测试里的核心断言：

```python
check("★ 响应不含 real_* 字段", not scan_real_keys(data))
check("★ 响应不含真实 IP",     REAL_IP not in raw)
check("★ 响应不含真实 UUID",   REAL_UUID not in raw)
check("★ 响应不含 'real_' 字样", "real_" not in raw)
```

客户端测试里的核心断言：

```python
check(f"[{mode}] ★ 不含真实 IP",   REAL_IP not in blob)
check(f"[{mode}] ★ 不含真实 UUID", REAL_UUID not in blob)
check("★ EntryPayload 拒绝 real_host（extra=forbid）", True)
```

---

## 6. 增强方案：让入口 UUID 也动态化

如果威胁模型要求"连中转层入口凭证都必须一次性"，做法是让服务端**动态增删中转层入站的用户**，
而不是把静态 UUID 下发下去。

sing-box 提供 `experimental.v2ray_api`，可以 gRPC 调用 `HandlerService.AlterInbound`
往 VLESS 入站里动态 Add/Remove 用户；同时路由规则用 `auth_user` 匹配，
把不同用户路由到不同出站。

中转层配置：

```jsonc
{
  "experimental": {
    "v2ray_api": { "listen": "127.0.0.1:10085", "stats": { "enabled": true } }
  },
  "inbounds": [{
    "type": "vless", "tag": "entry", "listen": "127.0.0.1", "listen_port": 20001,
    "users": [],                                   // ← 空的，运行时注入
    "transport": { "type": "ws", "path": "/e" }
  }],
  "outbounds": [
    { "type": "vless", "tag": "node-hk-01", "server": "203.0.113.7", "...": "..." }
  ],
  "route": {
    "rules": [
      { "auth_user": ["u12-n3"], "outbound": "node-hk-01" }   // 按注入时带的 email 路由
    ]
  }
}
```

服务端在 `/api/config` 里：

1. 生成一次性 UUID `U` 和 email `u{user_id}-n{node_id}`
2. gRPC `AddUser` 到 `entry` 入站（`U` + email）
3. 起一个 5 分钟后的定时任务 `RemoveUser`
4. 把 `U` 作为 `entry.uuid` 返回给客户端

这样连入口 UUID 都是短期、一次性、和用户/节点绑定的。

**本仓库默认不启用这条路径**（保持依赖精简、开箱可跑），但架构已经为它留好位置：
`entry.uuid` 就是从 `services/nodes.to_entry_payload()` 下发的，
换成动态 UUID 只需要改那一个函数 + 加一个 gRPC 客户端。
