#!/usr/bin/env bash
#
# 轻舟 / Canoe Server —— 一键安装（Debian / Ubuntu）
#
#   sudo bash deploy/install.sh canoe.s-ui.com --port 58588 --https le
#
# 不带参数就直接回车，会一项一项问你。
#
# 默认是**直连模式**：uvicorn 自己监听在指定端口上做 HTTPS，不需要 Nginx。
# （想用 Nginx 前置见 deploy/nginx.canoe.conf，装完把 TLS_CERT/TLS_KEY 清空即可。）
#
# 幂等：重复跑不会覆盖已有的 .env / 数据库 / 证书。
set -euo pipefail

APP_USER="canoe"
APP_DIR="/opt/canoe"
SERVER_DIR="$APP_DIR/canoe-server"
CERT_DIR="/etc/canoe"

DOMAIN=""
PORT=""
HTTPS_MODE=""      # le | self | none
EMAIL=""
REPO_URL=""
DO_SEED=1

log()  { printf '\033[1;36m[*]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
轻舟 / Canoe Server —— 一键安装（Debian / Ubuntu）

  sudo bash deploy/install.sh canoe.s-ui.com --port 58588 --https le

不带参数就直接回车，会一项一项问你。

默认是**直连模式**：uvicorn 自己监听在指定端口上做 HTTPS，不需要 Nginx。
（想用 Nginx 前置见 deploy/nginx.canoe.conf，装完把 TLS_CERT/TLS_KEY 留空即可。）

幂等：重复跑不会覆盖已有的 .env / 数据库 / 证书。

选项：
  --port N              对外端口（默认 58588）
  --https le|self|none  证书来源（默认：有域名用 le，没有用 self）
                          le   = Let's Encrypt（需要域名已解析到本机、80 端口空闲）
                          self = 自签证书（浏览器会警告，客户端需要信任它）
                          none = 不加密（只建议放在别的反代后面）
  --email ADDR          Let's Encrypt 注册邮箱（可选）
  --repo URL            从 git 拉代码（默认用脚本所在的这份代码）
  --no-seed             跳过种子数据
  -h, --help            看这个
EOF
}

# ---------------------------------------------------------------------------
# 0. 解析参数
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)   PORT="${2:-}"; shift 2 ;;
        --https)  HTTPS_MODE="${2:-}"; shift 2 ;;
        --email)  EMAIL="${2:-}"; shift 2 ;;
        --repo)   REPO_URL="${2:-}"; shift 2 ;;
        --no-seed) DO_SEED=0; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) die "未知选项：$1（-h 看用法）" ;;
        *)  [[ -z "$DOMAIN" ]] || die "域名只能给一个：$1"
            DOMAIN="$1"; shift ;;
    esac
done

[[ $EUID -eq 0 ]] || die "请用 root 跑：sudo bash deploy/install.sh <域名> [选项]"

# 没给的就问（非交互环境跳过，用默认值）
if [[ -t 0 ]]; then
    [[ -n "$DOMAIN" ]] || read -rp "对外域名（例如 canoe.s-ui.com，留空表示只用 IP）: " DOMAIN
    [[ -n "$PORT"   ]] || read -rp "对外端口 [58588]: " PORT
    PORT="${PORT:-58588}"
    if [[ -z "$HTTPS_MODE" ]]; then
        read -rp "HTTPS 方式 [le=Let's Encrypt / self=自签 / none=不加密] (默认 le): " HTTPS_MODE
        HTTPS_MODE="${HTTPS_MODE:-le}"
    fi
else
    PORT="${PORT:-58588}"
fi
PORT="${PORT:-58588}"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "端口必须是数字：$PORT"

if [[ -z "$HTTPS_MODE" ]]; then
    [[ -n "$DOMAIN" ]] && HTTPS_MODE="le" || HTTPS_MODE="self"
fi
case "$HTTPS_MODE" in le|self|none) ;; *) die "--https 只能是 le / self / none";; esac
if [[ "$HTTPS_MODE" == "le" && -z "$DOMAIN" ]]; then
    warn "Let's Encrypt 需要域名，自动改成自签证书"
    HTTPS_MODE="self"
fi

SCHEME="https"; [[ "$HTTPS_MODE" == "none" ]] && SCHEME="http"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cat <<EOF

  轻舟 / Canoe Server 安装
  ────────────────────────────────
  域名   ${DOMAIN:-<无，用 IP 访问>}
  端口   $PORT
  加密   $HTTPS_MODE
  访问   $SCHEME://${DOMAIN:-<本机IP>}:$PORT

EOF
if [[ -t 0 ]]; then read -rp "确认开始？[Y/n] " ok; [[ "${ok:-y}" =~ ^[Yy]?$ ]] || exit 0; fi

# ---------------------------------------------------------------------------
# 1. 系统依赖
# ---------------------------------------------------------------------------
log "安装系统依赖…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PKGS=(python3 python3-venv python3-pip openssl rsync curl)
[[ "$HTTPS_MODE" == "le" ]] && PKGS+=(certbot)
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
sudo -u "$APP_USER" "$PIP" install -q -U pip
sudo -u "$APP_USER" "$PIP" install -q -e "$APP_DIR/canoe-core"
sudo -u "$APP_USER" "$PIP" install -q -r "$SERVER_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 4. 证书
# ---------------------------------------------------------------------------
TLS_CERT=""
TLS_KEY=""
mkdir -p "$CERT_DIR"

case "$HTTPS_MODE" in
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
    TLS_CERT="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    TLS_KEY="/etc/letsencrypt/live/$DOMAIN/privkey.pem"

    # 续期后要把服务重启一次，否则还在用旧证书
    mkdir -p /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/canoe-restart.sh <<'HOOK'
#!/bin/sh
systemctl restart canoe-api
HOOK
    chmod +x /etc/letsencrypt/renewal-hooks/deploy/canoe-restart.sh
    ;;

  self)
    log "生成自签证书…"
    if [[ -f "$CERT_DIR/cert.pem" ]]; then
        log "已有证书，跳过"
    else
        CN="${DOMAIN:-localhost}"
        SAN="DNS:${CN},IP:127.0.0.1"
        # 顺手把本机内网 IP 也塞进 SAN，方便用 IP 直连
        LANIP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        [[ -n "$LANIP" ]] && SAN="$SAN,IP:$LANIP"
        openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
            -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
            -subj "//CN=$CN" -addext "subjectAltName=$SAN" >/dev/null 2>&1
        warn "自签证书：浏览器会提示不安全；客户端要信任 $CERT_DIR/cert.pem 才能连"
    fi
    TLS_CERT="$CERT_DIR/cert.pem"
    TLS_KEY="$CERT_DIR/key.pem"
    ;;

  none)
    warn "不加密模式：请确保前面有别的 HTTPS 反代，否则令牌是明文传的"
    ;;
esac
chmod 600 "$CERT_DIR"/*.pem 2>/dev/null || true

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
PORT=$PORT
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

# 中转层（那台机器上再配）
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
if [[ "$HTTPS_MODE" == "none" ]]; then
    curl -fsS "http://$HOSTPART:$PORT/api/health" >/dev/null && log "本机健康检查通过" || warn "健康检查没过，看 journalctl -u canoe-api"
else
    curl -fsSk "https://$HOSTPART:$PORT/api/health" >/dev/null && log "本机健康检查通过（自签证书用 -k）" || warn "健康检查没过，看 journalctl -u canoe-api"
fi

BASE_SHOWN="${SCHEME}://${DOMAIN:-<本机IP>}"
[[ "$PORT" != "443" && "$PORT" != "80" ]] && BASE_SHOWN="$BASE_SHOWN:$PORT"

cat <<EOF

============================================================
  装好了
============================================================

  管理面板 : $BASE_SHOWN/panel
  客户端更新接口 : $BASE_SHOWN/api/client/latest
  客户端订阅接口 : $BASE_SHOWN/api/subscription
  健康检查 : $BASE_SHOWN/api/health

  日志     : journalctl -u canoe-api -f
  配置     : $SERVER_DIR/.env   （改端口/证书只改这里，然后 systemctl restart canoe-api）
  管理员   : 见 $APP_DIR/ADMIN_PASSWORD.txt

  下一步：
    1. 打开面板登录，改掉管理员密码
    2. 面板「节点」里加一个节点，再去中转层机器部署 relay（见 relay/README.md）
    3. 面板「发布」里上传客户端安装包 —— 客户端点「更新」就能看到

  ⚠ 服务端只开 1 个 worker（推送是进程内的，多 worker 会收不到）。
    详见 deploy/README.md 开头。

EOF
