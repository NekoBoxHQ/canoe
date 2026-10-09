#!/usr/bin/env bash
#
# 轻舟 / Canoe Server —— 安装向导（Debian / Ubuntu）
#
#   sudo bash deploy/install.sh                 # 一路问下来
#   sudo bash deploy/install.sh --domain canoe.s-ui.com --port 58588 --cert-mode le
#
# 会问你三件事：**域名 / Web 端口 / 证书**。
#
# 默认是**直连模式**：uvicorn 自己监听在指定端口上做 HTTPS，不需要 Nginx。
# （想用 Nginx 前置见 deploy/nginx.canoe.conf，证书方式选 4 或装完把
#   TLS_CERT/TLS_KEY 清空即可。）
#
# 幂等：重复跑不会覆盖已有的 .env / 数据库 / 证书。
set -euo pipefail

APP_USER="canoe"
APP_DIR="/opt/canoe"
SERVER_DIR="$APP_DIR/canoe-server"
CERT_DIR="/etc/canoe"
LIVE_DIR="$CERT_DIR/live"
PANEL_PATH="/panel"

DOMAIN=""
PORT=""
PANEL_PORT=""      # 空 = 和 PORT 同口
CERT_MODE=""       # le | self | existing | none
CERT_FILE=""
KEY_FILE=""
EMAIL=""
REPO_URL=""
DO_SEED=1

log()  { printf '\033[1;36m[*]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
轻舟 / Canoe Server —— 安装向导（Debian / Ubuntu）

  sudo bash deploy/install.sh
  sudo bash deploy/install.sh --domain canoe.s-ui.com --port 58588 --cert-mode le
  sudo bash deploy/install.sh --domain x.com --port 58588 --panel-port 58589

不带参数会一项一项问你：域名 / 客户端口 / 面板端口 / 证书。

默认是**直连模式**：uvicorn 自己监听在指定端口上做 HTTPS，不需要 Nginx。

幂等：重复跑不会覆盖已有的 .env / 数据库 / 证书。

选项：
  --domain NAME         域名（用于证书和客户端下载地址；没有就留空）
  --port N              **客户端口**，客户端固定拿它取更新和订阅（默认 58588）
  --panel-port N        **管理面板口**。留空 = 和客户端同口；
                        填别的则另开一个口，客户端口不再响应 /panel。
                        两个口由同一个进程监听，推送照常互通。
  --cert-mode MODE      证书来源：
                          le       = Let's Encrypt 自动申请（需域名已解析、80 空闲）
                          self     = 自签证书（浏览器会警告）
                          existing = 用我已有的证书（配 --cert / --key）
                          none     = 不加密（前面已有 HTTPS 反代）
  --cert PATH           已有证书文件（fullchain，.pem/.crt）
  --key PATH            已有私钥文件（.key/.pem）
  --email ADDR          Let's Encrypt 注册邮箱（可选）
  --repo URL            从 git 拉代码（默认用脚本所在的这份代码）
  --no-seed             跳过种子数据
  -h, --help            看这个

  --https 是 --cert-mode 的旧名字，仍然能用。
EOF
}

# ---------------------------------------------------------------------------
# 0. 解析参数
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)               DOMAIN="${2:-}"; shift 2 ;;
        --port)                 PORT="${2:-}"; shift 2 ;;
        --panel-port)           PANEL_PORT="${2:-}"; shift 2 ;;
        --cert-mode|--https)    CERT_MODE="${2:-}"; shift 2 ;;
        --cert)                 CERT_FILE="${2:-}"; shift 2 ;;
        --key)                  KEY_FILE="${2:-}"; shift 2 ;;
        --email)                EMAIL="${2:-}"; shift 2 ;;
        --repo)                 REPO_URL="${2:-}"; shift 2 ;;
        --no-seed)              DO_SEED=0; shift ;;
        -h|--help)              usage; exit 0 ;;
        -*) die "未知选项：$1（-h 看用法）" ;;
        *)  [[ -z "$DOMAIN" ]] || die "域名只能给一个：$1"
            DOMAIN="$1"; shift ;;
    esac
done

# ---- 参数先校验：跟有没有 root 无关，早点报错 ----
[[ -z "$PORT" ]] || [[ "$PORT" =~ ^[0-9]+$ ]] || die "端口必须是数字：$PORT"
[[ -z "$PANEL_PORT" ]] || [[ "$PANEL_PORT" =~ ^[0-9]+$ ]] || die "面板端口必须是数字：$PANEL_PORT"
if [[ -n "$CERT_MODE" ]]; then
    case "$CERT_MODE" in
        le|self|existing|none) ;;
        cert|own|file) CERT_MODE="existing" ;;
        *) die "证书方式只能是 le / self / existing / none（现在给的是 $CERT_MODE）" ;;
    esac
fi
if [[ "$CERT_MODE" == "existing" && ( -z "$CERT_FILE" || -z "$KEY_FILE" ) ]]; then
    die "用已有证书时要一起给：--cert <证书> --key <私钥>"
fi

[[ $EUID -eq 0 ]] || die "请用 root 跑：sudo bash deploy/install.sh（-h 看用法）"

# ---------------------------------------------------------------------------
# 0.1 交互向导：域名 / Web 端口 / 证书
# ---------------------------------------------------------------------------
if [[ -t 0 ]]; then
    printf '\n  轻舟 / Canoe Server 安装向导\n  ─────────────────────────────\n  直接回车 = 用方括号里的默认值\n\n'

    if [[ -z "$DOMAIN" ]]; then
        cat <<'TXT'
[1/3] 域名
      用来申请证书、拼客户端下载地址（PUBLIC_BASE_URL）。
      没有域名就留空 —— 那样证书只能自签，客户端也得走 IP。
TXT
        read -rp "      域名: " DOMAIN || true
    fi

    if [[ -z "$PORT" ]]; then
        cat <<'TXT'
[2/4] 客户端口
      客户端固定拿这个口取更新和订阅，**必须对所有用户开放**。
TXT
        read -rp "      客户端口 [58588]: " PORT || true
        PORT="${PORT:-58588}"
    fi

    if [[ -z "$PANEL_PORT" ]]; then
        cat <<'TXT'
[3/4] 管理面板端口
      想只对自己开放的话，给面板另开一个口 —— 然后在防火墙/安全组里
      只放行你自己的 IP。那样客户端那个口就**不再响应 /panel**。
      两个口是同一个进程在听，推送照常互通。
TXT
        read -rp "      面板端口（回车 = 和客户端同口）: " PANEL_PORT || true
    fi

    if [[ -z "$CERT_MODE" ]]; then
        cat <<'TXT'
[4/4] 证书
      1) Let's Encrypt 自动申请   推荐。要域名已解析到本机，且 80 端口空闲
      2) 自签证书                 自己用够了；浏览器会警告
      3) 我已有证书               你把证书和私钥文件给我
      4) 不加密                   前面已经有 HTTPS 反代了
TXT
        read -rp "      请选择 [1]: " ans || true
        case "${ans:-1}" in
            1) CERT_MODE="le" ;;
            2) CERT_MODE="self" ;;
            3) CERT_MODE="existing" ;;
            4) CERT_MODE="none" ;;
            *) die "只能选 1-4" ;;
        esac
    fi

    if [[ "$CERT_MODE" == "existing" ]]; then
        if [[ -z "$CERT_FILE" ]]; then
            read -rp "      证书文件（fullchain，.pem/.crt）: " CERT_FILE || true
        fi
        if [[ -z "$KEY_FILE" ]]; then
            read -rp "      私钥文件（.key/.pem）: " KEY_FILE || true
        fi
    fi
    echo
fi

PORT="${PORT:-58588}"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "端口必须是数字：$PORT"

# ---- 默认值兜底 ----
if [[ -z "$CERT_MODE" ]]; then
    [[ -n "$DOMAIN" ]] && CERT_MODE="le" || CERT_MODE="self"
fi
if [[ "$CERT_MODE" == "le" && -z "$DOMAIN" ]]; then
    warn "Let's Encrypt 得有域名，自动改成自签证书"
    CERT_MODE="self"
fi
# 自签也要有个名字写进证书，不然 SAN 是空的
if [[ "$CERT_MODE" == "self" && -z "$DOMAIN" ]]; then
    DOMAIN="$(hostname -f 2>/dev/null || hostname)"
    warn "没给域名，自签证书用本机名 $DOMAIN"
fi

# ---- 已有证书：先验一遍，别等到服务起不来才发现 ----
if [[ "$CERT_MODE" == "existing" ]]; then
    [[ -f "$CERT_FILE" ]] || die "找不到证书文件：$CERT_FILE"
    [[ -f "$KEY_FILE"  ]] || die "找不到私钥文件：$KEY_FILE"
    openssl x509 -in "$CERT_FILE" -noout >/dev/null 2>&1 \
        || die "这不是一个合法的 X.509 证书：$CERT_FILE"
    # 证书和私钥是不是一对（很常见的错配）
    mc="$(openssl x509 -noout -modulus -in "$CERT_FILE" 2>/dev/null | openssl md5)"
    mk="$(openssl rsa  -noout -modulus -in "$KEY_FILE"  2>/dev/null | openssl md5)"
    if [[ -n "$mc" && -n "$mk" && "$mc" != "$mk" ]]; then
        die "证书和私钥对不上（modulus 不一致）——确认一下是不是同一套"
    fi
    log "已有证书检查通过：$(openssl x509 -noout -subject -in "$CERT_FILE" 2>/dev/null | sed 's/^subject=//')"
fi

SCHEME="https"; [[ "$CERT_MODE" == "none" ]] && SCHEME="http"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

CERT_DESC="$CERT_MODE"
[[ "$CERT_MODE" == "existing" ]] && CERT_DESC="已有证书（$CERT_FILE）"
[[ "$CERT_MODE" == "le" ]]       && CERT_DESC="Let's Encrypt"
[[ "$CERT_MODE" == "self" ]]     && CERT_DESC="自签证书"

HOST_DISPLAY="${DOMAIN:-<本机IP>}"
if [[ -z "$PANEL_PORT" || "$PANEL_PORT" == "$PORT" ]]; then
    PANEL_DESC="与客户端同口（$SCHEME://$HOST_DISPLAY:$PORT$PANEL_PATH）"
else
    PANEL_DESC="$SCHEME://$HOST_DISPLAY:$PANEL_PORT （客户端口不响应 $PANEL_PATH）"
fi

cat <<EOF

  轻舟 / Canoe Server 安装
  ────────────────────────────────
  域名         ${DOMAIN:-<无，用 IP 访问>}
  客户端口     $PORT          ← 更新 / 订阅 / API，对所有人开放
  管理面板     $PANEL_DESC
  证书         $CERT_DESC

EOF
if [[ -t 0 ]]; then read -rp "确认开始？[Y/n] " ok; [[ "${ok:-y}" =~ ^[Yy]?$ ]] || exit 0; fi

# ---------------------------------------------------------------------------
# 1. 系统依赖
# ---------------------------------------------------------------------------
log "安装系统依赖…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# git：--repo 拉代码要用；用户自己 clone 的话也总得有
PKGS=(python3 python3-venv python3-pip openssl rsync curl git ca-certificates)
[[ "$CERT_MODE" == "le" ]] && PKGS+=(certbot)
apt-get install -y -qq "${PKGS[@]}"

# ---------------------------------------------------------------------------
# 2. 用户与代码
# ---------------------------------------------------------------------------
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    log "创建用户 $APP_USER"
    useradd -r -s /usr/sbin/nologin -d "$APP_DIR" "$APP_USER"
fi
mkdir -p "$APP_DIR"; chown "$APP_USER:$APP_USER" "$APP_DIR"

if [[ -n "$REPO_URL" ]]; then
    if [[ -d "$APP_DIR/.git" ]]; then
        log "拉取最新代码"; sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
    else
        log "克隆仓库"; sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
    fi
else
    log "从 $REPO_ROOT 同步代码到 $APP_DIR"
    rsync -a --delete \
        --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
        --exclude 'canoe-server/data' --exclude 'canoe-server/releases' \
        --exclude 'canoe-server/panel/node_modules' \
        --exclude 'canoe-client/dist' --exclude 'canoe-client/build' \
        "$REPO_ROOT/" "$APP_DIR/"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ---------------------------------------------------------------------------
# 3. venv 与依赖
# ---------------------------------------------------------------------------
log "建立虚拟环境并安装依赖…"
[[ -d "$SERVER_DIR/.venv" ]] || sudo -u "$APP_USER" python3 -m venv "$SERVER_DIR/.venv"
PIP="$SERVER_DIR/.venv/bin/pip"
# setuptools/wheel 要显式装：Debian 的 python3-venv 不保证带 setuptools，
# 而 canoe-core 是 pyproject + setuptools 后端的可编辑安装，缺了会失败。
sudo -u "$APP_USER" "$PIP" install -q -U pip setuptools wheel
sudo -u "$APP_USER" "$PIP" install -q -e "$APP_DIR/canoe-core"
sudo -u "$APP_USER" "$PIP" install -q -r "$SERVER_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 4. 证书
# ---------------------------------------------------------------------------
TLS_CERT=""
TLS_KEY=""
mkdir -p "$CERT_DIR"

# 服务以 canoe 用户跑，读不了 root-only 的私钥。
# 所以证书统一**拷到 /etc/canoe/live/**，属主 root:canoe、权限 640 ——
# certbot 的 /etc/letsencrypt/live/*/privkey.pem 默认是 0600 root:root，
# 直接把路径写进 .env 的话服务起不来（Permission denied）。
publish_certs() {
    mkdir -p "$LIVE_DIR"
    cp -Lf "$1" "$LIVE_DIR/fullchain.pem"
    cp -Lf "$2" "$LIVE_DIR/privkey.pem"
    chown -R root:"$APP_USER" "$LIVE_DIR"
    chmod 750 "$LIVE_DIR"
    chmod 640 "$LIVE_DIR/fullchain.pem" "$LIVE_DIR/privkey.pem"
}

case "$CERT_MODE" in
  le)
    log "申请 Let's Encrypt 证书（$DOMAIN）…"
    if [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
        log "已有证书，跳过申请"
    else
        ARGS=(certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos
              --keep-until-expiring)
        if [[ -n "$EMAIL" ]]; then ARGS+=(--email "$EMAIL")
        else ARGS+=(--register-unsafely-without-email); fi
        certbot "${ARGS[@]}" || die "证书申请失败。检查：域名是否已解析到本机、80 端口是否被占用。"
    fi

    publish_certs "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" \
                  "/etc/letsencrypt/live/$DOMAIN/privkey.pem"
    TLS_CERT="$LIVE_DIR/fullchain.pem"
    TLS_KEY="$LIVE_DIR/privkey.pem"

    # 续期后：重新发布一次证书（续期会换新的 privkey 权限）+ 重启服务
    mkdir -p /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/canoe.sh <<HOOK
#!/bin/sh
# 由 canoe-server/deploy/install.sh 生成
cp -Lf /etc/letsencrypt/live/$DOMAIN/fullchain.pem $LIVE_DIR/fullchain.pem
cp -Lf /etc/letsencrypt/live/$DOMAIN/privkey.pem  $LIVE_DIR/privkey.pem
chown root:$APP_USER \$LIVE_DIR/*.pem
chmod 640 \$LIVE_DIR/*.pem
systemctl restart canoe-api
HOOK
    chmod +x /etc/letsencrypt/renewal-hooks/deploy/canoe.sh
    log "续期 hook 已装好（续期后会自动重发布证书并重启服务）"
    ;;

  self)
    log "生成自签证书…"
    if [[ -f "$LIVE_DIR/privkey.pem" ]]; then
        log "已有证书，跳过"
    else
        CN="${DOMAIN:-localhost}"
        SAN="DNS:${CN},IP:127.0.0.1"
        # 顺手把本机内网 IP 也塞进 SAN，方便用 IP 直连
        LANIP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        [[ -n "$LANIP" ]] && SAN="$SAN,IP:$LANIP"
        tmp="$(mktemp -d)"
        openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
            -keyout "$tmp/key.pem" -out "$tmp/cert.pem" \
            -subj "//CN=$CN" -addext "subjectAltName=$SAN" >/dev/null 2>&1
        publish_certs "$tmp/cert.pem" "$tmp/key.pem"
        rm -rf "$tmp"
        warn "自签证书：浏览器会提示不安全；客户端要信任 $LIVE_DIR/fullchain.pem 才能连"
    fi
    TLS_CERT="$LIVE_DIR/fullchain.pem"
    TLS_KEY="$LIVE_DIR/privkey.pem"
    ;;

  existing)
    # 你自己买的/别处签的证书。只是把它**拷成服务读得到的权限**，
    # 内容原样不动 —— 续期由你自己负责（我们不碰你的证书来源）。
    log "使用已有证书…"
    publish_certs "$CERT_FILE" "$KEY_FILE"
    TLS_CERT="$LIVE_DIR/fullchain.pem"
    TLS_KEY="$LIVE_DIR/privkey.pem"
    log "证书已就位：$(openssl x509 -noout -subject -in "$LIVE_DIR/fullchain.pem" 2>/dev/null | sed 's/^subject=//')"
    warn "已有证书不会自动续期 —— 到期前记得换掉 $CERT_FILE 再重跑本脚本（或在 $LIVE_DIR 里直接替换后 systemctl restart canoe-api）"
    ;;

  none)
    warn "不加密模式：请确保前面有别的 HTTPS 反代，否则令牌是明文传的"
    ;;
esac

# 顺带把 certbot 报错日志留一份，排查证书问题时有用
mkdir -p /var/log/letsencrypt 2>/dev/null || true

# ---------------------------------------------------------------------------
# 5. .env
# ---------------------------------------------------------------------------
ENV_FILE="$SERVER_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    log ".env 已存在，保留不动（要改端口/证书请手动编辑 $ENV_FILE）"
else
    log "生成 .env"
    read -r -d '' PY_CMD <<'PY' || true
import secrets
print(secrets.token_urlsafe(48))
print(secrets.token_urlsafe(18))
PY
    mapfile -t SECRETS < <(sudo -u "$APP_USER" "$SERVER_DIR/.venv/bin/python" -c "$PY_CMD")
    TICKET_SECRET="${SECRETS[0]}"
    ADMIN_PASSWORD="${SECRETS[1]}"
    BASE="${SCHEME}://${DOMAIN:-127.0.0.1}"
    [[ "$PORT" != "443" && "$PORT" != "80" ]] && BASE="$BASE:$PORT"

    cat > "$ENV_FILE" <<EOF
# 由 deploy/install.sh 生成于 $(date -Is) —— 改端口/证书改这里就行
DEBUG=false
HOST=0.0.0.0
# 客户端固定拿这个口取更新和订阅
PORT=$PORT
# 0 = 和 PORT 同口；填了别的就另开一个面板口
PANEL_PORT=${PANEL_PORT:-0}
TLS_CERT=$TLS_CERT
TLS_KEY=$TLS_KEY

DATABASE_URL=sqlite:///./data/canoe.db

TICKET_SECRET=$TICKET_SECRET
TOKEN_TTL=86400
ENTRY_TICKET_TTL=300

HEARTBEAT_INTERVAL=30
ONLINE_TIMEOUT=90
DEFAULT_EXPIRE_DAYS=30
DEFAULT_MAX_DEVICES=3

ADMIN_USERNAME=admin
ADMIN_PASSWORD=$ADMIN_PASSWORD

# 安装包下载地址靠它拼（客户端「更新」按钮指向这里）
PUBLIC_BASE_URL=$BASE
MAX_RELEASE_MB=300

# 推送
SSE_KEEPALIVE=20
SSE_MAX_CONNECTIONS=2000
SSE_MAX_PER_USER=5

# 中转层 —— 上一版模型的遗留，当前客户端链路不走它（订阅模式下
# 服务端只发订阅、不转发流量）。保留这几行是为了不让老的 relay 配置
# 读不到值；不用管它们。
RELAY_BEHIND_NGINX=true
RELAY_INTERNAL_BASE=20000
RELAY_RELOAD_HOOK=
EOF
    chown "$APP_USER:$APP_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"

    cat > "$APP_DIR/ADMIN_PASSWORD.txt" <<EOF
初始管理员账号（登录后立刻改掉，然后删掉这个文件）

  面板地址: $BASE/panel
  用户名:   admin
  密码:     $ADMIN_PASSWORD

改密码：面板 -> 用户 -> 编辑，或调 PATCH /api/admin/users/1
EOF
    chmod 600 "$APP_DIR/ADMIN_PASSWORD.txt"
    warn "管理员密码写在 $APP_DIR/ADMIN_PASSWORD.txt —— 登录后请改掉并删除"
fi

mkdir -p "$SERVER_DIR/data" "$SERVER_DIR/releases"
chown -R "$APP_USER:$APP_USER" "$SERVER_DIR/data" "$SERVER_DIR/releases"

# ---------------------------------------------------------------------------
# 6. 建库 + 种子
# ---------------------------------------------------------------------------
if [[ "$DO_SEED" == "1" ]]; then
    log "初始化数据库…"
    sudo -u "$APP_USER" bash -c "cd '$SERVER_DIR' && .venv/bin/python seed.py"
fi

# ---------------------------------------------------------------------------
# 7. systemd
# ---------------------------------------------------------------------------
log "安装 systemd 服务…"
install -m 644 "$SERVER_DIR/deploy/canoe-api.service" /etc/systemd/system/canoe-api.service
systemctl daemon-reload
systemctl enable canoe-api >/dev/null
systemctl restart canoe-api
sleep 3
systemctl is-active --quiet canoe-api \
    && log "canoe-api 已在跑" \
    || { journalctl -u canoe-api -n 40 --no-pager; die "canoe-api 起不来，日志见上"; }

# 顺带装管理脚本：以后启停 / 看状态 / 改配置 / 升级都用 `sudo canoe`
if [[ -f "$SERVER_DIR/deploy/canoe.sh" ]]; then
    install -m 755 "$SERVER_DIR/deploy/canoe.sh" /usr/local/bin/canoe
    log "管理脚本已安装：sudo canoe"
fi

# ---------------------------------------------------------------------------
# 8. 防火墙提示
# ---------------------------------------------------------------------------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    log "放行端口 $PORT"
    ufw allow "$PORT"/tcp >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
# 9. 自检
# ---------------------------------------------------------------------------
log "自检…"
sleep 1
HOSTPART="127.0.0.1"
if [[ "$CERT_MODE" == "none" ]]; then
    curl -fsS "http://$HOSTPART:$PORT/api/health" >/dev/null && log "本机健康检查通过" || warn "健康检查没过，看 journalctl -u canoe-api"
else
    curl -fsSk "https://$HOSTPART:$PORT/api/health" >/dev/null && log "本机健康检查通过（自签证书用 -k）" || warn "健康检查没过，看 journalctl -u canoe-api"
fi

BASE_SHOWN="${SCHEME}://${DOMAIN:-<本机IP>}"
[[ "$PORT" != "443" && "$PORT" != "80" ]] && BASE_SHOWN="$BASE_SHOWN:$PORT"

if [[ -z "$PANEL_PORT" || "$PANEL_PORT" == "$PORT" ]]; then
    PANEL_URL="$BASE_SHOWN$PANEL_PATH"
else
    PANEL_URL="${SCHEME}://${DOMAIN:-<本机IP>}:$PANEL_PORT$PANEL_PATH"
fi

cat <<EOF

============================================================
  装好了
============================================================

  管理面板       : $PANEL_URL
  客户端订阅接口 : $BASE_SHOWN/api/subscription
  客户端更新接口 : $BASE_SHOWN/api/client/latest
  健康检查       : $BASE_SHOWN/api/health

  管理脚本 : sudo canoe        （菜单：启动/停止/状态/配置/升级/卸载）
  日志     : sudo canoe logs
  配置     : $SERVER_DIR/.env
  管理员   : 见 $APP_DIR/ADMIN_PASSWORD.txt

  下一步：
    1. 打开面板登录，改掉管理员密码（也可以 sudo canoe passwd）
    2. 面板「用户」里给账号配**订阅** —— 点那行的「订阅」按钮，
       把节点链接一行一个贴进去（ss:// vmess:// vless:// trojan://）。
       清零 = 停止对该账号分发，客户端会就地销毁本地订阅。
    3. 面板「发布」里上传客户端安装包 —— 客户端点「更新」就能看到。
       （客户端地址写死在 $BASE_SHOWN，不用在客户端配任何东西）

EOF

if [[ -n "$PANEL_PORT" && "$PANEL_PORT" != "$PORT" ]]; then
cat <<EOF
  ⚠ 面板另开了 $PANEL_PORT 口。记得在防火墙/安全组里：
       · $PORT      对所有用户开放（客户端要用）
       · $PANEL_PORT 只放行你自己的 IP
     本机 ufw 已自动放行；云厂商的安全组要你自己加。

EOF
else
cat <<EOF
  ⚠ 面板和客户端共用一个口，所以 $PANEL_PATH 是公网可访问的。
     想只对自己开放，重跑本脚本时把面板端口填成别的（例如 58589）。

EOF
fi

cat <<EOF
  ⚠ 服务端只开 1 个 worker（推送是进程内的，多 worker 会收不到）。
    详见 deploy/README.md 开头。

EOF
