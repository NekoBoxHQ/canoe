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

## 2. 安装向导（推荐）

```bash
sudo bash canoe-server/deploy/install.sh
```

不带参数会**一项一项问你三件事**：

```
[1/3] 域名
      用来申请证书、拼客户端下载地址。没有域名就留空（证书只能自签）。
      域名: canoe.s-ui.com

[2/3] Web 端口
      管理面板和 API 共用这一个端口，客户端也用它取更新和订阅。
      Web 端口 [58588]:

[3/3] 证书
      1) Let's Encrypt 自动申请   推荐。要域名已解析到本机，且 80 端口空闲
      2) 自签证书                 自己用够了；浏览器会警告
      3) 我已有证书               你把证书和私钥文件给我
      4) 不加密                   前面已经有 HTTPS 反代了
      请选择 [1]:
```

也可以全用参数跳过问答（适合脚本化）：

```bash
sudo bash deploy/install.sh --domain canoe.s-ui.com --port 58588 --cert-mode le
sudo bash deploy/install.sh --domain x.com --cert-mode existing \
     --cert /root/ssl/fullchain.pem --key /root/ssl/privkey.pem
```

| 选项 | 说明 |
|---|---|
| `--domain NAME` | 域名。留空则用 IP 访问，证书只能自签 |
| `--port N` | **客户端口**。客户端固定拿它取更新和订阅，对所有人开放。默认 **58588** |
| `--panel-port N` | **管理面板口**。留空 = 和客户端同口；填别的则另开口（见下） |
| `--cert-mode le` | Let's Encrypt 自动申请（需域名已解析、80 端口空闲）。**推荐** |
| `--cert-mode self` | 自签证书。浏览器会警告 |
| `--cert-mode existing` | 用你已有的证书，配 `--cert` / `--key`。**不自动续期** |
| `--cert-mode none` | 不加密（只建议放在别的反代后面） |
| `--email ADDR` | Let's Encrypt 注册邮箱（可选） |

> `--https` 是 `--cert-mode` 的旧名字，仍然能用。

选 **3 已有证书** 时会先替你验一遍：文件在不在、是不是合法 X.509、
**证书和私钥是不是同一套**（modulus 比对），对不上直接报错，
不会等到服务起不来才发现。

脚本会：建 `canoe` 用户 → 放代码到 `/opt/canoe` → 建 venv 装依赖 →
准备证书 → 生成 `.env`（含随机 `TICKET_SECRET` 和管理员密码）→
建库 + 种子 → 装 systemd → 自检。

**默认是直连模式**：uvicorn 自己监听在指定端口上做 TLS，**不需要 Nginx**。
（想用 Nginx 前置的话证书方式选 4，再按第 4 节配。）

跑完检查：

```bash
systemctl status canoe-api
curl -s https://canoe.s-ui.com:58588/api/health
```

打开面板：`https://canoe.s-ui.com:58588/panel`（管理员密码在 `/opt/canoe/ADMIN_PASSWORD.txt`）。

### 证书放在哪

服务以 `canoe` 用户跑，**读不了 root-only 的私钥**。所以不管哪种方式，
证书最后都归拢到 `/etc/canoe/live/`（属主 `root:canoe`、权限 640）：

```
/etc/canoe/live/fullchain.pem
/etc/canoe/live/privkey.pem
```

`.env` 里指的就是这两个。certbot 签出来的
`/etc/letsencrypt/live/<域名>/privkey.pem` 默认是 `0600 root:root`，
直接写进 `.env` 服务会起不来 —— 这是踩过的坑。

Let's Encrypt 续期后会由 `/etc/letsencrypt/renewal-hooks/deploy/canoe.sh`
自动重发布证书并重启服务，不用你管。**已有证书模式不会自动续期**，
到期前自己换掉再重跑脚本。

### 把管理面板挪到另一个端口（可选）

客户端口 `--port` 必须对所有用户开放，管理面板没必要。给面板另开一个口，
你就能在安全组/防火墙里只放行自己的 IP：

```bash
sudo bash deploy/install.sh --domain x.com --port 58588 --panel-port 58589 --cert-mode le
```

效果：

| 端口 | 谁能连 | `/panel` | `/api/*` |
|---|---|---|---|
| 58588（客户端口） | 所有用户 | **404** | ✅ |
| 58589（面板口） | 只放行你自己 | ✅ | ✅（面板自己要调，同源不用 CORS） |

**两个端口是同一个进程在监听**，不是两个进程 —— 推送中心是进程内的内存结构，
拆成两个进程的话，你在面板上点的「踢下线」就传不到客户端的长连接上。
这一点有专门的跨端口测试守着。

改完 `.env` 里的 `PANEL_PORT` 后 `systemctl restart canoe-api` 即可。
`PANEL_PORT=0` 表示回到与客户端同口。

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
