"""下载 / 更新大陆分流规则集到 bin/ruleset/。

「绕过大陆」依赖两个 sing-box 规则集文件：

    geosite-cn.srs   域名规则（哪些域名算大陆）
    geoip-cn.srs     IP 规则（哪些 IP 段算大陆）

客户端优先用本地的（`kernel.py` 里的 `_ruleset_defs`），
找不到才回退成启动时远程下载 —— 而 raw.githubusercontent.com 在国内
经常连不上，所以**正式发布前请务必跑一次这个脚本**。

用法：
    python scripts/fetch_rulesets.py            # 缺失才下载
    python scripts/fetch_rulesets.py --force    # 强制更新
    python scripts/fetch_rulesets.py --check    # 只检查现状，不下载
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
RULESET_DIR = HERE.parent / "bin" / "ruleset"

SOURCES = {
    "geosite-cn.srs": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs",
    "geoip-cn.srs": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs",
}

# 小于这个大小基本可以断定下到的是错误页而不是规则集
MIN_SIZE = 4096


def fetch(name: str, url: str, force: bool) -> bool:
    target = RULESET_DIR / name
    if target.is_file() and not force:
        size = target.stat().st_size
        if size >= MIN_SIZE:
            print(f"  [=] {name} 已存在（{size} 字节），跳过")
            return True
        print(f"  [!] {name} 太小（{size} 字节），重新下载")

    print(f"  [↓] {name} <- {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Canoe-Client/1.0"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"  [✗] 下载失败：{exc}")
        return False

    if len(data) < MIN_SIZE:
        print(f"  [✗] 内容可疑（只有 {len(data)} 字节），不写入")
        return False

    RULESET_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"  [✓] 已写入 {target}（{len(data)} 字节）")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="下载大陆分流规则集")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    parser.add_argument("--check", action="store_true", help="只检查，不下载")
    args = parser.parse_args()

    print(f"\n规则集目录: {RULESET_DIR}\n")

    if args.check:
        ok = True
        for name in SOURCES:
            path = RULESET_DIR / name
            if path.is_file() and path.stat().st_size >= MIN_SIZE:
                print(f"  [✓] {name}（{path.stat().st_size} 字节）")
            else:
                print(f"  [✗] {name} 缺失或损坏 —— 绕过大陆会失效")
                ok = False
        return 0 if ok else 2

    results = [fetch(name, url, args.force) for name, url in SOURCES.items()]

    if all(results):
        print("\n完成。")
        return 0
    print("\n有文件没下成功。影响：客户端会退化成启动时远程下载，")
    print("国内网络下大概率失败，「绕过大陆」将不生效。")
    print("可以手动下载后放进 bin/ruleset/。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
