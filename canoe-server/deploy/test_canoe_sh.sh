#!/usr/bin/env bash
# ===========================================================================
#  管理脚本（canoe.sh）的测试
#
#  只测**不依赖 systemd / root / Linux** 的那部分：.env 读写、URL 拼装、
#  菜单与帮助的输出、错误命令的退出码。
#
#  启停服务、卸载这些必须动真格的，这里不碰 —— 在生产机器上跑测试时
#  误停一次服务是不可接受的。
#
#  用法：  bash deploy/test_canoe_sh.sh
# ===========================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HERE/canoe.sh"

passed=0
failed=0

check() {
    local label="$1" cond="$2" extra="${3:-}"
    if [[ "$cond" == "0" ]]; then
        passed=$((passed + 1))
        printf '  [ok]   %s\n' "$label"
    else
        failed=$((failed + 1))
        printf '  [FAIL] %s  %s\n' "$label" "$extra"
    fi
}

# 把脚本当库加载（source 时它不会跑 main —— 见脚本末尾的判据说明）
# shellcheck source=canoe.sh
source "$TARGET"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
printf '\n== 轻舟 · 管理脚本测试 ==\n\n'

printf '[1] .env 读写\n'
ENV_FILE="$TMP/.env"
cat > "$ENV_FILE" <<'EOF'
# 注释行不该被动
PORT=58588
PANEL_PORT=58589
DEBUG=false
EOF

check "env_get 取得到值" "$([[ "$(env_get PORT)" == "58588" ]] && echo 0 || echo 1)" \
      "实际 $(env_get PORT)"
check "env_get 取不存在的键给空串" "$([[ -z "$(env_get NOPE)" ]] && echo 0 || echo 1)"
check "env_get 不误吞注释行" "$([[ "$(env_get 注释行)" == "" ]] && echo 0 || echo 1)"

env_set PORT 60000
check "env_set 替换了原值" "$([[ "$(env_get PORT)" == "60000" ]] && echo 0 || echo 1)" \
      "实际 $(env_get PORT)"
check "env_set 没有把键写重复" \
      "$([[ "$(grep -c '^PORT=' "$ENV_FILE")" == "1" ]] && echo 0 || echo 1)" \
      "出现 $(grep -c '^PORT=' "$ENV_FILE") 次"
check "env_set 没动别的键" "$([[ "$(env_get DEBUG)" == "false" ]] && echo 0 || echo 1)"

env_set NEW_KEY "http://a/b?c=1&d=2"
check "env_set 能追加新键" "$([[ "$(env_get NEW_KEY)" == "http://a/b?c=1&d=2" ]] && echo 0 || echo 1)" \
      "实际 $(env_get NEW_KEY)"

# 值里带 / & = 是真实场景（PUBLIC_BASE_URL 就长这样），不能让替换逻辑崩掉
env_set PUBLIC_BASE_URL "https://canoe.s-ui.com:58588"
check "值里带 / 和 : 也写得对" \
      "$([[ "$(env_get PUBLIC_BASE_URL)" == "https://canoe.s-ui.com:58588" ]] && echo 0 || echo 1)" \
      "实际 $(env_get PUBLIC_BASE_URL)"
env_set NEW_KEY "a&b/c=d"
check "值里带 & 和 = 也写得对" "$([[ "$(env_get NEW_KEY)" == "a&b/c=d" ]] && echo 0 || echo 1)" \
      "实际 $(env_get NEW_KEY)"

printf '\n[2] 协议与地址\n'
env_set TLS_CERT "/etc/canoe/live/fullchain.pem"
check "有证书 -> https" "$([[ "$(scheme)" == "https" ]] && echo 0 || echo 1)" "$(scheme)"

env_set TLS_CERT ""
check "没证书 -> http" "$([[ "$(scheme)" == "http" ]] && echo 0 || echo 1)" "$(scheme)"

env_set PUBLIC_BASE_URL "https://canoe.s-ui.com:58588"
check "从 PUBLIC_BASE_URL 抠出域名" \
      "$([[ "$(domain_display)" == "canoe.s-ui.com" ]] && echo 0 || echo 1)" "$(domain_display)"

env_set PUBLIC_BASE_URL ""
check "没配时显示占位" "$([[ "$(domain_display)" == "<本机IP>" ]] && echo 0 || echo 1)" "$(domain_display)"

env_set PORT 58588
env_set PANEL_PORT 58588
check "同口时面板地址不带第二段端口" \
      "$([[ "$(panel_url)" == "http://<本机IP>:58588/panel" ]] && echo 0 || echo 1)" "$(panel_url)"

env_set PANEL_PORT 58589
check "另开口时面板地址用面板口" \
      "$([[ "$(panel_url)" == "http://<本机IP>:58589/panel" ]] && echo 0 || echo 1)" "$(panel_url)"

env_set PANEL_PORT ""
check "面板口留空 = 同口" \
      "$([[ "$(panel_url)" == "http://<本机IP>:58588/panel" ]] && echo 0 || echo 1)" "$(panel_url)"

printf '\n[3] 安装判断\n'
SERVER_DIR="$TMP/nope"
check "目录不存在 -> 未安装" "$(installed && echo 1 || echo 0)"

printf '\n[4] 帮助与菜单\n'
HELP="$(bash "$TARGET" help 2>&1)"
for cmd in install start stop restart status config upgrade uninstall logs passwd; do
    check "帮助里有 $cmd" "$(grep -qE "^[[:space:]]*$cmd" <<< "$HELP" && echo 0 || echo 1)"
done

# 菜单：喂 0 让它渲染一遍就退出。
#
# 未安装时菜单只会列出「安装」—— 这是刻意的（其余几项点了也没用）。
# 所以要验完整的 1-8，得先造一个"看起来装过了"的环境：
# CANOE_APP_DIR 指过去，并在里面放一个可执行的 .venv/bin/python。
FAKE_APP="$TMP/fakeapp"
mkdir -p "$FAKE_APP/canoe-server/.venv/bin"
printf '#!/bin/sh\nexit 0\n' > "$FAKE_APP/canoe-server/.venv/bin/python"
chmod +x "$FAKE_APP/canoe-server/.venv/bin/python"

MENU="$(printf '0\n' | CANOE_APP_DIR="$FAKE_APP" bash "$TARGET" 2>&1)"
for item in "安装 Canoe" "启动 Canoe" "停止 Canoe" "重启 Canoe" \
            "Canoe 状态" "Canoe 配置" "升级 Canoe" "卸载 Canoe"; do
    check "菜单里有「$item」" "$(grep -qF "$item" <<< "$MENU" && echo 0 || echo 1)" "$MENU"
done
check "菜单编号 0-8 齐全" \
      "$(for i in 1 2 3 4 5 6 7 8 0; do grep -qE "^[[:space:]]*$i[[:space:]]" <<< "$MENU" || exit 1; done; echo 0)"

# 菜单格式是用户点名要的 —— 标题、上下两条横线、只有 1-8 + 0。
# 之前我往里塞了状态行、版本号、l/p 快捷键、未安装提示，被要求改回来。
check "标题是「服务端管理脚本」" \
      "$(grep -q "服务端管理脚本" <<< "$MENU" && echo 0 || echo 1)" "$MENU"
check "★ 菜单里不掺状态/版本/快捷键这些杂项" \
      "$(grep -qE "运行中|已停止|版本|l  看日志|p  改管理员密" <<< "$MENU" && echo 1 || echo 0)" "$MENU"
# 那两个隐藏快捷键（l / p）用户点名说多余，已经从菜单循环里删掉了
check "★ 顶层菜单不再吃 l/p 这种隐藏快捷键" \
      "$(grep -qE '^[[:space:]]*[lpLP]?\|?[lLpP]\) cmd_' "$TARGET" && echo 1 || echo 0)"
check "菜单只有 9 行选项（1-8 加 0）" \
      "$([[ "$(grep -cE '^[[:space:]]+[0-9]  ' <<< "$MENU")" == "9" ]] && echo 0 || echo 1)" \
      "实际 $(grep -cE '^[[:space:]]+[0-9]  ' <<< "$MENU") 行"

# 配置子菜单。cmd_config 要 root / 要已安装，两个检查 stub 掉就能验。
CONFIG_MENU="$(printf '0\n' | bash -c "
    source '$TARGET'
    need_root() { :; }
    require_installed() { :; }
    cmd_config
" 2>&1)"
check "配置子菜单里有「改管理员账号」" \
      "$(grep -q "改管理员账号" <<< "$CONFIG_MENU" && echo 0 || echo 1)" "$CONFIG_MENU"
check "配置子菜单里有「查看当前配置」" \
      "$(grep -q "查看当前配置" <<< "$CONFIG_MENU" && echo 0 || echo 1)"
check "★ 配置子菜单里不再有「改客户端口」（客户端写死，不该给这个选项）" \
      "$(grep -q "改客户端口" <<< "$CONFIG_MENU" && echo 1 || echo 0)" "$CONFIG_MENU"

# 管理员账号 = 用户名 + 密码，两样都要能改（用户点名要求用户名可改）。
check "★ 改管理员账号会问新用户名" \
      "$(grep -q '新用户名' "$TARGET" && echo 0 || echo 1)"
check "★ 用户名有格式校验（3-32 位字母数字下划线）" \
      "$(grep -qE 'A-Za-z0-9_-\]\{3,32\}' "$TARGET" && echo 0 || echo 1)"
check "★ 改名会查重（不让人改成已存在的名字）" \
      "$(grep -q '已经被占用' "$TARGET" && echo 0 || echo 1)"
check "★ 改完把 .env 里的 ADMIN_USERNAME 也对齐" \
      "$(grep -q 'env_set ADMIN_USERNAME' "$TARGET" && echo 0 || echo 1)"
check "两样都留空时不误改成空账号" \
      "$(grep -q '都没改，返回' "$TARGET" && echo 0 || echo 1)"

# 「看日志」归到状态里，不再单列 —— 用户明确说 l/p 那两个是多余的。
check "★ 状态里带最近日志（所以不需要单独的「看日志」项）" \
      "$(grep -q '最近日志' "$TARGET" && echo 0 || echo 1)"

# 三种调用方式都必须能出菜单。曾经只认 BASH_SOURCE 惯用法，
# 结果 bash -c "$(curl …)"（推荐的一行安装方式）下菜单一个字都不显示。
for how in "直接" "管道"; do
    if [[ "$how" == "直接" ]]; then
        OUT="$(printf '0\n' | CANOE_APP_DIR="$FAKE_APP" bash "$TARGET" 2>&1)"
    else
        OUT="$(printf '0\n' | CANOE_APP_DIR="$FAKE_APP" bash -c "$(cat "$TARGET")" 2>&1)"
    fi
    check "★ $how 执行时菜单出得来" \
          "$(grep -qF "安装 Canoe" <<< "$OUT" && echo 0 || echo 1)" "$OUT"
done

printf '\n[5] 命令分发\n'
bash "$TARGET" 乱写 >/dev/null 2>&1
check "不认识的命令返回非 0" "$([[ $? -ne 0 ]] && echo 0 || echo 1)"

out="$(bash "$TARGET" version 2>&1)"
check "version 能跑" "$(grep -q "管理脚本" <<< "$out" && echo 0 || echo 1)" "$out"

printf '\n[6] 安全默认值\n'
# 改客户端口必须警告（客户端把地址写死了）—— 这条是从"别把人自己搞断网"来的
check "改端口函数存在" "$(declare -F change_port >/dev/null && echo 0 || echo 1)"
check "卸载函数存在" "$(declare -F cmd_uninstall >/dev/null && echo 0 || echo 1)"
# 卸载必须问两遍；这里只做静态检查，不真跑
check "卸载要先确认数据是否保留" \
      "$(grep -q "删除数据？" "$TARGET" && echo 0 || echo 1)"
check "卸载要二次确认" "$(grep -q "最后确认一次" "$TARGET" && echo 0 || echo 1)"
check "改客户端口会警告写死的事" \
      "$(grep -q "写死在 58588" "$TARGET" && echo 0 || echo 1)"

# ---------------------------------------------------------------------------
# 提权助手：Debian 最小安装不带 sudo。写死 `sudo -u canoe` 会在
# 刚装好的机器上以 "sudo: command not found" 把安装打断（真踩过）。
# ---------------------------------------------------------------------------
printf '\n[9] 切用户不依赖 sudo\n'
for f in "$TARGET" "$HERE/install.sh"; do
    name="$(basename "$f")"
    # 只允许 as_user 内部那一处真正调用 sudo（它自己会先判断 sudo 在不在）。
    # 注释里提到 sudo -u 的不算 —— 那是在解释为什么要这么做。
    naked="$(grep -n 'sudo -u' "$f" \
             | grep -v '^[0-9]*:[[:space:]]*#' \
             | grep -v 'command -v sudo' | wc -l | tr -d ' ')"
    check "$name 里没有裸的 sudo -u" "$([[ "$naked" -le 1 ]] && echo 0 || echo 1)" "找到 $naked 处"
    check "$name 有 as_user 助手" "$(grep -q '^as_user()' "$f" && echo 0 || echo 1)"
    check "$name 的 as_user 会退到 runuser" \
          "$(grep -q 'runuser -u' "$f" && echo 0 || echo 1)"
done

# 真的在"没有 sudo"的环境里跑一遍 as_user —— 只做静态检查会漏掉
# 分支写错、变量名写错这类问题。假的 runuser 要真的把命令执行了，
# 不然只能验到"没报错"，验不到"确实跑起来了"。
FAKEBIN="$TMP/fakebin"; mkdir -p "$FAKEBIN"
cat > "$FAKEBIN/runuser" <<'FAKE'
#!/bin/sh
# 假 runuser：吃掉 -u USER [--]，剩下的当成要执行的命令
shift 2
[ "${1:-}" = "--" ] && shift
exec "$@"
FAKE
chmod +x "$FAKEBIN/runuser"

AS_USER_BODY="$(sed -n '/^as_user()/,/^}/p' "$TARGET")"
check "抠得出 as_user 函数体" "$([[ -n "$AS_USER_BODY" ]] && echo 0 || echo 1)"

# PATH 前置而不是替换 —— 替换会把 bash 自己弄丢，报 "bash: command not found"
OUT="$(PATH="$FAKEBIN:$PATH" bash -c "
    APP_USER=canoe
    $AS_USER_BODY
    as_user 'echo 切过去了'
" 2>&1)"
check "★ 没有 sudo 时 as_user 仍能工作（走 runuser）" \
      "$([[ "$OUT" == "切过去了" ]] && echo 0 || echo 1)" "$OUT"

printf '\n[7] 语法\n'
check "bash -n 通过" "$(bash -n "$TARGET" 2>/dev/null && echo 0 || echo 1)"

# ---------------------------------------------------------------------------
# install.sh 的「自己去项目拉代码」这条路。
#
# ⚠ 这里只做静态检查 + 跑 --help：install.sh 一执行就要 apt-get / useradd /
#   systemd / certbot，在开发机上跑会真的改系统。真正的验收得在 Debian 上做。
# ---------------------------------------------------------------------------
printf '\n[8] install.sh 取代码\n'
INSTALL="$HERE/install.sh"

check "install.sh 存在" "$([[ -f "$INSTALL" ]] && echo 0 || echo 1)"
check "install.sh 语法通过" "$(bash -n "$INSTALL" 2>/dev/null && echo 0 || echo 1)"

check "内置了默认仓库地址" "$(grep -q '^DEFAULT_REPO=' "$INSTALL" && echo 0 || echo 1)"
check "默认地址指向本项目" \
      "$(grep -q 'DEFAULT_REPO=.*NekoBoxHQ/canoe' "$INSTALL" && echo 0 || echo 1)"

# 不在检出目录里跑 -> 应该自动改用 DEFAULT_REPO 去拉
check "不在检出目录时自动回落到项目地址" \
      "$(grep -q 'IN_CHECKOUT' "$INSTALL" && grep -q 'REPO_URL="\$DEFAULT_REPO"' "$INSTALL" && echo 0 || echo 1)"
check "能识别出「在不在检出目录里」" \
      "$(grep -q 'canoe-server/canoe_server/__init__.py' "$INSTALL" && echo 0 || echo 1)"

# 令牌处理：这是最容易写错、也最容易被忽略的一处
check "支持 --token" "$(grep -qE '^\s*--token\)' "$INSTALL" && echo 0 || echo 1)"
check "也认 CANOE_TOKEN 环境变量" \
      "$(grep -q 'CANOE_TOKEN' "$INSTALL" && echo 0 || echo 1)"
check "★ 令牌用完从 remote 里擦掉（不留明文）" \
      "$(grep -q 'remote set-url origin "\$REPO_URL"' "$INSTALL" && echo 0 || echo 1)"
check "★ 日志里只打印不带令牌的地址" \
      "$(grep -q '克隆仓库：\$REPO_URL' "$INSTALL" && echo 0 || echo 1)"
check "--token 配非 https 地址会明确报错" \
      "$(grep -q '只能配 https://' "$INSTALL" && echo 0 || echo 1)"

# root 操作 canoe 用户的仓库会被 git 拒（dubious ownership）
check "★ 声明了 safe.directory（否则 pull 会被 git 拒）" \
      "$(grep -q 'safe.directory' "$INSTALL" && echo 0 || echo 1)"

# curl | bash 这条路：管道执行时没有"来源文件"，BASH_SOURCE 是空的。
# 曾经直接取 ${BASH_SOURCE[0]}，被 set -u 判成 unbound variable ——
# 表现是一行输出都没有就退出，用户完全看不出为什么。
# 断言：把定位脚本目录那段单独喂给 bash 的标准输入，不许报 unbound。
PIPED="$(sed -n '/^_SELF=/,/^REPO_ROOT=/p' "$INSTALL" | bash 2>&1)"
check "★ 管道执行（curl | bash）时不会炸" \
      "$(grep -q "unbound variable" <<< "$PIPED" && echo 1 || echo 0)" "$PIPED"

# 证书校验：老版本用 `openssl rsa -noout -modulus` 比对，遇到 EC / Ed25519
# 密钥会失败；脚本是 set -euo pipefail，赋值语句一失败就**静默退出** ——
# 用户看到的是"向导问完了，什么都没发生"。真踩过一次（EC 证书）。
# 这里把那段单独抠出来跑，断言它不会把脚本干掉。
CERTBLOCK="$(sed -n '/^# ---- 已有证书：先验一遍/,/^fi$/p' "$INSTALL")"
check "抠得出证书校验那段" "$([[ -n "$CERTBLOCK" ]] && echo 0 || echo 1)"
# 找的是那段真代码（openssl rsa -noout -modulus），不是注释里提到它的地方
check "★ 不再用只认 RSA 的 openssl rsa -modulus" \
      "$(grep -qE 'openssl +rsa .*-modulus' "$INSTALL" && echo 1 || echo 0)"
check "★ 每一步都有兜底（不会触发 set -e 静默退出）" \
      "$(grep -c '|| true' <<< "$CERTBLOCK" | awk '{print ($1>=2)?0:1}')"

if command -v openssl >/dev/null 2>&1; then
    CERTDIR="$TMP/certs"; mkdir -p "$CERTDIR"

    # EC 密钥 + 自签证书（新版 certbot --key-type ecdsa 就是这种）
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -nodes -days 2 -keyout "$CERTDIR/ec.key" -out "$CERTDIR/ec.crt" \
        -subj "//CN=canoe.test" >/dev/null 2>&1

    # 另一套，用来验"对不上"要被抓住
    openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
        -keyout "$CERTDIR/other.key" -out "$CERTDIR/other.crt" \
        -subj "//CN=other.test" >/dev/null 2>&1

    run_certblock() {   # $1=cert $2=key
        { printf 'set -euo pipefail\n'
          printf 'log(){ :; }; warn(){ :; }; die(){ printf "DIED: %%s\\n" "$*"; exit 1; }\n'
          printf 'CERT_MODE=existing\nCERT_FILE=%q\nKEY_FILE=%q\n' "$1" "$2"
          printf '%s\n' "$CERTBLOCK"
        } | bash 2>&1
    }

    out="$(run_certblock "$CERTDIR/ec.crt" "$CERTDIR/ec.key")"
    check "★ EC 证书能通过校验（不再静默退出）" \
          "$([[ -z "$out" ]] && echo 0 || echo 1)" "$out"

    out="$(run_certblock "$CERTDIR/other.crt" "$CERTDIR/other.key")"
    check "RSA 证书也照常通过" "$([[ -z "$out" ]] && echo 0 || echo 1)" "$out"

    out="$(run_certblock "$CERTDIR/ec.crt" "$CERTDIR/other.key")"
    check "★ 证书与私钥错配会被抓住并说明白" \
          "$(grep -q '对不上' <<< "$out" && echo 0 || echo 1)" "$out"
else
    echo "  [跳过] 证书校验实测 —— 没有 openssl"
fi

# 管理员账号：装的时候就得问，不能自己随机一个塞进文件里让用户去找
# （用户原话："我找到现在都不知道初始密码在什么地方"）。
printf '\n[10] 管理员账号在安装时就要问\n'
check "★ 向导里有管理员用户名的提问" \
      "$(grep -q '管理员用户名' "$INSTALL" && echo 0 || echo 1)"
check "★ 向导里有管理员密码的提问" \
      "$(grep -q '管理员密码' "$INSTALL" && echo 0 || echo 1)"
check "★ 密码是不回显读入的（read -s）" \
      "$(grep -q 'read -rsp' "$INSTALL" && echo 0 || echo 1)"
check "密码要输两遍（有二次确认）" \
      "$(grep -q '再输一遍' "$INSTALL" && echo 0 || echo 1)"
check "密码有长度下限校验" \
      "$(grep -q '至少 8 位' "$INSTALL" && echo 0 || echo 1)"

# 密码**绝不能**在确认页/结尾提示里回显。
# 这里踩过：用 ${PW:+A}${PW:-B} 拼描述，变量非空时 :- 会回退到变量
# 自己的值，于是密码被原样打到屏幕上。
CONFIRM_BLOCK="$(sed -n '/确认页只说/,/^EOF$/p' "$INSTALL")"
check "抠得出确认页那段" "$([[ -n "$CONFIRM_BLOCK" ]] && echo 0 || echo 1)"
# 要防的是"把密码打进正文"。`if [[ -n "$ADMIN_PASS" ]]` 那种判空是
# 正当用法，所以只看 heredoc 的**正文**部分。
CONFIRM_BODY="$(sed -n '/^cat <<EOF$/,/^EOF$/p' <<< "$CONFIRM_BLOCK")"
check "抠得出确认页 heredoc 正文" "$([[ -n "$CONFIRM_BODY" ]] && echo 0 || echo 1)"
check "★ 不用 \$ADMIN_PASS 拼正文（那会把密码打到屏幕上）" \
      "$(grep -q '\$ADMIN_PASS' <<< "$CONFIRM_BODY" && echo 1 || echo 0)" "$CONFIRM_BODY"
check "确认页正文用的是不含密码的 \$ADMIN_DESC" \
      "$(grep -q '\$ADMIN_DESC' <<< "$CONFIRM_BODY" && echo 0 || echo 1)"

# 明文密码只在"随机生成"时才落盘。用户自己设的密码他自己知道，
# 再写一份到磁盘上纯属多此一举，还多一处泄漏面。
check "★ 写 ADMIN_PASSWORD.txt 有 ADMIN_GENERATED 守卫" \
      "$(grep -B 1 'ADMIN_PASSWORD.txt' "$INSTALL" | grep -q 'ADMIN_GENERATED' && echo 0 || echo 1)"

HELP_I="$(bash "$INSTALL" --help 2>&1)"
check "--help 能跑" "$(grep -q "安装向导" <<< "$HELP_I" && echo 0 || echo 1)"
check "帮助里说明了代码从哪来" "$(grep -q "代码从哪来" <<< "$HELP_I" && echo 0 || echo 1)"
check "帮助里有 Deploy Key 的走法" "$(grep -q "Deploy keys" <<< "$HELP_I" && echo 0 || echo 1)"
check "帮助里有 --token" "$(grep -q -- "--token" <<< "$HELP_I" && echo 0 || echo 1)"

# ---------------------------------------------------------------------------
printf '\n[11] 已经装过了就别再问一遍向导\n'
# 用户的原话：「我都部署好了服务端 为什么还是要按照你的脚本来」——
# 服务端明明跑着，重新执行一遍安装命令，迎面又是「[1/3] 域名」，
# 看着像要把装好的东西推倒重来。其实他多半只是想打开管理菜单。
#
# 现在：检测到装完了就停下，把 `canoe` 那条命令指给他；真要重装得显式说。
check "★ install.sh 有「已经装过」的闸门" \
      "$(grep -q '已经装过 Canoe' "$INSTALL" && echo 0 || echo 1)"
check "★ 闸门里给出的是管理菜单那条命令（而不是继续问）" \
      "$(grep -q '平时请用\*\*管理脚本\*\*' "$INSTALL" && echo 0 || echo 1)"
# 这里踩过一次：提示里那条给用户复制的命令指到了 install.sh 自己 ——
# 用户照着敲，又绕回同一个死循环。指过去的必须是菜单脚本。
check "★ 那条命令指的是 canoe.sh，不是 install.sh 自己" \
      "$(grep -q 'curl -fsSL \$MENU_RAW' "$INSTALL" && echo 0 || echo 1)"
check "MENU_RAW 确实指向 canoe.sh" \
      "$(grep -qE '^MENU_RAW=.*canoe\.sh' "$INSTALL" && echo 0 || echo 1)"
# 判据必须是三个都齐 —— 只看 .venv 的话，上一轮装到一半崩掉的机器
# 会被这条挡住，而那正是最该重跑的场合。
# 先压成一行再匹配 —— 那条 if 用 \ 折了行，逐行 grep 看不全。
INSTALL_FLAT="$(tr '\n' ' ' < "$INSTALL")"
check "★ 判定「装完了」看的是 .venv + .env + systemd 单元三样齐全" \
      "$(grep -qE 'SERVER_DIR/\.venv.*SERVER_DIR/\.env.*canoe-api\.service' <<< "$INSTALL_FLAT" \
        && echo 0 || echo 1)"
check "★ 闸门在参数校验之后就跑（不能等到装了一半才拦）" \
      "$(grep -n '已经装过 Canoe' "$INSTALL" | head -1 | cut -d: -f1 | \
        awk -v u="$(grep -n '0.1 交互向导' "$INSTALL" | head -1 | cut -d: -f1)" \
            '{ print ($1 < u) ? 0 : 1 }')"
check "★ 有 --reconfigure 这个显式出口" \
      "$(grep -q -- '--reconfigure|--force' "$INSTALL" && echo 0 || echo 1)"
check "环境变量 CANOE_RECONFIGURE 也能开（curl 那种写法传不进位置参数）" \
      "$(grep -q 'CANOE_RECONFIGURE' "$INSTALL" && echo 0 || echo 1)"
check "帮助里写了 --reconfigure" \
      "$(grep -q -- '--reconfigure         已经装过的机器上' <<< "$HELP_I" && echo 0 || echo 1)"

# 管理脚本自己走这两条路时得把闸门放开，否则会"静默不动"
check "★ 「重新走安装向导」带 --reconfigure" \
      "$(grep -A 4 'run_install_wizard()' "$TARGET" | grep -q -- '--reconfigure' && echo 0 || echo 1)"
check "★ 菜单「安装」在已装机器上确认后也带 --reconfigure" \
      "$(grep -q 'already -eq 1' "$TARGET" && echo 0 || echo 1)"

printf '\n[12] 提示里不要塞 sudo\n'
# 用户的提示符是 `root@localhost:~#` —— 他本来就是 root，每条命令前面挂个
# sudo 纯属噪音；而且 Debian 最小安装**根本没装 sudo**，真敲下去是
# `sudo: command not found`（这个坑真踩过两次）。门面上的命令一律不带它，
# 只有"你不是 root"那种劝阻里才提。
check "★ 管理脚本打印的提示里不带 sudo 命令" \
      "$(grep -qE '^[[:space:]]*(dim|log|printf|ok|warn) .*sudo (canoe|bash|rm|systemctl|cat|grep)' "$TARGET" && echo 1 || echo 0)"
# 排除 `sudo -u <用户>` —— 那是 as_user() 内部"切到 canoe 用户"的实现，
# 跟提示用户"你该敲 sudo xxx"是两回事。
check "★ 管理脚本的 usage 里不是 sudo 开头的命令" \
      "$(grep -qE '^[[:space:]]+sudo [^-]' "$TARGET" && echo 1 || echo 0)"
check "★ 安装向导的用法里不是 sudo 开头的命令" \
      "$(grep -qE '^[[:space:]]+sudo bash' "$INSTALL" && echo 1 || echo 0)"
check "★ 「已经装过」那段提示里的命令也不带 sudo" \
      "$(grep -qE '^      sudo ' "$INSTALL" && echo 1 || echo 0)"

REPO="$(cd "$HERE/../.." && pwd)"
for doc in "$REPO/README.md" "$REPO/canoe-server/deploy/README.md"; do
    check "★ $(basename "$(dirname "$doc")")/$(basename "$doc") 里的命令不带 sudo 前缀" \
          "$(grep -nE '^[[:space:]]*sudo (bash|canoe|systemctl|git|rm|cat|grep|useradd|mkdir|cp|ln|certbot|nginx|ufw|pip|python)' "$doc" \
            && echo 1 || echo 0)"
done
# 不是 root 的用户也得有道儿 —— 文档开头得说明白
check "文档开头说明了「本来就是 root 就直接敲」" \
      "$(grep -q '已经是 root' "$REPO/README.md" && echo 0 || echo 1)"
check "非 root 的漏网提示保留（劝阻别人用 curl|bash 那句）" \
      "$(grep -q 'curl … | bash' "$REPO/README.md" && echo 0 || echo 1)"

printf '\n[13] 发布客户端（canoe release）\n'
check "帮助里有 release" \
      "$(grep -qE '^[[:space:]]*release ' <<< "$(bash "$TARGET" help 2>&1)" && echo 0 || echo 1)"
check "★ 子命令能派发到 cmd_release" \
      "$(grep -qE '^ *release\|' "$TARGET" && echo 0 || echo 1)"
check "★ 菜单项数不变（release 不进菜单，用户点名要 1-8+0）" \
      "$(grep -qE "printf '   release" "$TARGET" && echo 1 || echo 0)"

# 真跑一遍：拿一个假的服务端目录，as_user 直接执行（测试机上是 root），
# 放一个假的 .venv/bin/python 把收到的参数原样打印出来。
# 这样能验出**参数有没有被正确引用** —— 只看"调了 cmd_release"是看不出
# `--notes "两个 词"` 会不会被拆成两个参数的。
FAKE_SRV="$TMP/fakesrv"
mkdir -p "$FAKE_SRV/.venv/bin"
: > "$FAKE_SRV/release.py"
cat > "$FAKE_SRV/.venv/bin/python" <<'PYEOF'
#!/usr/bin/env bash
# 假 python：把收到的 argv 一行一个打出来
printf 'ARGC=%d\n' "$#"
for a in "$@"; do printf 'ARG=<%s>\n' "$a"; done
PYEOF
chmod +x "$FAKE_SRV/.venv/bin/python"

run_release() {
    # $@ = 传给 cmd_release 的参数
    local inner="source '$TARGET'; need_root(){ :; }; require_installed(){ :; }
                 as_user(){ eval \"\$*\"; }; SERVER_DIR='$FAKE_SRV'; cmd_release"
    local q=""
    local a
    for a in "$@"; do q+=" $(printf '%q' "$a")"; done
    bash -c "$inner$q" 2>&1
}

OUT="$(run_release --notes '两个 词' /tmp/Canoe-1.0.1-win64.zip)"
check "跑得起来" "$(grep -q 'ARGC=' <<< "$OUT" && echo 0 || echo 1)" "$OUT"
check "★ 带空格的 --notes 没被拆开（参数正确引用了）" \
      "$(grep -q 'ARG=<两个 词>' <<< "$OUT" && echo 0 || echo 1)" "$OUT"
check "★ 参数个数对：release.py + --notes + 值 + 包路径 = 4" \
      "$(grep -q 'ARGC=4' <<< "$OUT" && echo 0 || echo 1)" "$OUT"
check "★ 以应用用户身份跑（不是 root 直接跑）" \
      "$(grep -q 'as_user' "$TARGET" && grep -q 'release.py' "$TARGET" && echo 0 || echo 1)"

OUT="$(run_release 2>&1)"
check "不给参数时打印用法" "$(grep -q 'canoe release' <<< "$OUT" && echo 0 || echo 1)" "$OUT"
check "用法里说了怎么把包弄上机器" \
      "$(grep -q 'scp ' <<< "$OUT" && echo 0 || echo 1)" "$OUT"

printf '\n%s\n' "$(printf '=%.0s' {1..48})"
printf '通过 %d 项，失败 %d 项\n' "$passed" "$failed"
printf '%s\n\n' "$(printf '=%.0s' {1..48})"

[[ $failed -eq 0 ]]


printf '%s\n\n' "$(printf '=%.0s' {1..48})"

[[ $failed -eq 0 ]]
