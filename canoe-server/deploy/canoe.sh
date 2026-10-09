#!/usr/bin/env bash
# ===========================================================================
#  轻舟 / Canoe —— 服务端管理脚本
#
#  用法：
#      sudo canoe              交互菜单
#      sudo canoe status       也可以直接用子命令（方便写进脚本/定时任务）
#
#      canoe install | start | stop | restart | status | config | upgrade
#      canoe uninstall | logs | passwd | version | help
#
#  设计说明：
#    · 菜单和子命令走的是同一批函数，不会出现"菜单里能用、脚本里不行"。
#    · **不用 set -e**：菜单流程里某条命令失败（比如健康检查没过）
#      不该把整个程序踢出去 —— 那会让人连菜单都回不去。
#    · 所有需要 root 的动作都先检查，而不是执行到一半才失败。
#    · 卸载是唯一会删东西的操作，所以它要确认两遍。
# ===========================================================================
set -uo pipefail

# ---- 固定路径（和 install.sh 保持一致，改一处要一起改）----
APP_USER="canoe"
APP_DIR="/opt/canoe"
SERVER_DIR="$APP_DIR/canoe-server"
ENV_FILE="$SERVER_DIR/.env"
CERT_DIR="/etc/canoe"
LIVE_DIR="$CERT_DIR/live"
UNIT="canoe-api"
UNIT_FILE="/etc/systemd/system/$UNIT.service"
SELF_DEST="/usr/local/bin/canoe"

# ---- 输出 ----
if [[ -t 1 ]]; then
    C_OK=$'\033[1;32m'; C_INFO=$'\033[1;36m'; C_WARN=$'\033[1;33m'
    C_ERR=$'\033[1;31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_INFO=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""
fi
log()  { printf '%s[*]%s %s\n' "$C_INFO" "$C_OFF" "$*"; }
ok()   { printf '%s[+]%s %s\n' "$C_OK"   "$C_OFF" "$*"; }
warn() { printf '%s[!]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
err()  { printf '%s[x]%s %s\n' "$C_ERR"  "$C_OFF" "$*" >&2; }
dim()  { printf '%s    %s%s\n' "$C_DIM" "$*" "$C_OFF"; }

die() { err "$*"; exit 1; }

hr() { printf '%s\n' "------------------------------------------------------------"; }

need_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "需要 root：请用 sudo canoe"
}

# 已经装过没？（判据是 venv，不是目录存在 —— 目录可能在、但还没装）
installed() { [[ -x "$SERVER_DIR/.venv/bin/python" ]]; }

require_installed() {
    installed || die "还没安装。先跑：sudo canoe install"
}

# ---------------------------------------------------------------------------
# 读 .env
# ---------------------------------------------------------------------------
env_get() {
    # $1 = key；取不到就给空串，不报错（调用方自己决定默认值）
    [[ -f "$ENV_FILE" ]] || { printf ''; return; }
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n1
}

env_set() {
    # $1 = key, $2 = value。有就替换，没有就追加。
    # 用 awk 而不是 sed -i：值里可能带 / 和 &，用 sed 得转义，容易出错。
    local key="$1" val="$2" tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" '
        BEGIN { done = 0 }
        $0 ~ "^[[:space:]]*" k "[[:space:]]*=" { print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$tmp" && cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}

scheme() {
    # 有证书走 https，没有就是 http
    [[ -n "$(env_get TLS_CERT)" ]] && printf 'https' || printf 'http'
}

client_port() { printf '%s' "$(env_get PORT)"; }
panel_port()  { printf '%s' "$(env_get PANEL_PORT)"; }

local_port() {
    # 服务实际监听的口（.env 里 HOST 可能是 0.0.0.0，健康检查要走回环）
    printf '%s' "$(client_port)"
}

health_url() {
    printf '%s://127.0.0.1:%s/api/health' "$(scheme)" "$(local_port)"
}

health_check() {
    # 自签证书时 curl 会报错，加 -k。生产是真实证书，加 -k 也无害（只连本机回环）
    curl -fsSk --max-time 6 "$(health_url)" >/dev/null 2>&1
}

# 版本号：以安装好的那份代码为准（可能刚升级过）
app_version() {
    installed || { printf '?'; return; }
    "$SERVER_DIR/.venv/bin/python" - <<'PY' 2>/dev/null || printf '?'
try:
    from canoe_core import VERSION
    print(VERSION)
except Exception:
    print("?")
PY
}

panel_url() {
    local s p pp host
    s="$(scheme)"; p="$(client_port)"; pp="$(panel_port)"
    host="$(domain_display)"
    if [[ -z "$pp" || "$pp" == "$p" ]]; then
        printf '%s://%s:%s/panel' "$s" "$host" "$p"
    else
        printf '%s://%s:%s/panel' "$s" "$host" "$pp"
    fi
}

# 域名显示用：.env 里没有独立的 DOMAIN，就从 PUBLIC_BASE_URL 里抠
domain_display() {
    local base
    base="$(env_get PUBLIC_BASE_URL)"
    [[ -z "$base" ]] && { printf '<本机IP>'; return; }
    printf '%s' "$base" | sed -E 's#^[a-z]+://##; s#[:/].*$##'
}

# ---------------------------------------------------------------------------
# 1. 安装
# ---------------------------------------------------------------------------
cmd_install() {
    need_root

    if installed; then
        warn "检测到已经装过了（$SERVER_DIR/.venv 存在）"
        dim "要改端口 / 证书，用「配置」→「重新走安装向导」；"
        dim "要更新代码，用「升级」。"
        printf '\n  继续安装会覆盖现有代码（数据不受影响）。确认继续？[y/N] '
        local a; read -r a
        [[ "$a" =~ ^[Yy]$ ]] || { log "已取消"; return 0; }
    fi

    local script="$SERVER_DIR/deploy/install.sh"

    # 还没把代码放到 /opt/canoe 的情况：从当前目录找一个
    if [[ ! -f "$script" ]]; then
        for cand in "./deploy/install.sh" "./install.sh" "$(dirname "$0")/install.sh"; do
            [[ -f "$cand" ]] && { script="$cand"; break; }
        done
    fi
    [[ -f "$script" ]] || die "找不到 install.sh。
     请先在源码目录里跑：sudo ./deploy/install.sh
     装好之后这个管理菜单里的「安装」才有意义。"

    log "交给安装向导（端口 / 域名 / 证书都在那里选）"
    exec bash "$script"
}

# ---------------------------------------------------------------------------
# 2-4. 启动 / 停止 / 重启
# ---------------------------------------------------------------------------
cmd_start() {
    need_root; require_installed
    if systemctl is-active --quiet "$UNIT"; then
        log "$UNIT 已经在跑了"; return 0
    fi
    log "启动 $UNIT…"
    systemctl start "$UNIT"
    sleep 2
    cmd_status_brief
}

cmd_stop() {
    need_root; require_installed
    if ! systemctl is-active --quiet "$UNIT"; then
        log "$UNIT 本来就没在跑"; return 0
    fi
    log "停止 $UNIT…"
    systemctl stop "$UNIT"
    sleep 1
    systemctl is-active --quiet "$UNIT" \
        && die "停不掉，看 journalctl -u $UNIT -n 30" \
        || ok "已停止"
}

cmd_restart() {
    need_root; require_installed
    log "重启 $UNIT…"
    systemctl restart "$UNIT"
    sleep 3
    cmd_status_brief
}

# 给 start/restart 用的一行结论
cmd_status_brief() {
    if systemctl is-active --quiet "$UNIT"; then
        if health_check; then
            ok "在跑，健康检查通过（$(health_url)）"
        else
            warn "在跑，但健康检查没过 —— 看 journalctl -u $UNIT -n 30"
            return 1
        fi
    else
        err "没起来"
        journalctl -u "$UNIT" -n 20 --no-pager
        return 1
    fi
}

# ---------------------------------------------------------------------------
# 5. 状态
# ---------------------------------------------------------------------------
cmd_status() {
    require_installed

    local ver active enabled since
    ver="$(app_version)"
    active="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
    enabled="$(systemctl is-enabled "$UNIT" 2>/dev/null || true)"

    hr
    printf '  %s轻舟 / Canoe Server%s   版本 %s\n' "$C_INFO" "$C_OFF" "$ver"
    hr

    printf '  服务状态   : '
    case "$active" in
        active)   printf '%s运行中%s\n' "$C_OK" "$C_OFF" ;;
        failed)   printf '%s启动失败%s\n' "$C_ERR" "$C_OFF" ;;
        *)        printf '%s已停止%s (%s)\n' "$C_WARN" "$C_OFF" "$active" ;;
    esac
    printf '  开机自启   : %s\n' "$([[ "$enabled" == "enabled" ]] && echo "是" || echo "否")"

    since="$(systemctl show -p ActiveEnterTimestamp --value "$UNIT" 2>/dev/null || true)"
    [[ -n "$since" ]] && printf '  启动于     : %s\n' "$since"

    local p pp
    p="$(client_port)"; pp="$(panel_port)"
    printf '  客户端口   : %s/tcp  （客户端固定用它取更新和订阅）\n' "${p:-?}"
    if [[ -n "$pp" && "$pp" != "$p" ]]; then
        printf '  面板口     : %s/tcp\n' "$pp"
    else
        printf '  面板口     : 同客户端口\n'
    fi
    printf '  面板地址   : %s\n' "$(panel_url)"
    printf '  证书       : %s\n' "$(env_get TLS_CERT || echo '（无，走 HTTP）')"

    printf '\n  健康检查   : '
    if health_check; then
        printf '%s通过%s  %s\n' "$C_OK" "$C_OFF" "$(health_url)"
    else
        printf '%s失败%s  %s\n' "$C_ERR" "$C_OFF" "$(health_url)"
    fi

    # 监听端口一览（有 ss 就用 ss，没有就算了，不报错）
    if command -v ss >/dev/null 2>&1; then
        printf '\n  监听端口   :\n'
        ss -lntp 2>/dev/null | awk -v p="$p" -v pp="$pp" '
            NR==1 { next }
            $4 ~ ":"p"$" || (pp != "" && $4 ~ ":"pp"$") {
                printf "    %-24s %s\n", $4, $6
            }'
    fi

    # 数据库大小
    local db="$SERVER_DIR/data/canoe.db"
    [[ -f "$db" ]] && printf '\n  数据库     : %s\n' "$(du -h "$db" 2>/dev/null | cut -f1)"

    # 账号数 / 在线会话。直接读 sqlite 是为了在服务已经挂掉时也能看，
    # 所以不走 API。
    if installed; then
        local stat accounts online
        stat="$(cd "$SERVER_DIR" && "$SERVER_DIR/.venv/bin/python" - <<'PY' 2>/dev/null
import sqlite3, pathlib
db = pathlib.Path("data/canoe.db")
if not db.is_file():
    raise SystemExit
c = sqlite3.connect(db)
try:
    u = c.execute("select count(*) from users where status='active'").fetchone()[0]
    s = c.execute("select count(*) from sessions where ended_at is null").fetchone()[0]
    print(u, s)
except Exception:
    pass
PY
)"
        if [[ -n "$stat" ]]; then
            read -r accounts online <<< "$stat"
            printf '  账号       : 正常 %s 个\n' "${accounts:-?}"
            printf '  在线会话   : %s 条\n' "${online:-?}"
        fi
    fi

    printf '\n  最近日志   :\n'
    journalctl -u "$UNIT" -n 8 --no-pager 2>/dev/null | sed 's/^/    /'
    hr
    dim "看完整日志：canoe logs"
}

# ---------------------------------------------------------------------------
# 6. 配置
# ---------------------------------------------------------------------------
cmd_config() {
    need_root; require_installed

    while true; do
        hr
        printf '  %s配置%s\n' "$C_INFO" "$C_OFF"
        hr
        printf '    1  查看当前配置\n'
        printf '    2  改客户端端口（客户端固定用 58588）\n'
        printf '    3  改面板端口\n'
        printf '    4  用编辑器打开 .env（高级）\n'
        printf '    5  重新走安装向导（改端口 / 域名 / 证书）\n'
        printf '    0  返回\n\n'
        printf '  请选择: '
        local c; read -r c
        case "$c" in
            1) show_config ;;
            2) change_port PORT "客户端口" ;;
            3) change_port PANEL_PORT "面板口" ;;
            4) edit_env ;;
            5) run_install_wizard ;;
            0) return 0 ;;
            *) warn "没有这一项" ;;
        esac
    done
}

show_config() {
    hr
    printf '  %s当前配置%s  %s\n' "$C_INFO" "$C_OFF" "$ENV_FILE"
    hr
    if [[ ! -f "$ENV_FILE" ]]; then
        warn "配置文件不存在"
        return 0
    fi
    # 密钥类打码再显示
    sed -E 's/^([A-Z_]*SECRET[A-Z_]*=).*/\1********/; s/^([A-Z_]*PASSWORD[A-Z_]*=).*/\1********/' \
        "$ENV_FILE" | sed 's/^/    /'
    hr
}

change_port() {
    local key="$1" label="$2" cur new
    cur="$(env_get "$key")"
    printf '\n  当前%s: %s\n' "$label" "${cur:-（未设置）}"
    printf '  新端口（1-65535，回车取消）: '
    read -r new

    [[ -z "$new" ]] && { log "已取消"; return 0; }
    [[ "$new" =~ ^[0-9]+$ ]] || { err "端口得是数字"; return 1; }
    (( new >= 1 && new <= 65535 )) || { err "端口要在 1-65535 之间"; return 1; }

    if [[ "$key" == "PORT" && "$new" != "58588" ]]; then
        warn "客户端那边把服务端地址写死在 58588 了"
        dim "改成别的口，所有已发出的客户端都会连不上 —— 除非你确定要这么干。"
        printf '  仍然继续？[y/N] '
        local a; read -r a
        [[ "$a" =~ ^[Yy]$ ]] || { log "已取消"; return 0; }
    fi

    env_set "$key" "$new"
    ok "$label 已改为 $new"

    # 端口另有防火墙/安全组要放行，这里提一句
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow "$new"/tcp >/dev/null 2>&1 && log "已在 ufw 放行 $new/tcp"
    fi

    restart_now
}

edit_env() {
    local editor="${EDITOR:-}"
    if [[ -z "$editor" ]]; then
        for e in nano vim vi; do
            command -v "$e" >/dev/null 2>&1 && { editor="$e"; break; }
        done
    fi
    [[ -n "$editor" ]] || { err "找不到编辑器，设一下 EDITOR 环境变量"; return 1; }

    cp -a "$ENV_FILE" "$ENV_FILE.bak"
    log "已备份到 $ENV_FILE.bak"
    "$editor" "$ENV_FILE"
    ok "编辑完成"
    restart_now
}

run_install_wizard() {
    local script="$SERVER_DIR/deploy/install.sh"
    [[ -f "$script" ]] || { err "找不到 $script"; return 1; }
    log "重新走安装向导（现有配置会作为默认值）"
    bash "$script"
}

restart_now() {
    printf '\n  现在重启服务让它生效？[Y/n] '
    local a; read -r a
    [[ "$a" =~ ^[Nn]$ ]] && { warn "改动要重启才生效：canoe restart"; return 0; }
    cmd_restart
}

# ---------------------------------------------------------------------------
# 7. 升级
# ---------------------------------------------------------------------------
cmd_upgrade() {
    need_root; require_installed

    local before after
    before="$(app_version)"
    log "当前版本 $before"

    # 1) 拉代码 —— 只在确实是 git 检出时才 pull
    if [[ -d "$APP_DIR/.git" ]]; then
        log "拉取最新代码…"
        if ! git -C "$APP_DIR" pull --ff-only; then
            err "拉取失败。按顺序排查："
            dim "1) 本地有没有改动：  git -C $APP_DIR status"
            dim "2) 私有仓库的凭据还在不在（最常见）："
            dim "   Deploy Key：确认 ~/.ssh/ 里的私钥在、公钥还在仓库的 Deploy keys 里"
            dim "   用的是 PAT：令牌会被擦掉，得重新给一次 ——"
            dim "              sudo bash $SERVER_DIR/deploy/install.sh --token <新PAT>"
            printf '\n  跳过拉代码，只重装依赖并重启？[y/N] '
            local a; read -r a
            [[ "$a" =~ ^[Yy]$ ]] || { log "已取消"; return 0; }
        fi
    else
        warn "$APP_DIR 不是 git 检出，跳过拉代码"
        dim "手动把新代码放进去，然后再跑一次「升级」"
    fi

    # 2) 重装依赖（canoe-core 可能有新依赖，比如 cryptography）
    log "更新依赖…"
    sudo -u "$APP_USER" bash -c "
        cd '$SERVER_DIR' &&
        .venv/bin/pip install -q --upgrade pip >/dev/null 2>&1
        .venv/bin/pip install -q -e ../canoe-core &&
        .venv/bin/pip install -q -r requirements.txt
    " || die "依赖安装失败"

    # 3) 数据表补列（新版本可能加了字段）
    log "对齐数据库结构…"
    sudo -u "$APP_USER" bash -c "cd '$SERVER_DIR' && .venv/bin/python -c '
from canoe_server.database import init_db
init_db()
print(\"    表结构已对齐\")
'" || warn "数据库对齐失败，启动时可能报错"

    # 4) 重启
    systemctl restart "$UNIT"
    sleep 3

    after="$(app_version)"
    hr
    if systemctl is-active --quiet "$UNIT" && health_check; then
        ok "升级完成：$before -> $after"
    else
        err "升级后服务不正常"
        journalctl -u "$UNIT" -n 30 --no-pager
        dim "回滚：git -C $APP_DIR checkout <上一个 tag> 然后 sudo canoe restart"
        return 1
    fi
    hr
}

# ---------------------------------------------------------------------------
# 8. 卸载
# ---------------------------------------------------------------------------
cmd_uninstall() {
    need_root
    hr
    printf '  %s卸载 Canoe%s\n' "$C_ERR" "$C_OFF"
    hr
    warn "这会停止服务、删掉 systemd 单元。"
    printf '\n  确认卸载？[y/N] '
    local a; read -r a
    [[ "$a" =~ ^[Yy]$ ]] || { log "已取消"; return 0; }

    printf '\n  同时删除数据？\n'
    dim "包括：账号、订阅配置、已发布的安装包、证书"
    dim "选 n 则只卸载程序，$APP_DIR 和 $CERT_DIR 原样留着"
    printf '  删除数据？[y/N] '
    local wipe; read -r wipe

    printf '\n  最后确认一次，输入 %syes%s 才会继续: ' "$C_WARN" "$C_OFF"
    local final; read -r final
    [[ "$final" == "yes" ]] || { log "已取消"; return 0; }

    log "停止并禁用服务…"
    systemctl stop "$UNIT" 2>/dev/null || true
    systemctl disable "$UNIT" 2>/dev/null || true

    if [[ -f "$UNIT_FILE" ]]; then
        rm -f "$UNIT_FILE"
        systemctl daemon-reload
        ok "systemd 单元已删除"
    fi

    [[ -f "$SELF_DEST" ]] && { rm -f "$SELF_DEST"; ok "管理脚本 $SELF_DEST 已删除"; }

    if [[ "$wipe" =~ ^[Yy]$ ]]; then
        log "删除程序与数据…"
        rm -rf "$APP_DIR"
        rm -rf "$CERT_DIR"
        ok "已删除 $APP_DIR 与 $CERT_DIR"

        if id "$APP_USER" >/dev/null 2>&1; then
            printf '  删除系统用户 %s？[y/N] ' "$APP_USER"
            local du; read -r du
            if [[ "$du" =~ ^[Yy]$ ]]; then
                userdel "$APP_USER" 2>/dev/null && ok "用户已删除" || warn "删除用户失败（可能有进程占用）"
            fi
        fi
    else
        log "保留 $APP_DIR 与 $CERT_DIR"
        dim "想彻底清干净：sudo rm -rf $APP_DIR $CERT_DIR"
    fi

    hr
    ok "卸载完成"
}

# ---------------------------------------------------------------------------
# 附加：日志 / 改管理员密码 / 版本
# ---------------------------------------------------------------------------
cmd_logs() {
    require_installed
    log "跟随 $UNIT 日志（Ctrl-C 退出）"
    journalctl -u "$UNIT" -f -n 50
}

cmd_passwd() {
    need_root; require_installed
    printf '  新管理员密码（至少 8 位）: '
    local p1; read -rs p1; printf '\n'
    printf '  再输一遍: '
    local p2; read -rs p2; printf '\n'

    [[ ${#p1} -ge 8 ]] || die "太短了，至少 8 位"
    [[ "$p1" == "$p2" ]] || die "两次输入不一致"

    local admin_env user
    user="$(env_get ADMIN_USERNAME)"; user="${user:-admin}"

    sudo -u "$APP_USER" bash -c "
        cd '$SERVER_DIR' && CO_CHANGE_PW='$p1' CO_CHANGE_USER='$user' .venv/bin/python - <<'PY'
import os
from canoe_server.database import SessionLocal
from canoe_server.models import User
from canoe_server.security import hash_password

pw = os.environ['CO_CHANGE_PW']
name = os.environ['CO_CHANGE_USER']

with SessionLocal() as db:
    u = db.query(User).filter(User.username == name).one_or_none()
    if u is None:
        raise SystemExit(f'找不到管理员 {name}')
    u.password_hash = hash_password(pw)
    db.commit()
print(f'    管理员 {name} 的密码已更新')
PY
" || die "改密码失败"

    ok "改好了 —— 旧令牌不会自动失效，要踢掉所有登录用「面板 → 用户 → 封禁再解封」"
}

cmd_version() {
    printf 'canoe 管理脚本  %s\n' "$([[ -f "$SELF_DEST" ]] && echo "$SELF_DEST" || echo "（未安装到 PATH）")"
    if installed; then
        printf '服务端          %s  (%s)\n' "$(app_version)" "$SERVER_DIR"
    else
        printf '服务端          未安装\n'
    fi
}

# ---------------------------------------------------------------------------
# 菜单
# ---------------------------------------------------------------------------
show_menu() {
    local state ver
    ver="$(app_version)"
    if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
        state="${C_OK}运行中${C_OFF}"
    elif installed; then
        state="${C_WARN}已停止${C_OFF}"
    else
        state="${C_DIM}未安装${C_OFF}"
    fi

    clear 2>/dev/null || true
    hr
    printf '          %s轻舟 / Canoe  服务端管理%s\n' "$C_INFO" "$C_OFF"
    dim "轻舟已过万重山        $state   版本 $ver"
    hr
    printf '   1  安装 Canoe\n'
    printf '   2  启动 Canoe\n'
    printf '   3  停止 Canoe\n'
    printf '   4  重启 Canoe\n'
    printf '   5  Canoe 状态\n'
    printf '   6  Canoe 配置\n'
    printf '   7  升级 Canoe\n'
    printf '   8  卸载 Canoe\n'
    hr
    printf '   l  看日志        p  改管理员密码\n'
    hr
    printf '   0  退出\n'
    hr
    printf '  请选择: '
}

menu() {
    while true; do
        show_menu
        local choice
        read -r choice || { printf '\n'; return 0; }

        case "$choice" in
            1) cmd_install ;;
            2) cmd_start ;;
            3) cmd_stop ;;
            4) cmd_restart ;;
            5) cmd_status ;;
            6) cmd_config ;;
            7) cmd_upgrade ;;
            8) cmd_uninstall ;;
            l|L) cmd_logs ;;
            p|P) cmd_passwd ;;
            0|q|Q) printf '\n  再见。\n\n'; return 0 ;;
            "") ;;
            *) warn "没有这一项：$choice" ;;
        esac

        # 卸载成功后别再回菜单了 —— 东西都没了
        [[ "$choice" == "8" ]] && ! installed && return 0

        printf '\n  回车继续…'
        read -r _ || true
    done
}

usage() {
    cat <<'EOF'
轻舟 / Canoe —— 服务端管理脚本

用法：
    sudo canoe                交互菜单
    sudo canoe <命令>         直接用子命令

命令：
    install     安装（交给安装向导，选端口 / 域名 / 证书）
    start       启动服务
    stop        停止服务
    restart     重启服务
    status      查看状态（端口 / 健康检查 / 在线人数 / 最近日志）
    config      改端口 / 证书 / 配置
    upgrade     拉代码 + 更新依赖 + 对齐表结构 + 重启
    uninstall   卸载（会删 systemd 单元；数据是否保留会单独问）
    logs        跟随日志
    passwd      改管理员密码
    version     版本
    help        这份帮助

文件位置：
    程序    /opt/canoe
    配置    /opt/canoe/canoe-server/.env
    证书    /etc/canoe/live/
    数据    /opt/canoe/canoe-server/data/
    单元    /etc/systemd/system/canoe-api.service

常用：
    journalctl -u canoe-api -f     看实时日志
    systemctl restart canoe-api    等价于 canoe restart
EOF
}

main() {
    if [[ $# -eq 0 ]]; then
        menu
        return 0
    fi

    local cmd="$1"; shift
    case "$cmd" in
        install)   cmd_install "$@" ;;
        start)     cmd_start ;;
        stop)      cmd_stop ;;
        restart)   cmd_restart ;;
        status|st) cmd_status ;;
        config|cfg) cmd_config ;;
        upgrade|update) cmd_upgrade ;;
        uninstall|remove) cmd_uninstall ;;
        logs|log)  cmd_logs ;;
        passwd)    cmd_passwd ;;
        version|-v|--version) cmd_version ;;
        help|-h|--help) usage ;;
        *) err "不认识的命令：$cmd"; printf '\n'; usage; exit 1 ;;
    esac
}

# 被 source（测试里就是这么用的）时不执行 —— 只当库用。
# 直接跑才进 main。
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
