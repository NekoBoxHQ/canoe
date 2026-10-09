# 部署 Canoe Server

把 `canoe-server` 跑到一台公网机器上，前面用 Nginx 终止 TLS。

> 中转层（真正转发流量的那个 sing-box）是**另一台机器**、另一套配置，
> 见 [`../relay/README.md`](../relay/README.md)。本文只管服务端 API。

---

## ⚠ 先看这条：只能开**一个** worker

推送（`/api/events`）的中心是**进程内**的内存结构（`services/broadcast.py`）。
如果 uvicorn 开多个 worker，管理端的请求可能落在 worker B，而客户端的长连接挂在
worker A —— 那条推送就永远送不到，表现是"推送时灵时不灵"。

所以：

```bash
uvicorn canoe_server.app:app --workers 1     # ← 必须
```

单 worker + async 足够撑住本项目这个量级。真要多 worker 时，
把 `EventHub` 换成 Redis pub/sub 再开（改一个文件，接口不变）。

---

## 1. 机器要求

| 项 | 建议 |
|---|---|
| 系统 | Debian 12 / Ubuntu 22.04+ |
| 配置 | 1 核 1G 起（API 本身很轻） |
| Python | 3.11+（3.13 实测可用） |
| 端口 | **默认对外 58588**（客户端固定拿它取更新和订阅），另有 80 留给 Let's Encrypt 签发与续期 |
| 域名 | 例如 `canoe.s-ui.com`，A 记录指向本机 |

> 服务端**不需要**大带宽 —— 流量走的是中转层那台机器，这里只传几百字节的配置。

---

## 2. 一键装（推荐）

```bash
sudo bash deploy/install.sh canoe.s-ui.com --port 58588 --https le
```

不带参数直接回车，会一项一项问你（域名 / 端口 / HTTPS 方式）。

| 选项 | 说明 |
|---|---|
| `--port N` | 对外端口，默认 **58588** |
| `--https le` | Let's Encrypt 证书（需要域名已解析到本机、80 端口空闲）。**推荐** |
| `--https self` | 自签证书。浏览器会警告；先用着，之后换正式证书 |
| `--https none` | 不加密（只建议放在别的反代后面） |

脚本会：建 `canoe` 用户 → 放代码到 `/opt/canoe` → 建 venv 装依赖 →
申请/生成证书 → 生成 `.env`（含随机 `TICKET_SECRET` 和管理员密码）→
建库 + 种子 → 装 systemd → 自检。

**默认是直连模式**：uvicorn 自己监听在指定端口上做 TLS，**不需要 Nginx**。
（想用 Nginx 前置的话，装完把 `.env` 里的 `TLS_CERT`/`TLS_KEY` 清空，
再按第 4 节配。）

跑完检查：

```bash
systemctl status canoe-api
curl -s https://canoe.s-ui.com:58588/api/health
```

打开面板：`https://canoe.s-ui.com:58588/panel`（管理员密码在 `/opt/canoe/ADMIN_PASSWORD.txt`）。

### 改端口 / 换证书

**只改 `.env` 就行**，不用碰 systemd 也不用 daemon-reload：

```bash
sudo -e /opt/canoe/canoe-server/.env     # 改 PORT / TLS_CERT / TLS_KEY
sudo systemctl restart canoe-api
```

`serve.py` 读 `.env` 决定监听什么。改完记得同步客户端那边的
`update_url`（`%APPDATA%\Canoe\client.json`）。

---

## 3. 手动装

```bash
# 3.1 用户与代码
sudo useradd -r -s /usr/sbin/nologin -d /opt/canoe canoe
sudo mkdir -p /opt/canoe && sudo chown canoe:canoe /opt/canoe
sudo -u canoe git clone <仓库> /opt/canoe

# 3.2 依赖（注意先装 canoe-core）
cd /opt/canoe/canoe-server
sudo -u canoe python3 -m venv .venv
sudo -u canoe .venv/bin/pip install -U pip
sudo -u canoe .venv/bin/pip install -e ../canoe-core -r requirements.txt

# 3.3 配置
sudo -u canoe cp .env.example .env
# 必须改的：
#   TICKET_SECRET   （生成：python3 -c "import secrets;print(secrets.token_urlsafe(48))"）
#   ADMIN_PASSWORD  （seed 用，装完再改也行）
#   PUBLIC_BASE_URL=https://api.canoe.example.com   ← 安装包下载地址靠它拼
sudo -u canoe $EDITOR .env

# 3.4 建库 + 管理员
sudo -u canoe .venv/bin/python seed.py

# 3.5 服务
sudo cp deploy/canoe-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now canoe-api
```

---

## 4. Nginx（可选）

默认的直连模式不需要它。只有下面这些情况才值得加一层 Nginx：

- 想用 443 标准端口（客户端那边就得写 `https://域名/api/...`，不带端口）
- 同一台机器上还要放别的服务
- 想上 Cloudflare 之类的 CDN 回源

装上之后，把 `.env` 里的 `TLS_CERT`/`TLS_KEY` **清空**（让 uvicorn 跑 HTTP），
Nginx 负责终止 TLS：

```bash
sudo cp deploy/nginx.canoe.conf /etc/nginx/sites-available/canoe
sudo ln -sf /etc/nginx/sites-available/canoe /etc/nginx/sites-enabled/canoe
sudo certbot --nginx -d canoe.s-ui.com
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart canoe-api
```

`nginx.canoe.conf` 里有三处**不能省**：

| 位置 | 为什么 |
|---|---|
| `/api/events` 的 `proxy_buffering off` | 不开的话 Nginx 会把推送攒在缓冲区，客户端收不到实时事件 |
| `/api/events` 的 `proxy_read_timeout 3600s` | 默认 60s 会把长连接掐断 |
| `/api/` 与 `/downloads/` 的 `client_max_body_size` | 不放宽的话上传大安装包会 413 |

---

## 5. 发布客户端新版本

两条路：

```bash
# A. 用管理端接口上传（推荐）
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -F "version=1.1.0" \
     -F "notes=修复 TUN 快速重连" \
     -F "min_version=1.0.0" \
     -F "file=@Canoe-1.1.0-win64.zip" \
     https://api.canoe.example.com/api/admin/releases/upload

# B. 自己把包丢进 releases/ 再登记版本
scp Canoe-1.1.0-win64.zip server:/opt/canoe/canoe-server/releases/
curl -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
     -d '{"version":"1.1.0","notes":"..."}' \
     https://api.canoe.example.com/api/admin/releases
```

发布后客户端点「更新」就会看到新版本。发布前可以先看一眼客户端到底会拿到什么：

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     https://api.canoe.example.com/api/admin/releases/latest-preview
```

---

## 6. 日常运维

```bash
systemctl status canoe-api
journalctl -u canoe-api -f                    # 看日志

# 备份（SQLite 就一个文件 + 上传的安装包）
tar czf canoe-backup-$(date +%F).tgz \
    -C /opt/canoe/canoe-server data releases

# 升级
cd /opt/canoe && sudo -u canoe git pull
sudo -u canoe canoe-server/.venv/bin/pip install -e canoe-core -r canoe-server/requirements.txt
sudo systemctl restart canoe-api
```

---

## 7. 排查

| 现象 | 排查 |
|---|---|
| 客户端提示"连不上渡口" | `curl https://api.canoe.example.com/api/health`；证书域名对不对 |
| 推送时有时无 | **开了多 worker**（见本文开头）。`systemctl cat canoe-api` 看 `--workers` |
| 推送完全不来 | Nginx 少了 `proxy_buffering off`；或客户端没带 Authorization 头 |
| 更新按钮报 404 | 还没发布过任何版本：`/api/admin/releases` 是空的 |
| 下载安装包 404 | 登记的 `filename` 和 `releases/` 里的文件名对不上 |
| 上传安装包 413 | Nginx `client_max_body_size` 太小，或超了 `MAX_RELEASE_MB` |
| 503 no_node | 没有启用的节点；后台加一个并勾上 `enabled` |
| 封禁了但客户端还在线 | 看 `pushed` 字段；为 0 说明客户端没挂着推送，会等心跳超时 |

---

## 8. 文件说明

| 文件 | 用途 |
|---|---|
| `install.sh` | 一键安装（Debian/Ubuntu） |
| `canoe-api.service` | systemd unit（已设单 worker） |
| `nginx.canoe.conf` | Nginx 站点示例（含 SSE / 上传的特殊设置） |
