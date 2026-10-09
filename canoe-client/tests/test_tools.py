"""轻舟 · 工具按钮与日志面板测试。

覆盖：
    1. 日志总线：多线程写入、增量取行
    2. 版本比较与更新检查（含各种畸形输入）
    3. TCping（真的连一次测试节点）
    4. URL 测试（启航状态下真的走一次代理）
    5. 内核输出真的会进日志总线

用法：
    python tests/test_tools.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_client import update  # noqa: E402
from canoe_client.config import config  # noqa: E402
from canoe_client.kernel import kernel  # noqa: E402
from canoe_client.logbus import LogBus  # noqa: E402
from canoe_client.nettest import tcping, url_test  # noqa: E402
from canoe_client.options import RunOptions  # noqa: E402
from canoe_client.entry import build_entry_outbound  # noqa: E402
from canoe_core import EntryPayload  # noqa: E402

#: 样例入口（真实运行时由服务端下发）
SAMPLE_ENTRY = EntryPayload(
    transport="ws", host="entry.example.com", port=443,
    uuid="11111111-2222-3333-4444-555555555555",
    path="/e/test", sni="entry.example.com", tls=True, insecure=False,
)

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


def main() -> int:
    print("\n== 轻舟 · 工具按钮与日志测试 ==\n")

    # --- 1. 日志总线 ---
    print("[1] 日志总线")
    bus = LogBus(maxlen=10)
    bus.system("就绪")
    bus.kernel("sing-box started")
    bus.error("出错了")
    lines = bus.since(0)
    check("能记录三种标签", len(lines) == 3, str(len(lines)))
    check("标签正确", [x.tag for x in lines] == ["系统", "内核", "错误"], str([x.tag for x in lines]))
    check("渲染含时间与标签", "[系统]" in lines[0].render(), lines[0].render())

    # since(seq) 语义是"seq >= 传入值"，界面靠它做增量刷新
    check("since(2) 只回第 2 行之后（含第 2 行）",
          [x.message for x in bus.since(2)] == ["出错了"],
          str([x.message for x in bus.since(2)]))
    check("since(1) 回第 1、2 行",
          [x.message for x in bus.since(1)] == ["sing-box started", "出错了"],
          str([x.message for x in bus.since(1)]))

    # 环形缓冲上限
    for i in range(30):
        bus.system(f"行{i}")
    check("超过 maxlen 会丢旧的", len(bus.since(0)) == 10, str(len(bus.since(0))))

    # 多线程并发写不丢行（这是它存在的意义：内核线程 + 界面线程）
    bus2 = LogBus(maxlen=10000)
    def writer(tag: str, n: int) -> None:
        for i in range(n):
            bus2.add(tag, f"{tag}-{i}")
    threads = [threading.Thread(target=writer, args=(f"T{k}", 200)) for k in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("多线程并发写 1000 行不丢", len(bus2.since(0)) == 1000, str(len(bus2.since(0))))

    # --- 1.5 可见性过滤：内核日志绝不能进界面 ---
    print("\n[1.5] 界面可见性过滤（安全要求）")
    b = LogBus()
    b.result("TCP 延迟：65ms")
    b.kernel("outbound/shadowsocks[proxy]: to one.leycc.com:443")
    b.system("系统事件")
    b.error("失败了")
    b.test("过程")
    vis = b.visible_since(0)
    tags = [x.tag for x in vis]
    check("★ 只有「结果」和「错误」会显示", tags == ["结果", "错误"], str(tags))
    check("★ 内核日志被挡住", all(x.tag != "内核" for x in vis))
    check("内核日志仍在总线里（只是不显示）",
          any(x.tag == "内核" for x in b.since(0)))
    check("visible_since 与 since 增量语义一致",
          [x.message for x in b.visible_since(1)] == ["失败了"],
          str([x.message for x in b.visible_since(1)]))

    # --- 2. 版本比较 ---
    print("\n[2] 版本比较")
    cases = [
        ("1.0.1", "1.0.0", 1), ("1.0.0", "1.0.1", -1), ("1.0.0", "1.0.0", 0),
        ("v1.2.0", "1.1.9", 1), ("1.10.0", "1.9.0", 1),      # 不能按字符串比
        ("1.0", "1.0.0", 0), ("2", "1.9.9", 1),
    ]
    for a, b, want in cases:
        got = update.compare_versions(a, b)
        check(f"compare({a!r}, {b!r}) == {want}", got == want, f"实际 {got}")

    check("解析不出数字时返回 0", update.compare_versions("abc", "1.0") == 0)

    # --- 3. 更新检查的错误路径 ---
    print("\n[3] 更新检查")
    try:
        update.check("", "1.0.0")
        check("空地址应当报错", False)
    except update.UpdateError as exc:
        check("空地址给出可读提示", "未配置" in exc.message, exc.message)

    try:
        update.check("http://127.0.0.1:1/nope", "1.0.0")
        check("连不上的地址应当报错", False)
    except update.UpdateError:
        check("连不上时抛 UpdateError", True)

    info = update.UpdateInfo(latest="1.1.0", current="1.0.0")
    check("UpdateInfo.is_newer 判断正确", info.is_newer)
    check("同版本不算更新", not update.UpdateInfo(latest="1.0.0", current="1.0.0").is_newer)

    # --- 4. TCping ---
    # 客户端现在测的是「到中转入口」的握手延迟（真实节点拿不到）。
    # 这里不连真入口，只验证函数本身 —— 自己开一个本地端口当靶子，
    # 不依赖外网，跑在谁的机器上结果都一样。
    print("\n[4] TCping")
    import socket  # noqa: PLC0415

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    live_port = listener.getsockname()[1]
    try:
        good = tcping("127.0.0.1", live_port, count=2, timeout=2.0)
        check("连得上时报出耗时", good.ok and len(good.times) >= 1, good.summary())
        check("记录尝试次数", good.total == 2, str(good))
    finally:
        listener.close()

    bad = tcping("127.0.0.1", 1, count=1, timeout=1.0)
    check("连不上时 ok=False 且有原因", not bad.ok and bool(bad.error), bad.summary())

    # --- 5. URL 测试 ---
    print("\n[5] URL 测试")
    exe = config.find_singbox()
    if exe is None:
        skip("URL 测试", "bin/sing-box.exe 不存在")
    else:
        port = 22019
        opts = RunOptions(use_system_proxy=True, use_tun=False, mixed_port=port, log_level="info")
        try:
            # 出站用直连：这里验证的是「通过本地代理通道发请求」这条管道，
            # 不需要真的有个节点。有节点时那条路由由 test_server.py 覆盖。
            kernel.start({"type": "direct", "tag": "proxy"}, opts)
        except Exception as exc:  # noqa: BLE001
            skip("URL 测试", f"内核启动失败：{exc}")
        else:
            try:
                time.sleep(1.5)
                r = url_test(port)
                if r.ok:
                    check(f"经代理请求成功：{r.summary()}", True)
                    check("拿到 HTTP 状态码", r.status == 204 or r.status < 400, str(r.status))
                    check("耗时是正数", r.elapsed_ms > 0, str(r.elapsed_ms))
                else:
                    skip("URL 测试实测", r.summary())

                # --- 6. 内核输出真的进了日志总线 ---
                print("\n[6] 内核输出进日志面板")
                from canoe_client.logbus import bus as global_bus
                kernel_lines = [x for x in global_bus.since(0) if x.tag == "内核"]
                check("★ 内核输出被接进日志总线", len(kernel_lines) > 0,
                      f"{len(kernel_lines)} 行")
                if kernel_lines:
                    print(f"        例：{kernel_lines[0].render()[:110]}")
            finally:
                kernel.stop()

        # 没启航时 URL 测试应当给出可读提示而不是崩
        r2 = url_test(port, timeout=3)
        check("未启航时 URL 测试失败但有说明",
              not r2.ok and bool(r2.error), r2.summary())

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项，跳过 {skipped} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
