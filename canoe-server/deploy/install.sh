#!/usr/bin/env bash
#
# 轻舟 / Canoe Server —— 一键安装（Debian / Ubuntu）
#
#   sudo bash deploy/install.sh api.canoe.example.com
#
# 会做到：建 canoe 用户 -> 放代码到 /opt/canoe -> 建 venv 装依赖 ->
#         生成 .env（随机密钥）-> 建库 + 管理员 -> 装 systemd -> 配 Nginx + 证书
#
# 幂等：重复跑不会把已有的 .env / 数据库覆盖掉。
set -euo pipefail

APP_USER="canoe"
APP_DIR="/opt/canoe"
SERVER_DIR="$APP_DIR/canoe-server"
DOMAIN="${1:-}"

log()  { printf '\033[1;36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. 前置检查
# ---------------------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "请用 root 跑（sudo bash deploy/install.sh <域名>）"
[[ -n "$DOMAIN" ]] || die "用法：sudo bash deploy/install.sh api.canoe.example.com"
[[ "$DOMAIN" == *.* ]] || die "域名看起来不对：$DOMAIN"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
[[ -f "$REPO_ROOT/canoe-core/setup.py" || -f "$REPO_ROOT/canoe-core/pyproject.toml" ]] \
  || warn "在 $REPO_ROOT 没找到 canoe-core，确认一下目录结构"

if [[ -z "${CANOE_REPO_URL:-}" && ! -d "$REPO_ROOT/canoe-core" ]]; then
    die "既没有 CANOE_REPO_URL，当前目录也不像仓库根目录"
fi

log "域名：$DOMAIN"
log "代码来源：${CANOE_REPO_URL:-$REPO_ROOT}"

# ---------------------------------------------------------------------------
# 1. 系统依赖
# ---------------------------------------------------------------------------
log "安装系统依赖…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx certbot python3-certbot-nginx rsync

# ---------------------------------------------------------------------------
# 2. 用户与代码
# ---------------------------------------------------------------------------
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    log "创建用户 $APP_USER"
    useradd -r -s /usr/sbin/nologin -d "$APP_DIR" "$APP_USER"
fi
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

if [[ -n "${CANOE_REPO_URL:-}" ]]; then
    if [[ -d "$APP_DIR/.git" ]]; then
        log "拉取最新代码"
        sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
    else
        log "克隆仓库"
        sudo -u "$APP_USER" git clone "$CANOE_REPO_URL" "$APP_DIR"
    fi
else
    log "从 $REPO_ROOT 同步代码到 $APP_DIR"
    rsync -a --delete \
        --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
        --exclude 'canoe-server/data' --exclude 'canoe-server/releases' \
        --exclude 'canoe-client/dist' --exclude 'canoe-client/build' \
        "$REPO_ROOT/" "$APP_DIR/"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ---------------------------------------------------------------------------
# 3. venv 与依赖
# ---------------------------------------------------------------------------
log "建立虚拟环境并安装依赖…"
if [[ ! -d "$SERVER_DIR/.venv" ]]; then
    sudo -u "$APP_USER" python3 -m venv "$SERVER_DIR/.venv"
fi
sudo -u "$APP_USER" "$SERVER_DIR/.venv/bin/pip" install -q -U pip
sudo -u "$APP_USER" "$SERVER_DIR/.venv/bin/pip" install -q -e "$APP_DIR/canoe-core"
sudo -u "$APP_USER" "$SERVER_DIR/.venv/bin/pip" install -q -r "$SERVER_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 4. .env（已存在就不动）
# ---------------------------------------------------------------------------
ENV_FILE="$SERVER_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    log ".env 已存在，保留不动（要改请手动编辑 $ENV_FILE）"
else
    log "生成 .env"
    TICKET_SECRET="$(sudo -u "$APP_USER" "$SERVER_DIR/.venv/bin/python" -c \
        'import secrets;print(secrets.token_urlsafe(48))')"
    ADMIN_PASSWORD="$(sudo -u "$APP_USER" "$SERVER_DIR/.venv/bin/python" -c \
        'import secrets;print(secrets.token_urlsafe(18))')"

    cat > "$ENV_FILE" <<EOF
# 由 deploy/install.sh 生成于 $(date -Is)
DEBUG=false
HOST=0.0.0.0
PORT=8000
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

# 安装包下载地址靠它拼。写死成对外域名，别让它被 Host 头带偏。
PUBLIC_BASE_URL=https://$DOMAIN
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
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    cat > "$APP_DIR/ADMIN_PASSWORD.txt" <<EOF
初始管理员账号（登录后请立刻改掉，然后删掉这个文件）

  用户名: admin
  密码:   $ADMIN_PASSWORD

改密码：
  curl -X PATCH -H "Authorization: Bearer <admin token>" -H "Content-Type: application/json" \\
       -d '{"password":"新密码"}' https://$DOMAIN/api/admin/users/1
EOF
    chmod 600 "$APP_DIR/ADMIN_PASSWORD.txt"
    warn "管理员密码写到了 $APP_DIR/ADMIN_PASSWORD.txt —— 登录后请改掉并删除它"
fi

mkdir -p "$SERVER_DIR/data" "$SERVER_DIR/releases"
chown -R "$APP_USER:$APP_USER" "$SERVER_DIR/data" "$SERVER_DIR/releases"

# ---------------------------------------------------------------------------
# 5. 建库 + 种子
# ---------------------------------------------------------------------------
log "初始化数据库…"
sudo -u "$APP_USER" bash -c "cd '$SERVER_DIR' && .venv/bin/python seed.py"

# ---------------------------------------------------------------------------
# 6. systemd
# ---------------------------------------------------------------------------
log "安装 systemd 服务…"
install -m 644 "$SERVER_DIR/deploy/canoe-api.service" /etc/systemd/system/canoe-api.service
systemctl daemon-reload
systemctl enable canoe-api >/dev/null
systemctl restart canoe-api
sleep 2
systemctl is-active --quiet canoe-api \
    && log "canoe-api 已在跑" \
    || { journalctl -u canoe-api -n 30 --no-pager; die "canoe-api 起不来，日志见上"; }

# ---------------------------------------------------------------------------
# 7. Nginx + 证书
# ---------------------------------------------------------------------------
log "配置 Nginx…"
sed "s/api\.canoe\.example\.com/$DOMAIN/g" \
    "$SERVER_DIR/deploy/nginx.canoe.conf" > "/etc/nginx/sites-available/canoe"
ln -sf /etc/nginx/sites-available/canoe /etc/nginx/sites-enabled/canoe
[[ -e /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "申请证书…"
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
        --register-unsafely-without-email --redirect; then
    systemctl reload nginx
else
    warn "证书没申请成功（DNS 还没解析到本机？）。先用 HTTP 验证，之后再跑："
    warn "  sudo certbot --nginx -d $DOMAIN"
fi

# ---------------------------------------------------------------------------
# 8. 自检
# ---------------------------------------------------------------------------
log "自检…"
sleep 1
if curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null; then
    log "本机健康检查通过"
else
    warn "本机 /api/health 通不过，看看 journalctl -u canoe-api"
fi

cat <<EOF

============================================================
  装好了
============================================================

  API      : https://$DOMAIN
  健康检查 : curl https://$DOMAIN/api/health
  日志     : journalctl -u canoe-api -f
  管理员   : 见 $APP_DIR/ADMIN_PASSWORD.txt（改完密码请删掉）

  下一步：
    1. 登录后台改掉管理员密码
    2. 加一个节点（/api/admin/nodes），然后配置中转层
       见 canoe-server/relay/README.md
    3. 发布客户端版本（/api/admin/releases/upload），客户端「更新」就能看到

  ⚠ 服务端只开 1 个 worker —— 推送是进程内的，多 worker 会收不到。
    详见 deploy/README.md 开头。

EOF
