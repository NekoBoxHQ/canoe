# 部署 Canoe Server

把 `canoe-server` 跑到一台公网机器上，前面用 Nginx 终止 TLS。

> 服务端**不转发流量**。它只做三件事：管账号、按账号发加密订阅、发客户端更新。
> 你的节点服务器和它**互不关联** —— 节点链接由你在面板里贴进来、勾给客户。

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

> 服务端**不需要**大带宽也不用高配 —— 它不转发流量，只在登舟和启航时
> 传几 KB 的 JSON。1 核 1G 跑几百个账号很轻松。

---

## 2. 安装（推荐）

**在服务器上一条命令，进管理菜单：**

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/NekoBoxHQ/canoe/main/canoe-server/deploy/canoe.sh)"
```

出来的就是管理菜单：

```
-------------------------------
         服务端管理脚本
-------------------------------
   1  安装 Canoe
   2  启动 Canoe
   3  停止 Canoe
   4  重启 Canoe
   5  查看状态
   6  修改配置
   7  升级 Canoe
   8  卸载 Canoe
   0  退出
-------------------------------
  请选择:
```

菜单**永远是这 9 行**，装没装过都一样。还没装的时候只有 `1` 走得通 ——
选 2~8 会提示「还没安装，先选 1」，不会打一堆报错。

选 `1` 进安装向导 —— **整台机器只需要这一个脚本**，代码和后续文件都是它自己去项目拉的。
装完这个菜单就落到 `/usr/local/bin/canoe`，以后直接：

```bash
canoe
```

> ⚠️ 一定要用 `bash -c "$(curl …)"` 这个写法，**不要**用 `curl … | bash`。
> 管道会把 stdin 占掉，菜单和向导的 `read` 就再也读不到你敲的字了 ——
> 表现是它卡在那里不动，或者读到你上一条命令的残留。

想先看一眼脚本再跑（第一次建议这么做）：

```bash
curl -fsSL https://raw.githubusercontent.com/NekoBoxHQ/canoe/main/canoe-server/deploy/canoe.sh -o canoe.sh
less canoe.sh
bash canoe.sh
```

### 只想装、不想进菜单

也可以直接跑安装向导，或者用参数跳过问答（适合脚本化）。

> ⚠️ **这条路只走第一次。** 机器上已经装好之后，install.sh 会检测到
> （`.venv` + `.env` + systemd 单元都在）然后**停下来**，把 `canoe`
> 那条命令指给你 —— 不会又从头问一遍「[1/3] 域名」。
> 确实要重走向导，加 `--reconfigure`（或环境变量 `CANOE_RECONFIGURE=1`）。

```bash
# 本地检出
git clone https://github.com/NekoBoxHQ/canoe.git && cd canoe
bash canoe-server/deploy/install.sh

# 或者不 clone：
bash -c "$(curl -fsSL https://raw.githubusercontent.com/NekoBoxHQ/canoe/main/canoe-server/deploy/install.sh)"
```

向导会**一项一项问你四件事**：

```
[1/4] 域名
      用来申请证书、拼客户端下载地址（PUBLIC_BASE_URL）。
      没有域名就留空 —— 那样证书只能自签，客户端也得走 IP。
      域名: canoe.s-ui.com

[2/4] 管理面板端口
      想只对自己开放的话，给面板另开一个口 —— 然后在防火墙/安全组里
      只放行你自己的 IP。那样客户端那个口就**不再响应 /panel**。
      面板端口（回车 = 和客户端同口）: 8899

[3/4] 管理员账号
      登录管理面板用它。用户名留空就是 admin。
      管理员用户名 [admin]: admin
      管理员密码（至少 8 位，回车 = 随机生成）:
      再输一遍:

[4/4] 证书
      1) Let's Encrypt 自动申请   推荐。要域名已解析到本机，且 80 端口空闲
      2) 自签证书                 自己用够了；浏览器会警告
      3) 我已有证书               你把证书和私钥文件给我
      4) 不加密                   前面已经有 HTTPS 反代了
      请选择 [1]:
```

**管理员密码就在这一步设**。不想现在想，一路回车也行 —— 那样会随机生成一个
并**打印在屏幕上**（同时留一份在 `/opt/canoe/ADMIN_PASSWORD.txt`）。
忘了密码随时可以改：

```bash
canoe config      # -> 3 改管理员账号
```

> **客户端口（58588）不问**：客户端把服务端地址写死在代码里了，
> 改了这个口，已经发出去的客户端当场全部失联 —— 那不是选项，是个陷阱。
> 所以它固定 58588，只在最后的确认页作为信息展示一次。
> 真要改得用 `--port` 显式指定（那是知道自己在干什么的人）。

也可以全用参数跳过问答（适合脚本化）：

```bash
bash deploy/install.sh --domain canoe.s-ui.com --port 58588 --cert-mode le
bash deploy/install.sh --domain x.com --cert-mode existing \
     --cert /root/ssl/fullchain.pem --key /root/ssl/privkey.pem
```

| 选项 | 说明 |
|---|---|
| `--domain NAME` | 域名。留空则用 IP 访问，证书只能自签 |
| `--port N` | 客户端口，默认 **58588**。**一般不要动** —— 客户端把地址写死在代码里，改了已发出的客户端全失联 |
| `--panel-port N` | **管理面板口**。留空 = 和客户端同口；填别的则另开口（见下） |
| `--cert-mode le` | Let's Encrypt 自动申请（需域名已解析、80 端口空闲）。**推荐** |
| `--cert-mode self` | 自签证书。浏览器会警告 |
| `--cert-mode existing` | 用你已有的证书，配 `--cert` / `--key`。**不自动续期** |
| `--cert-mode none` | 不加密（只建议放在别的反代后面） |
| `--email ADDR` | Let's Encrypt 注册邮箱（可选） |
| `--admin-user NAME` | 管理员用户名，默认 `admin` |
| `--admin-pass PASS` | 管理员密码。不给会问；都不给才随机生成（并打印出来） |

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
bash deploy/install.sh --domain x.com --port 58588 --panel-port 58589 --cert-mode le
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
${EDITOR:-vi} /opt/canoe/canoe-server/.env     # 改 PORT / TLS_CERT / TLS_KEY
systemctl restart canoe-api
```

`serve.py` 读 `.env` 决定监听什么。

> ⚠️ **客户端口不要随便改。** 客户端把那台服务器写死在 `58588` 上了，
> 改成别的口，所有已经发出去的客户端都会连不上 —— 除非你打算重发一版客户端。
> 面板口随便改，两者互不影响。
>
> 改配置也可以用管理脚本：`canoe config`（会问你要不要重启）。

---

## 3. 手动装

```bash
# 3.1 用户与代码
useradd -r -s /usr/sbin/nologin -d /opt/canoe canoe
mkdir -p /opt/canoe && chown canoe:canoe /opt/canoe
runuser -u canoe -- git clone <仓库> /opt/canoe

# 3.2 依赖（注意先装 canoe-core）
cd /opt/canoe/canoe-server
runuser -u canoe -- python3 -m venv .venv
runuser -u canoe -- .venv/bin/pip install -U pip
runuser -u canoe -- .venv/bin/pip install -e ../canoe-core -r requirements.txt

# 3.3 配置
runuser -u canoe -- cp .env.example .env
# 必须改的：
#   TICKET_SECRET   （生成：python3 -c "import secrets;print(secrets.token_urlsafe(48))"）
#   ADMIN_PASSWORD  （seed 用，装完再改也行）
#   PUBLIC_BASE_URL=https://api.canoe.example.com   ← 安装包下载地址靠它拼
runuser -u canoe -- $EDITOR .env

# 3.4 建库 + 管理员
runuser -u canoe -- .venv/bin/python seed.py

# 3.5 服务
cp deploy/canoe-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now canoe-api
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
cp deploy/nginx.canoe.conf /etc/nginx/sites-available/canoe
ln -sf /etc/nginx/sites-available/canoe /etc/nginx/sites-enabled/canoe
certbot --nginx -d canoe.s-ui.com
nginx -t && systemctl reload nginx
systemctl restart canoe-api
```

`nginx.canoe.conf` 里有三处**不能省**：

| 位置 | 为什么 |
|---|---|
| `/api/events` 的 `proxy_buffering off` | 不开的话 Nginx 会把推送攒在缓冲区，客户端收不到实时事件 |
| `/api/events` 的 `proxy_read_timeout 3600s` | 默认 60s 会把长连接掐断 |
| `/api/` 与 `/downloads/` 的 `client_max_body_size` | 不放宽的话上传大安装包会 413 |

---

## 5. 发布客户端新版本

**包挂在 GitHub Release 上，服务端自己去拉** —— 不用把 83MB 传到这台机器上。

```bash
# 1) 开发机：出包（打包前会先让 exe 自检一遍，跑不过就不出包）
python scripts/package_release.py
#    -> dist/Canoe-1.1.0-win64.zip  +  dist/Canoe-1.1.0-win64.zip.sha256

# 2) 开发机：把**两份**都挂到 GitHub Release（tag 形如 v1.1.0）
gh release create v1.1.0 dist/Canoe-1.1.0-win64.zip dist/Canoe-1.1.0-win64.zip.sha256
#    或者在网页的 Releases 页把它们拖进去

# 3) 服务器：一条命令拉下来发布
canoe release                 # 拉最新那个 Release
canoe release v1.1.0          # 拉指定 tag
```

> **为什么不让开发机直接传**：那条路断过两次连接，`/tmp` 里留下 46MB 的半截包，
> 而服务端是按**落盘的字节**算 sha256 的 —— 残包自洽，于是被当成合法版本发了
> 出去。走 Release 之后，摘要是开发机算好、当资产挂上去的，跟下载链路无关，
> 对不上就整个丢掉。

面板上也有同一个动作：「发布」页 → **「拉取最新轻舟」**（服务端自己去 GitHub 拉）。

发布后客户端点「更新」就会看到新版本。发布前可以先看一眼客户端到底会拿到什么：

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     https://api.canoe.example.com/api/admin/releases/latest-preview
```

---

## 6. 日常运维

装完就有个管理脚本 `canoe`（在 `/usr/local/bin/`）：

```bash
canoe          # 菜单：

                    ┌──────────────────────────────┐
                    │   1  安装 Canoe              │
                    │   2  启动 Canoe              │
                    │   3  停止 Canoe              │
                    │   4  重启 Canoe              │
                    │   5  查看状态                │
                    │   6  修改配置                │
                    │   7  升级 Canoe              │
                    │   8  卸载 Canoe              │
                    │   0  退出                    │
                    └──────────────────────────────┘
```

也可以直接用子命令（写进脚本、定时任务都行，菜单和子命令是同一批函数）：

```bash
canoe status      # 端口 / 健康检查 / 账号数 / 在线会话 / 最近日志
canoe config      # 改端口、换证书、编辑 .env（会问要不要重启）
canoe upgrade     # 拉代码 -> 更新依赖 -> 对齐数据库 -> 重启 -> 健康检查
canoe logs        # 跟随日志
canoe passwd      # 改管理员账号（用户名 / 密码）
canoe release     # 发布一个客户端安装包（不用开面板）
```

`canoe upgrade` 不用手动 `git pull` + `pip install` + 重启那一串，
它还会顺便跑一遍 `init_db()` 把新版本加的字段补上。

### 发一版客户端

客户端安装包在开发机上打，挂到 **GitHub Release**，服务器这边一条命令拉下来发：

```bash
# 开发机：出包（打包前会先让 exe 自检一遍，跑不过就不出包）
python scripts/package_release.py
#   -> dist/Canoe-1.0.31-win64.zip + dist/Canoe-1.0.31-win64.zip.sha256
#   两份一起挂到 Release（tag 形如 v1.0.31）

# 服务器：
canoe release                 # 拉最新那个 Release
canoe release v1.0.31         # 拉指定 tag
```

版本号默认从资产文件名里抠（`Canoe-1.0.31-win64.zip` → `1.0.31`），
抠不出来就得在面板/接口里显式给 —— 不会静默发成 `0.0.0`。

```
  已发布    1.0.31    （库里一共 3 个版本）
  上一版    1.0.30
  来源      GitHub NekoBoxHQ/canoe   最新那个 Release
  文件      Canoe-1.0.31-win64.zip
  大小      83.1 MB
  sha256    22b0a426...
  下载      https://canoe.s-ui.com:58588/downloads/Canoe-1.0.31-win64.zip
```

发布后：

- `GET /api/client/latest` 立刻指向新版本；
- 在线的客户端会收到 `release` 推送，结果框提示有新版本；
- 「发布」页里也能看到它。

常用参数：

```bash
canoe release --list          # 看已经发过哪些
canoe release v1.0.31         # 拉指定 tag（要重发某一版时用）
```

> 面板上也有同一个动作：「发布」页 →「拉取最新轻舟」。
> 两条路走的是同一段代码（`services/updates.pull_from_github`），
> 不会出现"一边改了另一边忘"。
>
> ⚠ 以前那条"scp 上来再 `canoe release /tmp/xxx.zip`"的路**已经废了**，
> 现在只认 GitHub Release（原因见上面 §5）。应急时可以用
> `python release.py <路径或URL> --sha256 <摘要>` 直接发本机的包，
> 但那条路不再由 `canoe` 暴露。

### 手动做（脚本不在或想自己来）

```bash
systemctl status canoe-api
journalctl -u canoe-api -f                    # 看日志

# 备份（SQLite 就一个文件 + 上传的安装包）
tar czf canoe-backup-$(date +%F).tgz \
    -C /opt/canoe/canoe-server data releases

# 升级
cd /opt/canoe && runuser -u canoe -- git pull
runuser -u canoe -- canoe-server/.venv/bin/pip install -e canoe-core -r canoe-server/requirements.txt
systemctl restart canoe-api
```

> 改客户端口（`PORT`）前想清楚：客户端把服务器地址写死在 `58588` 了，
> 改完所有已发出的客户端都会连不上。管理脚本在这个操作上会拦你一下。

---

## 7. 排查

| 现象 | 排查 |
|---|---|
| 客户端提示"连不上渡口" | `curl https://api.canoe.example.com/api/health`；证书域名对不对 |
| 推送时有时无 | **开了多 worker**（见本文开头）。`systemctl cat canoe-api` 看 `--workers` |
| 推送完全不来 | Nginx 少了 `proxy_buffering off`；或客户端没带 Authorization 头 |
| 更新按钮报 404 | 还没发布过任何版本：`/api/admin/releases` 是空的 |
| 下载安装包 404 | 登记的 `filename` 和 `releases/` 里的文件名对不上 |
| 「拉取最新轻舟」拉不下来 | 服务器连不上 GitHub（`curl -I https://api.github.com`），或那个 Release 上**没挂 zip 或 `.sha256`**。原因在 `journalctl -u canoe-api -n 50` 里 |
| 上传安装包 413（备用通道） | 面板按钮已经不走这条路了；真要用 `POST /api/admin/releases/upload` 时才会碰上。要么 Nginx `client_max_body_size` 太小，要么超了 `MAX_RELEASE_MB` |
| 客户端说"没有下发订阅" | 面板「用户」里那一行的「订阅」栏是空的。贴上节点链接即可 |
| 客户端说"订阅解密失败" | 会话换了（服务端重启会换 TICKET_SECRET 吗？不会；多半是令牌被顶掉了）—— 重新登舟 |
| 清空了订阅，客户端还在跑 | 它最迟下一次心跳（默认 30s）会发现；没挂推送时会慢一点。急的话在面板踢一下 |
| 封禁了但客户端还在线 | 看 `pushed` 字段；为 0 说明客户端没挂着推送，会等心跳超时 |

---

## 8. 文件说明

| 文件 | 用途 |
|---|---|
| `install.sh` | 一键安装（Debian/Ubuntu），装完会把 `canoe` 放进 PATH |
| `canoe.sh` | 管理脚本：菜单 + 子命令（启停 / 状态 / 配置 / 升级 / 卸载） |
| `canoe-api.service` | systemd unit（已设单 worker） |
| `nginx.canoe.conf` | Nginx 站点示例（含 SSE / 上传的特殊设置） |
| `test_canoe_sh.sh` | 管理脚本的测试（44 项，不需要 root/systemd） |
