#!/usr/bin/env bash
#
# 轻舟 / Canoe Server —— 安装向导（Debian / Ubuntu）
#
#   bash deploy/install.sh                      # 一路问下来
#   bash deploy/install.sh --domain canoe.s-ui.com --port 58588 --cert-mode le
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
REPO_TOKEN="${CANOE_TOKEN:-}"     # 环境变量也行，免得令牌出现在 ps 里
DO_SEED=1
#: 已经装过的机器要重走向导，必须显式点头（见下面 0.05 那段）
RECONFIGURE="${CANOE_RECONFIGURE:-0}"

#: 两个脚本在仓库里的地址。被 curl 起来跑时原生路径取不到，写死一份。
RAW_BASE="https://raw.githubusercontent.com/NekoBoxHQ/canoe/main/canoe-server/deploy"
SELF_RAW="$RAW_BASE/install.sh"
MENU_RAW="$RAW_BASE/canoe.sh"

#: 管理员账号。装的**时候**就问，不再自己随机生成塞进文件里 ——
#: 之前那样用户装完满世界找密码，是真的难用。
ADMIN_USER=""
ADMIN_PASS=""
ADMIN_GENERATED=0

#: 项目的默认地址。不在检出目录里跑、又没给 --repo 时用它。
DEFAULT_REPO="${CANOE_REPO:-https://github.com/NekoBoxHQ/canoe.git}"

# ---- 输出 ----------------------------------------------------------------
#
# ⚠ 下面这几个函数在 canoe.sh 里有一份**一模一样**的，是**故意重复**的：
#   install.sh 用在"机器上还什么都没有"的时候 —— 它可能要先从 GitHub 把
#   canoe.sh 取回来，在那之前它谁也 source 不到，排版基建只能自带。
#   改这里记得两边一起改（test_canoe_sh.sh 里对两边都钉了行为）。
#
#   颜色只在 tty 上给：输出重定向到文件/管道时全是空的，
#   不会在日志里塞一堆 \033[。
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
die()  { err "$*"; exit 1; }
hr()   { printf '%s\n' "------------------------------------------------------------"; }

#: 标签列补到多少显示列（跟 canoe.sh 一致）
FIELD_WIDTH=10

#: 字符串的**显示宽度**（不是字符数）：CJK 算 2 列，其余算 1。
#: ⚠ 必须 local —— 裸写 `LC_ALL=C` 会把 locale 泄漏给整个脚本。
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

#: 标签补到 n 个显示列（不能用 printf 的 %-Ns —— 它按字节算）。
pad_label() {
    local w pad
    w="$(disp_width "$1")"
    pad=$(( $2 - w )); (( pad < 1 )) && pad=1
    printf '%s%*s' "$1" "$pad" ""
}

#: 一行键值：两空格缩进、标签补到 FIELD_WIDTH 显示列、冒号对齐。
field() {
    local label="$1"; shift
    local w pad
    w="$(disp_width "$label")"
    pad=$(( FIELD_WIDTH - w )); (( pad < 1 )) && pad=1
    printf '  %s%*s : %s\n' "$label" "$pad" "" "$*"
}

#: 小节标题：两空格缩进 + 上下两条横线。
title() {
    hr
    printf '  %s%s%s\n' "$C_INFO" "$*" "$C_OFF"
    hr
}

# 以 $APP_USER 的身份跑一条命令（会交给 bash -c）。
#
# ⚠ Debian 最小安装**不带 sudo** —— 那是个独立软件包。本脚本要求以 root
#   运行，所以根本不需要提权工具，但也绝不能假设 sudo 在：
#   一开始写死 `sudo -u canoe`，在刚装好的 Debian 12 上直接
#   `sudo: command not found` 把安装打断（真踩过）。
#   优先 sudo（保留环境最省心），退而 runuser（util-linux 自带，debian 必有），
#   最后 su 兜底。
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

# ---- 失败回滚 -------------------------------------------------------------
#
# set -e 有个很不友好的地方：某条命令失败时它**一声不吭就退出**。
# 用户看到的是"向导问完了，命令结束，什么都没发生"，完全无从下手
# （真踩过：openssl 校验在 EC 密钥上失败，整个安装在最后一步静默中止）。
#
# 所以这里做两件事：先说清楚"在哪一行、跑的什么"，再把**本次新建的**
# 系统对象收回去。
#
# ★ `NEW_*` 那组标记是关键：只在"创建那一刻它还不存在"时才置 1，回滚只
#   处理置了标记的。这样**绝不会**把机器上原本就有的东西删掉 —— 重跑一次
#   install（对象都在）时，回滚实际上什么都不会做，这正是要的。
#
# ⚠ apt 装过的包不回收：删包比留着更危险，而且卸载那边也把它算作"故意保留"。
NEW_USER=0; NEW_APP_DIR=0; NEW_CERT_DIR=0
NEW_UNIT=0; NEW_HOOK=0; NEW_SELF=0
ROLLING=0

rollback() {
    local rc=$?
    (( ROLLING )) && return 0
    ROLLING=1
    trap - ERR EXIT
    (( rc == 0 )) && exit 0

    err "脚本在这里中断了（退出码 $rc）—— 上面那几行就是原因"
    dim "这不是设计好的报错，是没兜住的失败。把上面几行贴给开发者。"
    if (( NEW_UNIT || NEW_HOOK || NEW_SELF || NEW_CERT_DIR || NEW_APP_DIR || NEW_USER )); then
        warn "回滚本次新建的东西（机器上原有的不动）…"
        if (( NEW_UNIT )); then
            systemctl disable --now canoe-api >/dev/null 2>&1 || true
            rm -f /etc/systemd/system/canoe-api.service
            systemctl daemon-reload >/dev/null 2>&1 || true
        fi
        (( NEW_HOOK ))     && rm -f /etc/letsencrypt/renewal-hooks/deploy/canoe.sh 2>/dev/null || true
        (( NEW_SELF ))     && rm -f /usr/local/bin/canoe 2>/dev/null || true
        (( NEW_CERT_DIR )) && rm -rf "$CERT_DIR" 2>/dev/null || true
        (( NEW_APP_DIR ))  && rm -rf "$APP_DIR"  2>/dev/null || true
        if (( NEW_USER )); then
            userdel "$APP_USER"  >/dev/null 2>&1 || true
            groupdel "$APP_USER" >/dev/null 2>&1 || true
        fi
        dim "已回到安装前。"
    else
        dim "没有本次新建的东西要收 —— 机器上原有的都原样留着。"
    fi
    exit "$rc"
}
trap rollback ERR
trap rollback EXIT

usage() {
    cat <<'EOF'
轻舟 / Canoe Server —— 安装向导（Debian / Ubuntu）

  bash deploy/install.sh
  bash deploy/install.sh --domain canoe.s-ui.com --port 58588 --cert-mode le
  bash deploy/install.sh --domain x.com --port 58588 --panel-port 58589

不带参数会一项一项问你：域名 / 管理面板端口 / 证书。

客户端口**不问**，固定 58588 —— 客户端把它写死在代码里了，改了就失联。

默认是**直连模式**：uvicorn 自己监听在指定端口上做 HTTPS，不需要 Nginx。

幂等：重复跑不会覆盖已有的 .env / 数据库 / 证书。

选项：
  --domain NAME         域名（用于证书和客户端下载地址；没有就留空）
  --port N              客户端口，默认 58588。**一般不要动** ——
                        客户端把地址写死在代码里，改了已发出的客户端全失联。
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
  --admin-user NAME     管理员用户名（默认 admin；不问就回车）
  --admin-pass PASS     管理员密码。不传会在向导里问你；
                        都不给才随机生成一个（会打印出来）
  --repo URL            从哪个仓库拉代码（默认见下）
  --token TOKEN         GitHub 私有仓库用的访问令牌（PAT），只读权限即可。
                        只在克隆那一次用到，用完立刻从 remote 里擦掉。
  --no-seed             跳过种子数据
  --reconfigure         已经装过的机器上，强制重走一遍向导。
                        不加这个：检测到装完了就停下来，告诉你用 `canoe`
                        那个管理菜单 —— 免得每次改点东西都从「[1/3] 域名」问起。
  -h, --help            看这个

  --https 是 --cert-mode 的旧名字，仍然能用。

代码从哪来
------------------------------------------------------------------
  1) 在检出目录里跑（最常见）—— 直接把当前这份代码同步到 /opt/canoe
         bash canoe-server/deploy/install.sh

  2) 不在检出目录里跑 —— 脚本自己去仓库拉
         bash install.sh --repo https://github.com/NekoBoxHQ/canoe.git

  私有仓库有两条路（二选一，都是只读）：
    · Deploy Key（推荐，一次配好，以后 `canoe upgrade` 直接能拉）
        ssh-keygen -t ed25519 -f ~/.ssh/canoe -N ""
        # 把 ~/.ssh/canoe.pub 加到仓库 Settings -> Deploy keys（不要勾写入）
        bash install.sh --repo git@github.com:NekoBoxHQ/canoe.git
    · PAT（临时用，方便）
        bash install.sh --token <你的PAT>
      注意：令牌用完会被擦掉，所以之后 `canoe upgrade` 拉不动，
      得重新给一次。想一劳永逸就用上面的 Deploy Key。
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
        --token)                REPO_TOKEN="${2:-}"; shift 2 ;;
        --admin-user)           ADMIN_USER="${2:-}"; shift 2 ;;
        --admin-pass)           ADMIN_PASS="${2:-}"; shift 2 ;;
        --no-seed)              DO_SEED=0; shift ;;
        --reconfigure|--force)  RECONFIGURE=1; shift ;;
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
# 0.05 已经装过就别再走一遍安装向导
#
# 这条是被用户骂出来的：服务端明明跑着了，重新执行一遍安装命令，迎面
# 又是「[1/3] 域名」—— 看着像要把装好的东西推倒重来。其实他多半只是
# 想打开管理菜单，只是不知道该敲哪条命令。
#
# 所以：检测到**装完了**就停下，把菜单那条命令给他；真要重装得显式说
# （--reconfigure 或 CANOE_RECONFIGURE=1）。
#
# 判据故意取三个都齐 —— .venv + .env + systemd 单元。只看 .venv 的话，
# 上一轮装到一半崩掉的机器会被这条挡住，而那正是最该重跑的场合。
# ---------------------------------------------------------------------------
if [[ -d "$SERVER_DIR/.venv" && -f "$SERVER_DIR/.env" \
      && -f /etc/systemd/system/canoe-api.service && "$RECONFIGURE" != "1" ]]; then
    cat <<TXT

  这台机器上已经装过 Canoe 了（$APP_DIR）。
  安装向导不再往下走 —— 它问的那些（域名 / 端口 / 证书）现在是好的。

  平时请用**管理脚本**，安装 / 启动 / 停止 / 状态 / 配置 / 升级 / 卸载
  都在那一个菜单里：

      canoe

  菜单没装到 PATH 上？取一份来跑：

      bash -c "\$(curl -fsSL $MENU_RAW)"

  真要重走一遍安装向导（改域名 / 端口 / 证书；代码会被覆盖，数据不动）：

      CANOE_RECONFIGURE=1 bash -c "\$(curl -fsSL $SELF_RAW)"

TXT
    exit 0
fi

# ---------------------------------------------------------------------------
# 0.1 交互向导：域名 / 管理面板端口 / 证书
#
# ⚠ 这里**不问客户端口**。客户端把服务端地址（含 58588）写死在代码里了，
#   改了这个口，所有已经发出去的客户端当场全部失联 —— 那不是"选项"，
#   是个改了就得重发客户端的陷阱。所以它固定 58588，只在最后确认页
#   作为信息展示一次，要用 --port 显式指定才改得动。
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

    if [[ -z "$PANEL_PORT" ]]; then
        cat <<'TXT'
[2/4] 管理面板端口
      想只对自己开放的话，给面板另开一个口 —— 然后在防火墙/安全组里
      只放行你自己的 IP。那样客户端那个口就**不再响应 /panel**。
      两个口是同一个进程在听，推送照常互通。
TXT
        read -rp "      面板端口（回车 = 和客户端同口）: " PANEL_PORT || true
    fi

    if [[ -z "$ADMIN_PASS" ]]; then
        cat <<'TXT'
[3/4] 管理员账号
      登录管理面板用它。用户名留空就是 admin。
TXT
        read -rp "      管理员用户名 [admin]: " ADMIN_USER || true
        ADMIN_USER="${ADMIN_USER:-admin}"

        # 密码让用户自己设，不替他随机生成 —— 生成一堆乱码塞进文件里，
        # 用户装完得满世界找，这是实打实的难用。
        # 连问两遍，且不回显。真不想设（回车两次）才随机生成一个。
        local pw1 pw2
        while true; do
            read -rsp "      管理员密码（至少 8 位，回车 = 随机生成）: " pw1 || true
            printf '\n'
            if [[ -z "$pw1" ]]; then
                break
            fi
            if [[ ${#pw1} -lt 8 ]]; then
                warn "太短了，至少 8 位"
                continue
            fi
            read -rsp "      再输一遍: " pw2 || true
            printf '\n'
            if [[ "$pw1" != "$pw2" ]]; then
                warn "两次输入不一致，重来"
                continue
            fi
            ADMIN_PASS="$pw1"
            break
        done
        unset pw1 pw2
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

# 客户端口固定 58588：客户端把这个地址写死在代码里了。
# 只有显式给 --port 才允许改（那是知道自己在干什么的人）。
PORT="${PORT:-58588}"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "端口必须是数字：$PORT"

# 管理员账号：非交互（给了 --admin-pass）时用户名也要有默认值
ADMIN_USER="${ADMIN_USER:-admin}"
if [[ -n "$ADMIN_PASS" && ${#ADMIN_PASS} -lt 8 ]]; then
    die "管理员密码至少 8 位（现在 ${#ADMIN_PASS} 位）"
fi
[[ "$ADMIN_USER" =~ ^[A-Za-z0-9_-]{3,32}$ ]] || die "管理员用户名要是 3-32 位字母数字下划线：$ADMIN_USER"
if [[ "$PORT" != "58588" ]]; then
    warn "客户端口不是默认的 58588 —— 已发出的客户端会连不上。"
    warn "确认你要这么干（换端口 = 重发一版客户端）。"
fi

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

    # 证书和私钥是不是一对（很常见的错配）。
    #
    # ⚠ 两处讲究，都是踩出来的：
    #   1) 每一步都跟 `|| true`。脚本开头是 set -euo pipefail，命令替换
    #      一旦返回非 0，赋值语句就会触发 set -e，**一声不吭地退出** ——
    #      用户看到的是"敲完最后一项，命令结束了，什么都没发生"。
    #      这个检查只是提示性的，它跑不动不该把整个安装中止。
    #   2) 用 -pubkey 比公钥，不用 -modulus 比模数。模数那套只对 RSA 有效，
    #      遇到 EC / Ed25519 密钥时 openssl rsa 直接失败（就是上面第 1 条
    #      触发的那一刻）。公钥比对对任何密钥类型都成立。
    _cert_pub="$(openssl x509 -in "$CERT_FILE" -noout -pubkey 2>/dev/null | openssl sha256 2>/dev/null || true)"
    _key_pub="$(openssl pkey -in "$KEY_FILE" -pubout 2>/dev/null | openssl sha256 2>/dev/null || true)"
    if [[ -n "$_cert_pub" && -n "$_key_pub" && "$_cert_pub" != "$_key_pub" ]]; then
        die "证书和私钥对不上 —— 确认一下是不是同一套"
    fi

    _subject="$(openssl x509 -noout -subject -in "$CERT_FILE" 2>/dev/null | sed 's/^subject=//' || true)"
    log "已有证书检查通过：${_subject:-$CERT_FILE}"
fi

SCHEME="https"; [[ "$CERT_MODE" == "none" ]] && SCHEME="http"
# 脚本自己在哪。
# ⚠️ 用 curl | bash 跑时没有"来源文件"，BASH_SOURCE 是空的 ——
#    直接取下标会被 set -u 判成 unbound variable，一行都执行不到。
#    这种情况就把当前目录当脚本目录，反正下面的 IN_CHECKOUT 会
#    认出"这不是检出目录"，改去仓库拉代码。
_SELF="${BASH_SOURCE[0]:-}"
if [[ -n "$_SELF" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$_SELF")" 2>/dev/null && pwd)" || SCRIPT_DIR="$(pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)" || REPO_ROOT="/nonexistent"

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

# 确认页只说"密码设过没有"，**绝不回显密码本身**。
# （一开始用 ${PW:+A}${PW:-B} 拼，结果变量非空时 :- 回退到变量自己的值，
#   把密码原样打到屏幕上了 —— 好在渲染一遍就看出来了。）
if [[ -n "$ADMIN_PASS" ]]; then
    ADMIN_DESC="$ADMIN_USER     ← 密码你刚设的（不回显）"
else
    ADMIN_DESC="$ADMIN_USER     ← 密码将随机生成并打印出来"
fi

cat <<EOF

  轻舟 / Canoe Server 安装
  ────────────────────────────────
  域名         ${DOMAIN:-<无，用 IP 访问>}
  客户端口     $PORT          ← 更新 / 订阅 / API，对所有人开放（固定，客户端写死的）
  管理面板     $PANEL_DESC
  管理员       $ADMIN_DESC
  证书         $CERT_DESC

EOF
if [[ -t 0 ]]; then read -rp "确认开始？[Y/n] " ok; [[ "${ok:-y}" =~ ^[Yy]?$ ]] || exit 0; fi

# ---------------------------------------------------------------------------
# 1. 系统依赖
# ---------------------------------------------------------------------------
log "安装系统依赖…"
export DEBIAN_FRONTEND=noninteractive
# git：--repo 拉代码要用；用户自己 clone 的话也总得有
PKGS=(python3 python3-venv python3-pip openssl rsync curl git ca-certificates)
[[ "$CERT_MODE" == "le" ]] && PKGS+=(certbot)

# 输出先收进日志，只有**失败**时才打出来。
# 直接放出去的话，装个 rsync 也会刷一屏 "Selecting previously
# unselected package..." —— 用户要的是看得清的步骤，不是 dpkg 的流水账；
# 但真出错了又不能把原因藏起来，所以是"平时安静、出事全说"。
_APT_LOG="$(mktemp)"
_apt_fail() {
    printf '\n'
    die "系统依赖安装失败。下面是原始输出：

$(cat "$_APT_LOG")
"
}
apt-get update -qq >"$_APT_LOG" 2>&1 || _apt_fail
apt-get install -y -qq "${PKGS[@]}" >>"$_APT_LOG" 2>&1 || _apt_fail
rm -f "$_APT_LOG"
ok "系统依赖就绪"

# ---------------------------------------------------------------------------
# 2. 用户与代码
# ---------------------------------------------------------------------------
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    log "创建用户 $APP_USER"
    useradd -r -s /usr/sbin/nologin -d "$APP_DIR" "$APP_USER"
    NEW_USER=1                    # 只有确实是这次新建的，回滚才敢删
fi
[[ -d "$APP_DIR" ]] || NEW_APP_DIR=1
mkdir -p "$APP_DIR"; chown "$APP_USER:$APP_USER" "$APP_DIR"

# 是不是在检出目录里跑？（脚本自己在 canoe-server/deploy/ 下）
IN_CHECKOUT=0
[[ -f "$REPO_ROOT/canoe-server/canoe_server/__init__.py" ]] && IN_CHECKOUT=1

# 不在检出目录里、又没指名仓库 —— 那就自己去项目上拉。
# 这就是「脚本自己在服务器上把代码拉下来」那条路：
# 你只需要把 install.sh 弄到服务器上，别的它自己搞定。
if [[ -z "$REPO_URL" && "$IN_CHECKOUT" == "0" ]]; then
    REPO_URL="$DEFAULT_REPO"
    log "不在检出目录里，改从项目拉代码"
fi

if [[ -n "$REPO_URL" ]]; then
    CLONE_URL="$REPO_URL"

    # root 去操作一个属于 canoe 用户的仓库时，新版 git 会以
    # "detected dubious ownership" 直接拒绝。这里显式声明一次，
    # 之后 install 和 `canoe upgrade`（也是 root）都能正常拉。
    # ⚠ 幂等：先按值**锚定**删掉同名条目，再加。原来只有 `--add`，
    #   每重跑一次 install 就往 /root/.gitconfig 里多堆一行，越跑越长。
    #   值用正则转义 + ^…$ 锚定，免得把 /opt/canoe-old 之类一起误删。
    _esc="$(printf '%s' "$APP_DIR" | sed 's/[][\\.*^$]/\\&/g')"
    git config --global --unset-all safe.directory "^${_esc}$" 2>/dev/null || true
    git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
    unset _esc

    # 私有仓库 + 令牌：只在克隆这一下把令牌塞进 URL，用完立刻从
    # remote 里擦掉。留着的话它会明文躺在 /opt/canoe/.git/config 里 ——
    # 那是任何能读该目录的人都拿得到的东西。
    if [[ -n "$REPO_TOKEN" ]]; then
        case "$REPO_URL" in
            https://*) CLONE_URL="https://${REPO_TOKEN}@${REPO_URL#https://}" ;;
            *) die "--token 只能配 https:// 的仓库地址（现在给的是 $REPO_URL）。
     要用 SSH（Deploy Key）就别传 --token。" ;;
        esac
    fi

    if [[ -d "$APP_DIR/.git" ]]; then
        log "拉取最新代码（$REPO_URL）"
        git -C "$APP_DIR" remote set-url origin "$CLONE_URL"
        if ! git -C "$APP_DIR" pull --ff-only; then
            git -C "$APP_DIR" remote set-url origin "$REPO_URL"
            die "拉取失败。私有仓库拉不动通常是凭据问题：
      · Deploy Key：确认 ~/.ssh/ 里的私钥在，且公钥已加到仓库
        （Settings -> Deploy keys，只读即可）
      · PAT：bash install.sh --token <新的PAT>
      · 本地有改动？git -C $APP_DIR status 看看"
        fi
        git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    else
        log "克隆仓库：$REPO_URL"
        # 用 root 克隆而不是 sudo -u canoe：Deploy Key 和 git 凭据都在
        # root 的 HOME 下，canoe 用户根本看不到它们。克隆完再 chown 过去。
        if ! git clone "$CLONE_URL" "$APP_DIR"; then
            die "克隆失败。
     私有仓库要先把凭据准备好，二选一：
       Deploy Key（推荐）：
         ssh-keygen -t ed25519 -f ~/.ssh/canoe -N \"\"
         # 把 ~/.ssh/canoe.pub 加到仓库 Settings -> Deploy keys
         bash install.sh --repo git@github.com:NekoBoxHQ/canoe.git
       PAT（临时）：
         bash install.sh --token <你的PAT>
     仓库是公开的话这条不该失败，检查一下域名拼写。"
        fi
        [[ -n "$REPO_TOKEN" ]] && git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    fi
elif [[ "$REPO_ROOT" == "$APP_DIR" ]]; then
    # 已经装过、这次是从 /opt/canoe 里那份 install.sh 跑起来的 ——
    # 源和目标撞在一起。rsync 自己到自己虽然是个空操作，但打出来
    # "从 /opt/canoe 同步代码到 /opt/canoe" 纯属让人犯嘀咕。
    log "代码已在 $APP_DIR，跳过同步"
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
[[ -d "$SERVER_DIR/.venv" ]] || as_user "python3 -m venv '$SERVER_DIR/.venv'"
PIP="$SERVER_DIR/.venv/bin/pip"
# setuptools/wheel 要显式装：Debian 的 python3-venv 不保证带 setuptools，
# 而 canoe-core 是 pyproject + setuptools 后端的可编辑安装，缺了会失败。
as_user "$PIP install -q -U pip setuptools wheel"
as_user "$PIP install -q -e '$APP_DIR/canoe-core'"
as_user "$PIP install -q -r '$SERVER_DIR/requirements.txt'"

# ---------------------------------------------------------------------------
# 4. 证书
# ---------------------------------------------------------------------------
TLS_CERT=""
TLS_KEY=""
[[ -d "$CERT_DIR" ]] || NEW_CERT_DIR=1
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
    [[ -f /etc/letsencrypt/renewal-hooks/deploy/canoe.sh ]] || NEW_HOOK=1
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
        # 顺手把本机内网 IP 也塞进 SAN，方便用 IP 直连。
        # `|| true`：hostname -I 在个别环境里不可用，而脚本是 pipefail 的，
        # 少个网卡就让整个自签流程静默中止，太不值当。
        LANIP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
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
    warn "已有证书不会自动续期：到期前换掉 $LIVE_DIR/ 里的文件，再 canoe restart"
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
    mapfile -t SECRETS < <(as_user "'$SERVER_DIR/.venv/bin/python' -c '$PY_CMD'")
    TICKET_SECRET="${SECRETS[0]}"

    # 管理员密码：用户在向导里设过就用他的；没设（一路回车）才生成，
    # 而且会明明白白打印出来 —— 不会再有"密码藏在哪个文件里"这种事。
    if [[ -z "$ADMIN_PASS" ]]; then
        ADMIN_PASS="${SECRETS[1]}"
        ADMIN_GENERATED=1
    fi
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

ADMIN_USERNAME=$ADMIN_USER
ADMIN_PASSWORD=$ADMIN_PASS

# 安装包下载地址靠它拼（客户端「更新」按钮指向这里）
PUBLIC_BASE_URL=$BASE
MAX_RELEASE_MB=300

# 推送
SSE_KEEPALIVE=20
SSE_MAX_CONNECTIONS=2000
SSE_MAX_PER_USER=5

# 说明：本服务端只分发订阅，不转发流量 —— 节点服务器是另一台机器，
# 跟这里无关。所以 .env 里没有 relay * 那类设置。
EOF
    chown "$APP_USER:$APP_USER" "$ENV_FILE"; chmod 600 "$ENV_FILE"

    # 只有"密码是随机生成的"才留文件 —— 用户自己设的密码他自己知道，
    # 再往磁盘上写一份明文纯属多此一举。
    if [[ "$ADMIN_GENERATED" == "1" ]]; then
        cat > "$APP_DIR/ADMIN_PASSWORD.txt" <<EOF
管理员密码是随机生成的（向导里那一步直接回车了）

  面板地址: $BASE/panel
  用户名:   $ADMIN_USER
  密码:     $ADMIN_PASS

登录后请改掉并删掉这个文件。
以后想改：canoe config -> 改管理员账号
EOF
        chmod 600 "$APP_DIR/ADMIN_PASSWORD.txt"
    fi
fi

mkdir -p "$SERVER_DIR/data" "$SERVER_DIR/releases"
chown -R "$APP_USER:$APP_USER" "$SERVER_DIR/data" "$SERVER_DIR/releases"

# ---------------------------------------------------------------------------
# 6. 建库 + 种子
# ---------------------------------------------------------------------------
if [[ "$DO_SEED" == "1" ]]; then
    log "初始化数据库…"
    as_user "cd '$SERVER_DIR' && .venv/bin/python seed.py"
fi

# ---------------------------------------------------------------------------
# 7. systemd
# ---------------------------------------------------------------------------
log "安装 systemd 服务…"
[[ -f /etc/systemd/system/canoe-api.service ]] || NEW_UNIT=1
install -m 644 "$SERVER_DIR/deploy/canoe-api.service" /etc/systemd/system/canoe-api.service
systemctl daemon-reload
systemctl enable canoe-api >/dev/null
systemctl restart canoe-api
sleep 3
systemctl is-active --quiet canoe-api \
    && log "canoe-api 已在跑" \
    || { journalctl -u canoe-api -n 40 --no-pager; die "canoe-api 起不来，日志见上"; }

# 顺带装管理脚本：以后启停 / 看状态 / 改配置 / 升级都用 `canoe`
if [[ -f "$SERVER_DIR/deploy/canoe.sh" ]]; then
    [[ -f /usr/local/bin/canoe ]] || NEW_SELF=1
    install -m 755 "$SERVER_DIR/deploy/canoe.sh" /usr/local/bin/canoe
    log "管理脚本已安装：canoe"
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

printf '\n'
title "装好了"
# ⚠ 标签一律 4 个汉字（8 显示列）—— field 的标签列宽是 10，超过会被挤成
#   1 个空格，整块就歪了。
field "管理面板" "$PANEL_URL"
field "订阅接口" "$BASE_SHOWN/api/subscription"
field "更新接口" "$BASE_SHOWN/api/client/latest"
field "健康检查" "$BASE_SHOWN/api/health"
field "管理脚本" "canoe    （启动 / 停止 / 状态 / 配置 / 升级 / 卸载）"
field "配置文件" "$SERVER_DIR/.env"
field "登录面板" "$ADMIN_USER    （密码是刚才设的那个；忘了：canoe config）"
printf '\n'
printf '  %s下一步%s\n' "$C_INFO" "$C_OFF"
dim "1. 面板「用户」→ 给账号配订阅：节点链接一行一个"
dim "   （ss:// vmess:// vless:// trojan://）。清零 = 停止分发，客户端会就地销毁本地订阅"
dim "2. 面板「发布」→「拉取最新轻舟」：服务端自己去 GitHub Release 把客户端版本拉回来发布"
dim "   开发机那边先跑 scripts/package_release.py，把 zip 和 .sha256 一起挂到 Release 上"
dim "   （客户端地址写死在 $BASE_SHOWN，不用在客户端配任何东西）"
hr

if [[ "$ADMIN_GENERATED" == "1" ]]; then
printf '\n'
warn "管理员密码是随机生成的（向导里那步直接回车了）："
field "用户名" "$ADMIN_USER"
field "密码"   "$ADMIN_PASS"
dim "也写了一份在 $APP_DIR/ADMIN_PASSWORD.txt。登录后请改掉、并删掉那个文件。"
fi

if [[ -n "$PANEL_PORT" && "$PANEL_PORT" != "$PORT" ]]; then
    printf '\n'
    warn "面板另开了 $PANEL_PORT 口，记得在防火墙/安全组里："
    dim "$PORT       对所有用户开放（客户端要用）"
    dim "$PANEL_PORT   只放行你自己的 IP"
    dim "本机 ufw 已自动放行；云厂商的安全组要你自己加。"
else
    printf '\n'
    warn "面板和客户端共用一个口，所以 $PANEL_PATH 是公网可访问的。"
    dim "想只对自己开放，重跑本脚本时把面板端口填成别的（例如 58589）。"
fi

printf '\n'
warn "服务端只开 1 个 worker（推送是进程内的，多 worker 会收不到）。"
dim "详见 deploy/README.md 开头。"
