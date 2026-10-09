"""阶段1 · 真实联网验收测试。

这是阶段1「点启航能真的走代理」这条验收标准的自动化版本。

需要：
    · bin/sing-box.exe
    · 测试节点可达（one.leycc.com:33222）
    · 能访问外网

验证方法不是只看"请求成功了没有" —— 那样证明不了分流。
而是**读 sing-box 的内核日志**，看每个请求实际走了哪个出站：

    国外站点 -> outbound/shadowsocks[proxy]
    大陆站点 -> outbound/direct

这是唯一能证明「绕过大陆」真的生效的方式。

用法：
    python tests/test_live.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_client.config import config  # noqa: E402
from canoe_client.kernel import build_config  # noqa: E402
from canoe_client.options import RunOptions  # noqa: E402
from canoe_client.testnodes import build_proxy_outbound  # noqa: E402

TEST_PORT = 21919
FOREIGN_URL = "https://api.ipify.org"
CHINA_URL = "https://www.baidu.com"

passed = failed = skipped = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def skip(label: str, why: str) -> None:
    global skipped
    skipped += 1
    print(f"  [跳过] {label} —— {why}")


def port_open(port: int, timeout: float = 0.4) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def curl(url: str, port: int, timeout: int = 30) -> tuple[int, str]:
    """通过本地 mixed 入站发请求。用 socks5-hostname 让内核按域名分流。"""
    curl_exe = shutil.which("curl") or "curl"
    proc = subprocess.run(
        [
            curl_exe, "-sS", "-m", str(timeout),
            "--socks5-hostname", f"127.0.0.1:{port}",
            "-o", "-", "-w", "\n%{http_code}", url,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "").strip()
    if "\n" in out:
        body, code = out.rsplit("\n", 1)
        try:
            return int(code), body
        except ValueError:
            return 0, body
    return (0, out) if proc.returncode != 0 else (200, out)


def main() -> int:
    print("\n== 轻舟 · 真实联网验收 ==\n")

    exe = config.find_singbox()
    if exe is None:
        skip("全部", "bin/sing-box.exe 不存在")
        return 0

    # 节点可达性
    try:
        with socket.create_connection(("one.leycc.com", 33222), timeout=8):
            pass
    except OSError as exc:
        skip("全部", f"测试节点不可达：{exc}")
        return 0
    print(f"测试节点可达 · 本地端口 {TEST_PORT}\n")

    # 用 debug 级别，才能从日志里看出走了哪个出站
    opts = RunOptions(use_system_proxy=True, mixed_port=TEST_PORT, log_level="debug")
    cfg = build_config(build_proxy_outbound(), opts)

    fd, cfg_path = tempfile.mkstemp(prefix="canoe-live-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)

    log = open(tempfile.mktemp(prefix="canoe-live-", suffix=".log"), "w+", encoding="utf-8")
    proc = subprocess.Popen(
        [str(exe), "run", "-c", cfg_path],
        cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
    )

    try:
        # 等端口就绪
        for _ in range(60):
            if port_open(TEST_PORT):
                break
            if proc.poll() is not None:
                print("  内核启动失败，日志：")
                log.flush()
                print(Path(log.name).read_text(encoding="utf-8", errors="replace")[:1500])
                return 1
            time.sleep(0.5)
        else:
            skip("全部", "内核端口一直没就绪")
            return 0

        print("[1] 启航：国外站点走代理")
        code, body = curl(FOREIGN_URL, TEST_PORT)
        check(f"代理请求成功（HTTP {code}）", code == 200, body[:200])
        check("返回了出口 IP", bool(re.search(r"\d+\.\d+\.\d+\.\d+", body)), body[:120])

        print("\n[2] 绕过大陆：国内站点走直连")
        code_cn, _ = curl(CHINA_URL, TEST_PORT)
        check(f"国内站点请求成功（HTTP {code_cn}）", code_cn in (200, 301, 302), str(code_cn))

        print("\n[3] 靠岸：停止后代理端口关闭")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(1.5)
        check("内核已退出", proc.poll() is not None)
        check("代理端口已关闭", not port_open(TEST_PORT))

        # --- 关键：从内核日志确认分流真的生效 ---
        print("\n[4] 内核日志证明分流生效（不是靠猜）")
        log.flush()
        text = Path(log.name).read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)   # 去 ANSI 颜色

        foreign_lines = [
            ln for ln in text.splitlines()
            if "api.ipify.org" in ln and "outbound/" in ln
        ]
        china_lines = [
            ln for ln in text.splitlines()
            if "baidu.com" in ln and "outbound/" in ln
        ]

        check("日志里有国外站点的出站记录", bool(foreign_lines), str(len(foreign_lines)))
        check("日志里有国内站点的出站记录", bool(china_lines), str(len(china_lines)))

        if foreign_lines:
            check(
                "★ 国外站点走 shadowsocks 代理出站",
                any("shadowsocks" in ln for ln in foreign_lines),
                foreign_lines[0][:160],
            )
        if china_lines:
            check(
                "★ 国内站点走 direct 直连出站（绕过大陆生效）",
                any("outbound/direct" in ln for ln in china_lines),
                china_lines[0][:160],
            )

        if foreign_lines and china_lines:
            print("\n  出站记录：")
            for ln in (foreign_lines[:1] + china_lines[:1]):
                print(f"    {ln.strip()[:150]}")

    finally:
        if proc.poll() is None:
            proc.kill()
        log.close()
        Path(cfg_path).unlink(missing_ok=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
