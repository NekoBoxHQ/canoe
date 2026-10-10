"""「记住账号密码」的测试。

**不动你本机那份 client.json** —— 全程在自己的临时 APPDATA 里跑，
跑完连目录一起删。

    python tests/test_remember.py

盯的是这几件事：

  1. 密码在磁盘上必须是 DPAPI 密文，**配置文件原文里搜不到明文**
  2. 加解密往返一致；坏数据解不开时是"当没记住"，不是崩
  3. 取消勾选 = 立刻清掉本地那份（不是等下次登录成功才清）
  4. 换了 Windows 账户 / 换了机器 → 密文解不开 → 自动清干净，
     用户重新输一次就行，不该被卡住
  5. 老配置文件里少一两个子键，不该把整块 remember 重置掉
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import atexit
import tempfile
from pathlib import Path

# ⚠ 必须在 import config **之前**改 APPDATA：配置目录是模块加载时算出来的。
_TMP = Path(tempfile.mkdtemp(prefix="canoe-remember-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ["APPDATA"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_client import credstore  # noqa: E402
from canoe_client.config import CONFIG_FILE, config  # noqa: E402

passed = failed = 0

PASSWORD = "canoe-pass-12345-中文"


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {extra}")


def raw_config() -> str:
    return CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else ""


def main() -> int:
    print("\n== 轻舟客户端 · 记住账号密码 ==\n")
    print(f"    临时配置目录 {CONFIG_FILE}")

    try:
        # --- 1. DPAPI 本身 ---
        print("\n[1] 密码加密（Windows DPAPI）")
        check("这台机器上可用", credstore.available())

        blob = credstore.protect(PASSWORD)
        check("能加密出东西", bool(blob) and len(blob) > 40, str(len(blob)))
        check("★ 密文里不含明文", PASSWORD not in blob)
        check("★ 中文也没漏成 utf-8 字节",
              PASSWORD.encode("utf-8") not in blob.encode("utf-8", "ignore"))
        check("★ 解回来一模一样", credstore.unprotect(blob) == PASSWORD)
        check("同一明文两次加密结果不同（每次新随机）", credstore.protect(PASSWORD) != blob)

        for bad in ("@@@不是base64@@@", "", "YWJjZA==", "AAAA"):
            try:
                credstore.unprotect(bad)
                check(f"坏数据 {bad[:12]!r} 应当抛错", False)
            except credstore.CredStoreError:
                check(f"坏数据 {bad[:12]!r} -> CredStoreError", True)

        # --- 2. 默认不记 ---
        print("\n[2] 默认状态")
        check("新装的客户端默认不记", config.remember_enabled is False)
        check("默认读回来是空的", config.remembered_credentials() == ("", ""))

        # --- 3. 记下来 ---
        print("\n[3] 勾上之后")
        config.remember_credentials("canoe-user", PASSWORD)
        check("标记为已记", config.remember_enabled is True)
        got = config.remembered_credentials()
        check("★ 能原样读回来", got == ("canoe-user", PASSWORD), str(got))

        text = raw_config()
        check("配置文件确实写了", "remember" in text)
        check("★ 配置文件里搜不到明文密码", PASSWORD not in text)
        check("★ 配置里也没有密码的 base64", PASSWORD.encode().hex() not in text)
        check("用户名是明文存的（它不算秘密，界面要回填）", "canoe-user" in text)

        # --- 4. 换了机器 / 换了 Windows 账户 ---
        print("\n[4] 密文失效时（换机器 / 换 Windows 账户）")
        saved = json.loads(text)
        saved["remember"]["secret"] = "AAAA" + saved["remember"]["secret"][4:]
        CONFIG_FILE.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

        fresh = type(config)()          # 重新读一遍配置，模拟下次启动
        check("★ 解不开时当「没记住」，不抛异常", fresh.remembered_credentials() == ("", ""))
        check("★ 顺手把失效的那份清干净了", fresh.remember_enabled is False)
        check("★ 清完之后配置文件里连密文都不剩了",
              "secret" not in raw_config() or '"secret": ""' in raw_config(),
              raw_config()[:200])

        # --- 5. 取消勾选 = 立刻清 ---
        print("\n[5] 取消勾选")
        config.remember_credentials("canoe-user", PASSWORD)
        check("先记上", config.remembered_credentials()[0] == "canoe-user")
        config.forget_credentials()
        check("★ 清掉之后读不到", config.remembered_credentials() == ("", ""))
        check("★ 磁盘上也不该剩密文", PASSWORD not in raw_config() and "remember" in raw_config())

        # --- 6. 老配置文件缺子键 ---
        print("\n[6] 旧配置文件（少子键）")
        CONFIG_FILE.write_text(
            json.dumps({"remember": {"enabled": True}}, ensure_ascii=False), encoding="utf-8"
        )
        old = type(config)()
        check("★ 缺 username/secret 不会把 remember 整块弄丢",
              old._data.get("remember", {}).get("enabled") is True)
        check("缺的部分按默认值补上", old.remembered_credentials() == ("", ""))

    finally:
        shutil.rmtree(_TMP, ignore_errors=True)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
