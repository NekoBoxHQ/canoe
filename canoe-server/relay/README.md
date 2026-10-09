# 中转层（relay）

轻舟的第三块拼图。它是"客户端拿不到真实节点"的技术落点：

```
客户端 ──VLESS+WS+TLS──► 中转层 sing-box ──真实协议──► 真实节点
                          ↑ 出入站绑定在这里
```

**真实节点的 IP / 端口 / UUID 只出现在这台机器的 sing-box 配置里。**

---

## 1. 生成配置

中转层配置由 Canoe Server 从数据库渲染，**不要手写**。

### 方式 A：通过 API（推荐，能触发 reload hook）

```bash
TOKEN=<管理员 token>
curl -H "Authorization: Bearer $TOKEN" \
     https://api.canoe.example.com/api/admin/relay/config?fmt=singbox \
     -o /etc/sing-box/config.json

curl -H "Authorization: Bearer $TOKEN" \
     https://api.canoe.example.com/api/admin/relay/config?fmt=nginx \
     -o /etc/nginx/conf.d/canoe.conf

curl -X POST -H "Authorization: Bearer $TOKEN" \
     https://api.canoe.example.com/api/admin/relay/reload
```

### 方式 B：在服务端机器上直接渲染

```bash
cd canoe-server
python -c "
import json
from canoe_server.database import SessionLocal
from canoe_server.services.relay import render_singbox
db = SessionLocal()
print(json.dumps(render_singbox(db), indent=2, ensure_ascii=False))
" > /etc/sing-box/config.json
```

### 校验并重启

```bash
sing-box check -c /etc/sing-box/config.json
sudo systemctl restart sing-box
sudo journalctl -u sing-box -f
```

---

## 2. 部署（推荐：Nginx 前置）

sing-box 的多个入站**不能共用同一个 listen_port**，所以要把多个节点都挂在
`canoe.example.com:443` 上：让 Nginx 在 443 终止 TLS，再按 WebSocket 路径
反代到本机不同端口。

```
客户端 ──wss://canoe.example.com/e/hk01──► Nginx:443 ──► sing-box 127.0.0.1:20001
客户端 ──wss://canoe.example.com/e/us01──► Nginx:443 ──► sing-box 127.0.0.1:20002
```

`nginx.example.conf` 是完整示例。

> ⚠️ **`proxy_read_timeout` 必须调大。** 默认 60 秒会把长连接掐断，
> 表现是"能连上但几十秒后断流"。

证书用 certbot 申请即可（`certbot --nginx -d canoe.example.com`）。

---

## 3. 部署（直连模式：sing-box 自己开 TLS）

只有一个节点、或不想引入 Nginx 时：

```env
RELAY_BEHIND_NGINX=false
RELAY_TLS_CERT=/etc/sing-box/cert.pem
RELAY_TLS_KEY=/etc/sing-box/key.pem
```

给每个节点配**不同的 `entry_port`**，sing-box 用上面的证书自己终止 TLS。

```bash
sing-box run -c /etc/sing-box/config.json
```

---

## 4. 让服务端能触发重载

`.env` 里配置：

```env
RELAY_RELOAD_HOOK=rsync -a ./data/relay_config.json relay-host:/etc/sing-box/config.json
```

之后 `POST /api/admin/relay/reload` 会重新渲染并执行这条命令。
更安全的做法是去掉 shell 能力，改成一个白名单脚本。

---

## 5. 硬性加固（**强烈建议**）

### 5.1 真实节点只接受中转层的连接

即使真实节点 IP 泄漏（机房被扫描），也要让它无法被直连：

```
# 在真实节点机器上
ufw default deny incoming
ufw allow 22/tcp
ufw allow from <relay_ip> to any port 8443 proto tcp
ufw enable
```

**这一条在"客户端拿不到"的范畴之外，但缺少它，前面几层防护的实际价值会打折。**
它是整套设计里最容易被忽略、而攻击成本最低的一环。

### 5.2 中转层本身

```
ufw allow 443/tcp
ufw allow from 10.0.0.0/8 to any port 22 proto tcp
```

### 5.3 监控

```bash
sudo journalctl -u sing-box -f | grep -iE "error|reject"
vnstat -l
```

---

## 6. 故障排查

| 现象 | 排查 |
|---|---|
| 客户端连不上，Nginx 502 | sing-box 没起 / 端口对不上：`ss -lntp \| grep 200` |
| 客户端连上但秒断 | Nginx `proxy_read_timeout` 太小 |
| WebSocket 握手 400 | `entry_path` 与 Nginx `location` 不一致 |
| TLS 报错 | 证书路径 / `entry_sni` 与证书域名不一致 |
| 客户端连上了但没网 | 出站配置错：在 relay 机器上直接测真实节点 |
| 换节点后客户端无感但没生效 | 忘了 reload sing-box |
| 启动报 address already in use | 两个节点用了同一个 `entry_port`（直连模式），或 `relay_internal_base` 冲突 |

---

## 7. 文件说明

| 文件 | 用途 |
|---|---|
| `nginx.example.conf` | Nginx server 块示例（也可由 API 自动生成） |
| `sing-box.service` | systemd unit |
