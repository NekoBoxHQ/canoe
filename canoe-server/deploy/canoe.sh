#!/usr/bin/env bash
# ===========================================================================
#  轻舟 / Canoe —— 服务端管理脚本
#
#  用法：
#      canoe                   交互菜单
#      canoe status            也可以直接用子命令（方便写进脚本/定时任务）
#
#      canoe install | start | stop | restart | status | config | upgrade
#      canoe uninstall | logs | passwd | release | version | help
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
#: 装在哪个目录。可以用 CANOE_APP_DIR 改（装在别处、或测试造一个假环境）。
#: install.sh 用的是写死的 /opt/canoe，改这里要一起改，否则两边对不上。
APP_DIR="${CANOE_APP_DIR:-/opt/canoe}"
SERVER_DIR="$APP_DIR/canoe-server"
ENV_FILE="$SERVER_DIR/.env"
CERT_DIR="/etc/canoe"
LIVE_DIR="$CERT_DIR/live"
UNIT="canoe-api"
UNIT_FILE="/etc/systemd/system/$UNIT.service"
SELF_DEST="/usr/local/bin/canoe"

#: 仓库里 deploy/ 目录的 raw 地址。
#: 机器上还没有任何代码时，「安装」要先去这里把安装向导取回来 ——
#: 这样整台机器只需要有这一个脚本就能起步。
RAW_BASE="${CANOE_RAW_BASE:-https://raw.githubusercontent.com/NekoBoxHQ/canoe/main/canoe-server/deploy}"

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

# ---- 排版基建 ------------------------------------------------------------
#
# 全脚本的 key/value 都走 field()，别再各写各的 printf —— 之前有三套
# （cmd_status 用冒号对齐、cmd_version 用空格硬推、show_config 直接 dump），
# 用户的原话是"不喜欢杂乱无章"。
#
#: 标签列补到多少显示列。现有最长标签是 4 个汉字（8 列），留 2 格。
FIELD_WIDTH=10

#: 字符串的**显示宽度**（不是字符数）。
#:
#: 为什么要自己算：对齐必须知道"一个汉字占两列"。而 bash 的 ${#s} 是按当前
#: locale 算的 —— 脚本可能在 cron、`curl | bash`、systemd 里跑，那种环境下
#: LANG 往往是 POSIX，bash 会按**字节**数，一个汉字 = 3，整屏对齐全歪。
#: 所以这里把 locale 钉成 C，自己按 UTF-8 的**前导字节**判长度，不依赖环境。
disp_width() {
    # ⚠ 必须 local —— 写成裸的 `LC_ALL=C` 会把 locale 泄漏给整个脚本，
    #   后面所有 grep/awk 都跟着变（踩过：中文匹配忽然失效）。
    local LC_ALL=C
    local s="$1" w=0 i=0 n b
    n=${#s}
    while (( i < n )); do
        printf -v b '%d' "'${s:i:1}" 2>/dev/null || b=63
        if   (( b < 0x80 )); then w=$(( w + 1 )); i=$(( i + 1 ))
        elif (( b < 0xE0 )); then w=$(( w + 2 )); i=$(( i + 2 ))   # 2 字节
        elif (( b < 0xF0 )); then w=$(( w + 2 )); i=$(( i + 3 ))   # 3 字节（汉字在这档）
        else                      w=$(( w + 2 )); i=$(( i + 4 ))   # 4 字节（emoji 等）
        fi
    done
    printf '%d' "$w"
}

#: 一行键值：两空格缩进、标签补到 FIELD_WIDTH 显示列、冒号对齐。
field() {
    local label="$1"; shift
    local w pad
    w="$(disp_width "$label")"
    pad=$(( FIELD_WIDTH - w )); (( pad < 1 )) && pad=1
    printf '  %s%*s : %s\n' "$label" "$pad" "" "$*"
}

#: 把标签补到 n 个显示列。给"标签后面还要跟别的东西"的地方用（不需要冒号）。
#: ⚠ 不能用 printf 的 %-11s —— 它按**字节**算宽度，汉字会被算成 3 列。
pad_label() {
    local w pad
    w="$(disp_width "$1")"
    pad=$(( $2 - w )); (( pad < 1 )) && pad=1
    printf '%s%*s' "$1" "$pad" ""
}

#: 小节标题：两空格缩进 + 上下两条横线（正文一律 60 根，见 hr）。
#: ⚠ 菜单是唯一的例外 —— 那个 31 根是用户点名的结构，见 MENU_RULE。
title() {
    hr
    printf '  %s%s%s\n' "$C_INFO" "$*" "$C_OFF"
    hr
}

#: 小节标签行：跟 field 同列，只是值在下面几行（"监听端口"、"最近日志"）。
section() {
    local label="$1"
    local w pad
    w="$(disp_width "$label")"
    pad=$(( FIELD_WIDTH - w )); (( pad < 1 )) && pad=1
    printf '  %s%*s :\n' "$label" "$pad" ""
}

need_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "需要 root：sudo canoe，或者 su - 切到 root 再敲 canoe"
}

# 以 $APP_USER 的身份跑一条命令（交给 bash -c）。
#
# ⚠ Debian 最小安装**不带 sudo**（独立软件包）。本脚本要求 root 运行，
#   所以不需要提权工具，但也不能假设 sudo 在 —— 一开始写死 `sudo -u canoe`，
#   在刚装好的 Debian 12 上直接 `sudo: command not found`（install.sh 上
#   踩过，这里是同一处代码）。优先 sudo，退而 runuser，最后 su。
as_user() {
    local cmd="$*"
    if command -v sudo >/dev/null 2>&1; then
        sudo -u "$APP_USER" bash -c "$cmd"
    elif command -v runuser >/dev/null 2>&1; then
        runuser -u "$APP_USER" -- bash -c "$cmd"
    else
        su -s /bin/bash -c "$cmd" "$APP_USER"
    fi
}

# 已经装过没？（判据是 venv，不是目录存在 —— 目录可能在、但还没装）
installed() { [[ -x "$SERVER_DIR/.venv/bin/python" ]]; }

require_installed() {
    installed || die "还没安装。先跑：canoe install"
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
# 找安装向导：本地检出优先，都没有就从仓库取一份回来（会打印路径）。
# 取回来的临时文件由调用方负责删。
INSTALLER_TMP=""

find_installer() {
    local cand self

    for cand in "$SERVER_DIR/deploy/install.sh" "./deploy/install.sh" "./install.sh"; do
        [[ -f "$cand" ]] && { printf '%s' "$cand"; return 0; }
    done

    # 脚本自己旁边有没有（本地检出里跑的情况）
    self="${BASH_SOURCE[0]:-}"
    if [[ -n "$self" ]]; then
        cand="$(dirname "$self")/install.sh"
        [[ -f "$cand" ]] && { printf '%s' "$cand"; return 0; }
    fi

    # 机器上还什么都没有 —— 去仓库取。
    # 这就是「一条命令起步」的实现：整台机器只需要这一个脚本。
    INSTALLER_TMP="$(mktemp)"
    log "本地没有安装向导，从仓库取一份…"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$RAW_BASE/install.sh" -o "$INSTALLER_TMP" || {
            rm -f "$INSTALLER_TMP"; INSTALLER_TMP=""
            die "下载安装向导失败（$RAW_BASE/install.sh）。
     检查一下这台机器能不能上 GitHub；或者手动把仓库 clone 下来，
     再在仓库里跑 ./canoe-server/deploy/install.sh。"
        }
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$INSTALLER_TMP" "$RAW_BASE/install.sh" || {
            rm -f "$INSTALLER_TMP"; INSTALLER_TMP=""
            die "下载安装向导失败（$RAW_BASE/install.sh）"
        }
    else
        die "这台机器上既没有 curl 也没有 wget，取不了安装向导。
     先装一个：apt-get update && apt-get install -y curl"
    fi

    [[ -s "$INSTALLER_TMP" ]] || { rm -f "$INSTALLER_TMP"; INSTALLER_TMP=""; die "下载到的安装向导是空的"; }
    printf '%s' "$INSTALLER_TMP"
}

cmd_install() {
    need_root

    local already=0
    if installed; then
        already=1
        warn "检测到已经装过了（$SERVER_DIR/.venv 存在）"
        dim "要改端口 / 证书，用「配置」→「重新走安装向导」；"
        dim "要更新代码，用「升级」。"
        printf '\n  继续安装会覆盖现有代码（数据不受影响）。确认继续？[y/N] '
        local a; read -r a
        [[ "$a" =~ ^[Yy]$ ]] || { log "已取消"; return 0; }
    fi

    local script rc=0
    script="$(find_installer)" || return 1
    [[ -n "$script" ]] || return 1

    log "进入安装向导（域名 / 管理端口 / 证书）"
    printf '\n'

    # 不用 exec：装完要回到菜单，不能把用户丢回 shell 提示符 ——
    # 他可能还想顺手启动 / 看状态。
    #
    # 已经装过（而且用户上面点了 y）-> 显式带上 --reconfigure。
    # 安装向导自己也会拦"已经装过了"，不加这个参数它会直接退出。
    if [[ $already -eq 1 ]]; then
        bash "$script" --reconfigure || rc=$?
    else
        bash "$script" || rc=$?
    fi

    [[ -n "$INSTALLER_TMP" ]] && { rm -f "$INSTALLER_TMP"; INSTALLER_TMP=""; }

    printf '\n'
    if [[ $rc -eq 0 && -x "$SERVER_DIR/.venv/bin/python" ]]; then
        ok "安装完成"
        dim "以后直接敲：canoe    （这个菜单现在装在 $SELF_DEST 了）"
    elif [[ $rc -ne 0 ]]; then
        err "安装向导以退出码 $rc 结束 —— 它上面的输出就是原因"
    fi
    return $rc
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

    local ver active enabled since p pp active_txt health
    ver="$(app_version)"
    active="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
    enabled="$(systemctl is-enabled "$UNIT" 2>/dev/null || true)"

    title "轻舟 / Canoe Server    版本 $ver"

    case "$active" in
        active) active_txt="${C_OK}运行中${C_OFF}" ;;
        failed) active_txt="${C_ERR}启动失败${C_OFF}" ;;
        *)      active_txt="${C_WARN}已停止${C_OFF}（${active:-未知}）" ;;
    esac

    since="$(systemctl show -p ActiveEnterTimestamp --value "$UNIT" 2>/dev/null || true)"
    p="$(client_port)"; pp="$(panel_port)"

    field "服务状态" "$active_txt"
    field "开机自启" "$([[ "$enabled" == "enabled" ]] && echo "是" || echo "否")"
    [[ -n "$since" ]] && field "启动于" "$since"
    field "客户端口" "${p:-?}/tcp  （客户端固定用它取更新和订阅）"
    if [[ -n "$pp" && "$pp" != "$p" ]]; then
        field "面板口" "$pp/tcp"
    else
        field "面板口" "同客户端口"
    fi
    field "面板地址" "$(panel_url)"
    field "证书" "$(env_get TLS_CERT)"

    if health_check; then
        health="${C_OK}通过${C_OFF}  $(health_url)"
    else
        health="${C_ERR}失败${C_OFF}  $(health_url)"
    fi
    printf '\n'
    field "健康检查" "$health"

    # 监听端口一览（有 ss 就用 ss，没有就算了，不报错）
    if command -v ss >/dev/null 2>&1; then
        printf '\n'
        section "监听端口"
        ss -lntp 2>/dev/null | awk -v p="$p" -v pp="$pp" '
            NR==1 { next }
            $4 ~ ":"p"$" || (pp != "" && $4 ~ ":"pp"$") {
                printf "    %-24s %s\n", $4, $6
            }'
    fi

    # 数据库大小
    local db="$SERVER_DIR/data/canoe.db"
    if [[ -f "$db" ]]; then
        printf '\n'
        field "数据库" "$(du -h "$db" 2>/dev/null | cut -f1)"
    fi

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
            field "账号" "正常 ${accounts:-?} 个"
            field "在线会话" "${online:-?} 条"
        fi
    fi

    printf '\n'
    section "最近日志"
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
        printf '%s\n' "$MENU_RULE"
        printf '         %s配置%s\n' "$C_INFO" "$C_OFF"
        printf '%s\n' "$MENU_RULE"
        printf '   1  查看当前配置\n'
        printf '   2  改管理面板端口\n'
        printf '   3  改管理员账号\n'
        printf '   4  用编辑器打开 .env（高级）\n'
        printf '   5  重新走安装向导（域名 / 端口 / 证书）\n'
        printf '   0  返回\n'
        printf '%s\n' "$MENU_RULE"
        printf '  请选择: '
        local c; read -r c
        case "$c" in
            1) show_config ;;
            2) change_port PANEL_PORT "面板端口" ;;
            3) cmd_passwd ;;
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
    # --reconfigure：这条路径本来就是在"已装好"的机器上重走，
    # 安装向导里那道"已经装过了"的闸门得显式放开。
    bash "$script" --reconfigure
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
            dim "              bash $SERVER_DIR/deploy/install.sh --token <新PAT>"
            printf '\n  跳过拉代码，只重装依赖并重启？[y/N] '
            local a; read -r a
            [[ "$a" =~ ^[Yy]$ ]] || { log "已取消"; return 0; }
        fi
    else
        warn "$APP_DIR 不是 git 检出，跳过拉代码"
        dim "手动把新代码放进去，然后再跑一次「升级」"
    fi

    # 1.5) 把管理脚本自己刷新一遍
    #
    # 这一步必须有。`/usr/local/bin/canoe` 是安装那一次拷贝过去的快照，
    # 升级只拉代码不会动它 —— 结果就是：仓库里已经有 `canoe release`
    # 这个子命令了，敲下去却还提示"没有这一项"（真踩过：眼睁睁看着
    # release 用不了，只能 bash .../deploy/canoe.sh release）。
    # 用 -ef 比一下 inode，是同一个文件就说明本来就是从检出目录直接跑的，
    # 不用自己拷自己。
    if [[ -f "$SERVER_DIR/deploy/canoe.sh" && ! "$SERVER_DIR/deploy/canoe.sh" -ef "$SELF_DEST" ]]; then
        install -m 755 "$SERVER_DIR/deploy/canoe.sh" "$SELF_DEST"
        log "管理脚本已刷新：$SELF_DEST"
    fi

    # 2) 重装依赖（canoe-core 可能有新依赖，比如 cryptography）
    log "更新依赖…"
    as_user "
        cd '$SERVER_DIR' &&
        .venv/bin/pip install -q --upgrade pip >/dev/null 2>&1
        .venv/bin/pip install -q -e ../canoe-core &&
        .venv/bin/pip install -q -r requirements.txt
    " || die "依赖安装失败"

    # 3) 数据表补列（新版本可能加了字段）
    log "对齐数据库结构…"
    as_user "cd '$SERVER_DIR' && .venv/bin/python -c '
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
        dim "回滚：git -C $APP_DIR checkout <上一个 tag> 然后 canoe restart"
        return 1
    fi
    hr
}

# ---------------------------------------------------------------------------
# 8. 卸载
# ---------------------------------------------------------------------------
# ★ 用户对卸载的要求是"**0 残留**"。要做到这点，前提是**有一份完整的足迹
#   清单**：装的时候往哪些地方写过东西，卸的时候就得一处不落地收回来。
#   install.sh 在 $APP_DIR / $CERT_DIR 之外还写过这些 ——
#
#     · /etc/systemd/system/canoe-api.service（+ enable 生成的 wants 软链）
#     · /usr/local/bin/canoe
#     · /etc/letsencrypt/renewal-hooks/deploy/canoe.sh
#         ← 漏了它的后果：certbot 每次续期都去 restart 一个已经不存在的服务
#     · /root/.gitconfig 里的 safe.directory=<APP_DIR>
#     · ufw 的 allow <端口>/tcp
#         ← 漏了它的后果：防火墙上一个洞永远留着，没人知道是谁开的
#     · 系统用户 canoe（和同名组）
#
#   这份清单由 footprint() **一处说了算** —— 真删、--dry-run、卸完的自检
#   三者共用它，就不会出现"自检说干净了、其实还留着"这种自欺。
#
# ⚠ 有两样**故意不删**，因为它们是共享的、清了会影响别的软件：
#   apt 包、journald 历史日志。certbot 那个真证书也不动（用户选的"只提示"）。

CERT_HOOK="/etc/letsencrypt/renewal-hooks/deploy/canoe.sh"
UNIT_WANTS="/etc/systemd/system/multi-user.target.wants/$UNIT.service"
GITCONFIG="${GIT_CONFIG_GLOBAL:-${HOME:-/root}/.gitconfig}"
#: 卸载时在删掉 .env **之前**抓下来的端口（"58588 8899"）。自检要用它查 ufw。
UFW_PORTS=""

#: /root/.gitconfig 里有没有我们那条 safe.directory。
#: 只看文件文本、不调 git —— 卸载可能发生在 git 已经被删掉的机器上。
gitconfig_has_entry() {
    [[ -f "$GITCONFIG" ]] || return 1
    grep -qE "^[[:space:]]*directory[[:space:]]*=[[:space:]]*${APP_DIR//\//\\/}[[:space:]]*$" \
        "$GITCONFIG" 2>/dev/null
}

#: 这台机器上**还留着**的 canoe 足迹。一行一条：`标签|路径`。
footprint() {
    local p
    [[ -e "$UNIT_FILE"  ]] && printf '%s|%s\n' "单元"     "$UNIT_FILE"
    [[ -e "$UNIT_WANTS" ]] && printf '%s|%s\n' "软链"     "$UNIT_WANTS"
    [[ -e "$SELF_DEST"  ]] && printf '%s|%s\n' "管理脚本" "$SELF_DEST"
    [[ -d "$APP_DIR"    ]] && printf '%s|%s\n' "程序目录" "$APP_DIR"
    [[ -d "$CERT_DIR"   ]] && printf '%s|%s\n' "证书副本" "$CERT_DIR"
    [[ -e "$CERT_HOOK"  ]] && printf '%s|%s\n' "续期hook" "$CERT_HOOK"
    id -u "$APP_USER" >/dev/null 2>&1       && printf '%s|%s\n' "系统用户" "$APP_USER"
    getent group "$APP_USER" >/dev/null 2>&1 && printf '%s|%s\n' "用户组"  "$APP_USER"
    gitconfig_has_entry && printf '%s|%s\n' "git配置" "$GITCONFIG（safe.directory=$APP_DIR）"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
        for p in $UFW_PORTS; do
            ufw status 2>/dev/null | grep -qE "^${p}/tcp" \
                && printf '%s|%s\n' "ufw规则" "${p}/tcp"
        done
    fi
    return 0
}

#: 把足迹按统一版式打出来。dry-run 和"确认前先看一眼"都用它。
print_footprint() {
    local lines label path
    lines="$(footprint)"
    if [[ -z "$lines" ]]; then
        dim "什么都没有 —— 本来就没装过，或者已经卸干净了"
        return 0
    fi
    while IFS='|' read -r label path; do
        [[ -n "$label" ]] || continue
        printf '  %s·%s %s %s\n' "$C_DIM" "$C_OFF" "$(pad_label "$label" 13)" "$path"
    done <<< "$lines"
    return 0
}

# ---- 三个"叶子动作" ------------------------------------------------------
# 删东西只走这三个口子，失败一律吞掉（少删一个比中断卸载好，而且最后
# 有自检兜底）。放这三个口子的意义是：想知道"卸载到底动了哪些东西"，
# 看这三个函数就够，不用满脚本 grep rm -rf。
rm_file() { rm -f "$1" 2>/dev/null || true; }
rm_tree() { rm -rf "$1" 2>/dev/null || true; }
run_cmd() { "$@" >/dev/null 2>&1 || true; }

#: 删掉 /root/.gitconfig 里我们那一条 safe.directory。
#:
#: ⚠ 只删**值恰好等于 $APP_DIR** 的那一条，绝不能碰用户别的 safe.directory。
#:   所以：按 [safe] 段逐行比对、值全等才丢；万一 [safe] 段变空了，把段头
#:   也一起丢（git 不喜欢空段）。最后还有一道"结果最多只该少 1~2 行"的
#:   断言 —— 宁可什么都不做，也不能把人家整份 .gitconfig 弄坏。
purge_gitconfig_entry() {
    gitconfig_has_entry || return 0

    local tmp1 tmp2 before after
    tmp1="$(mktemp)"; tmp2="$(mktemp)"
    awk -v target="$APP_DIR" '
        /^[[:space:]]*\[/ {
            s = tolower($0); gsub(/[[:space:]\[\]]/, "", s); sub(/\..*/, "", s)
            sect = s; print; next
        }
        {
            if (sect == "safe" && $0 ~ /^[[:space:]]*directory[[:space:]]*=/) {
                v = $0; sub(/^[^=]*=[[:space:]]*/, "", v)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", v)
                if (v == target) next        # 精确命中，丢掉
            }
            print
        }
    ' "$GITCONFIG" > "$tmp1" 2>/dev/null || { rm -f "$tmp1" "$tmp2"; return 0; }

    awk '
        { L[NR] = $0 }
        END {
            for (i = 1; i <= NR; i++) {
                if (L[i] ~ /^[[:space:]]*\[[[:space:]]*[sS][aA][fF][eE][[:space:]]*\]/) {
                    j = i + 1
                    while (j <= NR && L[j] ~ /^[[:space:]]*$/) j++
                    if (j > NR || L[j] ~ /^[[:space:]]*\[/) continue   # 空段，段头也别留
                }
                print L[i]
            }
        }
    ' "$tmp1" > "$tmp2" 2>/dev/null || { rm -f "$tmp1" "$tmp2"; return 0; }

    before="$(wc -l < "$GITCONFIG")"
    after="$(wc -l < "$tmp2")"
    if (( before - after >= 0 && before - after <= 2 )); then
        cat "$tmp2" > "$GITCONFIG"
        log "已清掉 $GITCONFIG 里的 safe.directory=$APP_DIR"
    else
        warn "$GITCONFIG 改动的行数不对劲（$before -> $after），没敢动它"
    fi
    rm -f "$tmp1" "$tmp2"
}

#: 删 ufw 放行规则。ufw 不存在或没启用时静默跳过。
purge_ufw_rules() {
    command -v ufw >/dev/null 2>&1 || return 0
    ufw status 2>/dev/null | grep -q '^Status: active' || return 0
    local p
    for p in $UFW_PORTS; do
        [[ -n "$p" ]] || continue
        ufw status 2>/dev/null | grep -qE "^${p}/tcp" || continue
        if ufw delete allow "$p"/tcp >/dev/null 2>&1; then
            log "ufw 规则 ${p}/tcp 已移除"
        else
            warn "ufw 规则 ${p}/tcp 删除失败（手动：ufw delete allow ${p}/tcp）"
        fi
    done
}

#: 删系统用户和同名组。删之前先确认没人在用 —— 有就留着，绝不强删。
teardown_user_group() {
    id -u "$APP_USER" >/dev/null 2>&1 || return 0

    # 还有以它身份跑的进程就先收掉（服务已经停了，这里只是兜底）
    if pgrep -u "$APP_USER" >/dev/null 2>&1; then
        warn "还有进程以 $APP_USER 身份在跑，先结束它们"
        pkill -u "$APP_USER" 2>/dev/null || true
        sleep 1
    fi

    userdel "$APP_USER" >/dev/null 2>&1 \
        && log "系统用户 $APP_USER 已删除" \
        || warn "userdel 失败（可能仍有进程占用）"

    # 组：useradd -r 在 Debian 上会顺手建同名组。删之前确认没别人用它。
    if getent group "$APP_USER" >/dev/null 2>&1; then
        local others
        others="$(awk -F: -v g="$APP_USER" '$1 != g && $4 == g { print $1 }' /etc/passwd)"
        others="$others $(awk -F: -v g="$APP_USER" '$1 == g { print $4 }' /etc/group)"
        if [[ -n "${others// /}" ]]; then
            warn "组 $APP_USER 还有成员（${others}），保留不动"
        elif groupdel "$APP_USER" >/dev/null 2>&1; then
            log "用户组 $APP_USER 已删除"
        else
            warn "groupdel 失败"
        fi
    fi
}

#: 卸载收尾的自检：足迹清单为空才算干净。
#: 有残留时**返回非 0** —— 让脚本化调用（`canoe uninstall && ...`）也能看出来。
verify_uninstall() {
    local lines
    lines="$(footprint)"

    printf '\n'
    hr
    printf '  %s残留自检%s\n' "$C_INFO" "$C_OFF"
    hr
    if [[ -z "$lines" ]]; then
        printf '  %s[+]%s 所有足迹已清除\n' "$C_OK" "$C_OFF"
        hr
        return 0
    fi
    local label path
    while IFS='|' read -r label path; do
        [[ -n "$label" ]] || continue
        printf '  %s[x]%s 残留  %s %s\n' "$C_ERR" "$C_OFF" "$(pad_label "$label" 13)" "$path"
    done <<< "$lines"
    hr
    err "上面这些没清干净 —— 按路径手动删一下"
    return 1
}

#: 故意保留的东西，说清楚为什么、以及要清的话怎么清。
print_retained_note() {
    local domain="$1"
    printf '\n'
    dim "下面这些**故意没删** —— 都是共享的，清了会影响别的软件："
    dim "  · apt 包（python3 / certbot / rsync 等）   要清：apt-get autoremove --purge"
    dim "  · journald 里 -u $UNIT 的历史日志"
    dim "       要清：journalctl --rotate && journalctl --vacuum-time=1s"
    dim "             （注意：这条会把**整台机器**的日志一起清掉）"
    dim "  · /var/log/letsencrypt（certbot 自己的日志目录）"

    if [[ -n "$domain" && -d "/etc/letsencrypt/live/$domain" ]]; then
        printf '\n'
        log "证书还在：/etc/letsencrypt/live/$domain"
        dim "  本脚本不碰别人的证书。要一起删：certbot delete --cert-name $domain"
    fi
}

cmd_uninstall() {
    need_root

    ACT_DRY=0
    case "${1:-}" in
        --dry-run|-n) ACT_DRY=1 ;;
        "")           ;;
        *)            err "不认识的参数：$1"; dim "  用法：canoe uninstall [--dry-run]"; return 1 ;;
    esac

    # ⚠ 端口必须在**删 .env 之前**抓出来 —— 自检还要用它去查 ufw 规则，
    #   等 .env 没了再读就读到空串了（踩过这种"删完才想起来要用"的坑）。
    UFW_PORTS="$(client_port) $(panel_port)"
    local domain; domain="$(domain_display)"; [[ "$domain" == "<本机IP>" ]] && domain=""

    title "卸载 Canoe"

    if (( ACT_DRY )); then
        printf '  %s这是空跑 —— 只列清单，什么都不动。%s\n\n' "$C_WARN" "$C_OFF"
        print_footprint
        printf '\n'
        log "真跑的话把 --dry-run 去掉就行：canoe uninstall"
        return 0
    fi

    printf '  %s将要删除（数据一起，不可恢复）：%s\n\n' "$C_WARN" "$C_OFF"
    print_footprint
    printf '\n'
    printf '  确认卸载？[y/N] '
    local a; read -r a
    [[ "$a" =~ ^[Yy]$ ]] || { log "已取消"; return 0; }
    printf '\n'

    # 顺序不能乱：先停服务（不然文件被占着、userdel 也会失败）→ 再拆单元
    # → 再删文件 → 最后才删用户/组。
    if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
        log "停止 $UNIT"
        run_cmd systemctl stop "$UNIT"
    fi
    run_cmd systemctl disable "$UNIT"
    rm_file "$UNIT_FILE"
    rm_file "$UNIT_WANTS"
    run_cmd systemctl daemon-reload
    run_cmd systemctl reset-failed "$UNIT"

    # 续期 hook 要在拆完单元之后删 —— 它内容是 restart canoe-api，
    # 万一 certbot 正好在跑，先拆单元再删它就没人会去把服务拉起来。
    rm_file "$CERT_HOOK"
    # 目录空了才 rmdir（非空会失败）。绝不用 rm -rf —— 里面可能还有别人的 hook。
    if (( ! ACT_DRY )); then
        rmdir "$(dirname "$CERT_HOOK")" 2>/dev/null || true
    fi

    rm_file "$SELF_DEST"
    rm_tree "$APP_DIR"
    rm_tree "$CERT_DIR"
    purge_gitconfig_entry
    purge_ufw_rules
    teardown_user_group

    verify_uninstall
    local clean=$?
    printf '\n'
    if (( clean == 0 )); then
        ok "卸载完成"
    else
        warn "卸载完成，但上面列的东西没清掉"
    fi
    print_retained_note "$domain"
    return "$clean"
}

# ---------------------------------------------------------------------------
# 附加：日志 / 改管理员账号 / 版本
# ---------------------------------------------------------------------------
cmd_logs() {
    require_installed
    log "跟随 $UNIT 日志（Ctrl-C 退出）"
    journalctl -u "$UNIT" -f -n 50
}

#: 管理员账号 = 用户名 + 密码，一起改。两样都留空就直接返回，
#: 免得手滑回车把账号改成空字符串。
cmd_passwd() {
    need_root; require_installed

    local cur user p1 p2
    cur="$(env_get ADMIN_USERNAME)"; cur="${cur:-admin}"

    printf '  现在的管理员账号：%s\n' "$cur"
    printf '  新用户名（3-32 位字母数字下划线，回车不改）[%s]: ' "$cur"
    read -r user; user="${user:-$cur}"

    printf '  新密码（至少 8 位，回车不改）: '
    read -rs p1; printf '\n'
    if [[ -n "$p1" ]]; then
        printf '  再输一遍: '
        read -rs p2; printf '\n'
    fi

    if [[ "$user" == "$cur" && -z "$p1" ]]; then
        warn "用户名和密码都没改，返回"; return 0
    fi
    if [[ ! "$user" =~ ^[A-Za-z0-9_-]{3,32}$ ]]; then
        die "用户名只能是 3-32 位的字母、数字、下划线或减号"
    fi
    if [[ -n "$p1" ]]; then
        [[ ${#p1} -ge 8 ]] || die "密码太短了，至少 8 位"
        [[ "$p1" == "$p2" ]] || die "两次密码不一致"
    fi

    as_user "
        cd '$SERVER_DIR' && \
        CO_OLD_USER='$cur' CO_NEW_USER='$user' CO_NEW_PW='$p1' .venv/bin/python - <<'PY'
import os

from sqlalchemy import select

from canoe_server.database import SessionLocal
from canoe_server.models import User
from canoe_server.security import hash_password

old, new = os.environ['CO_OLD_USER'], os.environ['CO_NEW_USER']
pw = os.environ.get('CO_NEW_PW') or ''

with SessionLocal() as db:
    u = db.scalars(select(User).where(User.username == old)).first()
    if u is None:
        raise SystemExit(f'找不到管理员 {old}')
    if new != old:
        taken = db.scalars(select(User).where(User.username == new)).first()
        if taken is not None:
            raise SystemExit(f'用户名 {new} 已经被占用了')
        u.username = new
    if pw:
        u.password_hash = hash_password(pw)
    db.commit()

done = []
if new != old:
    done.append(f'用户名 {old} -> {new}')
if pw:
    done.append('密码已更新')
print('    ' + '；'.join(done))
PY
" || die "没改成 —— 上面应该有原因"

    # .env 里那份是给 seed.py 初次建号用的，跟着改掉免得对不上。
    # （账号已经在库里了，改这里不会新建一个。）
    env_set ADMIN_USERNAME "$user"

    ok "改好了。下次登面板用新账号。"
    dim "旧令牌不会自动失效 —— 要踢掉所有已登录的会话，用「面板 → 用户 → 封禁再解封」。"
}

#: `canoe release` 的用法。单独一个函数，免得和主 usage() 两处维护。
usage_release() {
    title "发布客户端安装包"
    printf '  canoe release               拉最新那个 Release 发出去\n'
    printf '  canoe release v1.0.31       拉指定 tag\n'
    printf '  canoe release --list        看看已经发了哪些\n'
    hr
    dim "包挂在 GitHub 的 Release 上，服务器自己去拉 —— 不用把安装包传上来。"
    dim "开发机那边：scripts/package_release.py 打出 zip 和 .sha256，"
    dim "两份一起挂到 Release（tag 形如 v1.0.31）。服务端会把 .sha256"
    dim "读回来核对，对不上就整个丢掉。"
}

cmd_release() {
    # --help 先处理：要个用法说明而已，不该因为"还没装代码"就被顶回去。
    case "${1:-}" in
        -h|--help|help)
            usage_release
            return 1
            ;;
    esac

    need_root; require_installed

    local script="$SERVER_DIR/release.py"
    if [[ ! -f "$script" ]]; then
        err "找不到 $script —— 这台的代码是旧版，先跑：canoe upgrade"
        return 1
    fi

    local tag="${1:-}"
    case "$tag" in
        --list|-l)
            # 以应用用户的身份跑：releases/ 和 sqlite 都是它的，
            # root 跑会写出 root 属主的文件，之后服务自己就写不动了。
            as_user "cd '$SERVER_DIR' && .venv/bin/python release.py --list"
            return $?
            ;;
        -*)
            err "不认识的参数：$tag"
            usage_release
            return 1
            ;;
    esac

    # ⚠ 只接受 tag。**发本机文件那条路已经废掉了** —— 以前是 scp 上来再
    #   `canoe release /tmp/xxx.zip`，那条路断过两次：/tmp 里留下 46MB 的
    #   半截包，而服务端是按**落盘的字节**算摘要的，残包自洽，于是被当成
    #   合法版本发了出去。现在唯一入口是 GitHub Release，摘要是开发机算好
    #   挂上去的，跟这条下载链路无关。
    if [[ "$tag" == */* || "$tag" == *.zip ]]; then
        err "$tag 看着像个文件 —— 现在不发本机文件了。"
        dim "  包要先挂到 GitHub Release 上（见 canoe release --help）。"
        return 1
    fi

    log "从 GitHub 拉取${tag:+（tag $tag）}并发布…"
    # 逐个参数 printf %q 再拼 —— 直接塞 "$*" 的话带空格的参数会被拆开。
    local quoted=""
    [[ -n "$tag" ]] && quoted=" $(printf '%q' "$tag")"
    as_user "cd '$SERVER_DIR' && .venv/bin/python release.py --github$quoted"
    local rc=$?
    if (( rc != 0 )); then
        err "发布没成功（退出码 $rc）—— 上面那几行就是原因"
        return "$rc"
    fi
    ok "发布完成 —— 客户端点「更新」就能看到了"
}

cmd_version() {
    local ver
    ver="$(app_version)"; [[ -n "$ver" ]] || ver="?"
    title "版本"
    field "管理脚本" "$([[ -f "$SELF_DEST" ]] && echo "$SELF_DEST" || echo "（未安装到 PATH）")"
    if installed; then
        field "服务端" "${ver}   （$SERVER_DIR）"
    else
        field "服务端" "未安装"
    fi
    hr
}

# ---------------------------------------------------------------------------
# 菜单
# ---------------------------------------------------------------------------
#: 菜单宽度（按用户给的样式，31 个横线）
MENU_RULE="-------------------------------"

show_menu() {
    clear 2>/dev/null || true
    printf '%s\n' "$MENU_RULE"
    printf '         %s服务端管理脚本%s\n' "$C_INFO" "$C_OFF"
    printf '%s\n' "$MENU_RULE"
    printf '   1  安装 Canoe\n'
    printf '   2  启动 Canoe\n'
    printf '   3  停止 Canoe\n'
    printf '   4  重启 Canoe\n'
    printf '   5  Canoe 状态\n'
    printf '   6  Canoe 配置\n'
    printf '   7  升级 Canoe\n'
    printf '   8  卸载 Canoe\n'
    printf '   0  退出\n'
    printf '%s\n' "$MENU_RULE"
    printf '  请选择: '
}

menu() {
    while true; do
        show_menu
        local choice
        read -r choice || { printf '\n'; return 0; }

        # 还没装的时候除了「安装」和「退出」，其余都不该往下走 ——
        # 否则会打一堆 require_installed 的报错，白折腾
        if ! installed && [[ "$choice" != "1" && "$choice" != "0" && -n "$choice" ]]; then
            warn "还没安装，先选 1"
            printf '\n  回车继续…'; read -r _ || true
            continue
        fi

        case "$choice" in
            1) cmd_install ;;
            2) cmd_start ;;
            3) cmd_stop ;;
            4) cmd_restart ;;
            5) cmd_status ;;
            6) cmd_config ;;
            7) cmd_upgrade ;;
            8) cmd_uninstall ;;
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
    canoe                     交互菜单
    canoe <命令>              直接用子命令

命令：
    install     安装（交给安装向导，选域名 / 管理端口 / 证书）
    start       启动服务
    stop        停止服务
    restart     重启服务
    status      查看状态（端口 / 健康检查 / 在线人数 / 最近日志）
    config      改端口 / 证书 / 配置
    upgrade     拉代码 + 更新依赖 + 对齐表结构 + 重启
    uninstall   卸载（全清：程序 / 数据 / 单元 / 系统用户；--dry-run 先看一眼）
    logs        跟随日志
    passwd      改管理员账号（用户名 / 密码）
    release     发布客户端安装包（从 GitHub Release 拉；--list 看已发的）
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
        uninstall|remove) cmd_uninstall "$@" ;;
        logs|log)  cmd_logs ;;
        passwd|admin) cmd_passwd ;;
        release|rel|publish) cmd_release "$@" ;;
        version|-v|--version) cmd_version ;;
        help|-h|--help) usage ;;
        *) err "不认识的命令：$cmd"; printf '\n'; usage; exit 1 ;;
    esac
}

# 直接执行时才进 main；被 source 时只当库用（测试就是那么用的）。
#
# 判据要同时认三种调用方式，缺一个就会"静默什么都不做"：
#   bash canoe.sh             BASH_SOURCE[0]=脚本、$0=脚本      -> 相等，跑
#   bash -c "$(curl …)"       BASH_SOURCE 为空、$0="bash"       -> 空，跑
#   source canoe.sh（测试）    BASH_SOURCE[0]=脚本、$0=调用方   -> 不等，不跑
#
# 只写业内那句惯用法 `[[ ${BASH_SOURCE[0]} == $0 ]]` 会漏掉中间那种 ——
# 而中间那种正是我们推荐的一行安装方式，结果是菜单一个字都不显示。
if [[ -z "${BASH_SOURCE[0]:-}" || "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
