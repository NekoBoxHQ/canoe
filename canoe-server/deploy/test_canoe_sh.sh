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

# 把脚本当库加载（它被 source 时不会跑 main）
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

# 菜单：喂 0 让它渲染一遍就退出
MENU="$(printf '0\n' | bash "$TARGET" 2>&1)"
for item in "安装 Canoe" "启动 Canoe" "停止 Canoe" "重启 Canoe" \
            "Canoe 状态" "Canoe 配置" "升级 Canoe" "卸载 Canoe"; do
    check "菜单里有「$item」" "$(grep -qF "$item" <<< "$MENU" && echo 0 || echo 1)"
done
check "菜单编号 0-8 齐全" \
      "$(for i in 1 2 3 4 5 6 7 8 0; do grep -qE "^[[:space:]]*$i[[:space:]]" <<< "$MENU" || exit 1; done; echo 0)"

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

printf '\n[7] 语法\n'
check "bash -n 通过" "$(bash -n "$TARGET" 2>/dev/null && echo 0 || echo 1)"

printf '\n%s\n' "$(printf '=%.0s' {1..48})"
printf '通过 %d 项，失败 %d 项\n' "$passed" "$failed"
printf '%s\n\n' "$(printf '=%.0s' {1..48})"

[[ $failed -eq 0 ]]
