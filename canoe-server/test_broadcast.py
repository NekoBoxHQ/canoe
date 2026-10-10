"""推送中心（SSE）的定向投递测试。

**不需要服务端在跑** —— 全程只碰内存里的 EventHub，不联网、不碰数据库。

    cd canoe-server
    .venv/Scripts/python test_broadcast.py        # Windows
    .venv/bin/python test_broadcast.py            # Linux

盯的是一件事：**哪条推送该点名，哪条该广播。**

    点名：kick（封禁 / 踢下线）、config_changed 的订阅变更
    广播：config_changed 的全局配置版本、release

为什么值得单独测：`EventHub.publish()` 不传 `user_id` 就是**广播给所有人**
（payload 里那个 user_id 字段是给客户端自查的，投递根本不看它）。
真机上就是这么出事的 —— `notify_kick()` 忘了传，管理员封 B，
**所有在线用户**（包括正开着客户端的 A）一起被踹下线：
"我登录 A，封禁 B，A 就下线了。"
"""
from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from canoe_server.services import broadcast as bc  # noqa: E402

passed = failed = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {extra}")


class FakeLoop:
    """顶替事件循环：publish() 里的 call_soon_threadsafe 会被记下来，
    我们就能看清"这条事件到底投给了谁"，而不用真跑一个 asyncio 循环。"""

    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []

    def call_soon_threadsafe(self, fn, event, targets) -> None:  # noqa: ANN001
        self.calls.append((list(targets), dict(event)))

    def is_closed(self) -> bool:
        return False

    def delivered(self) -> list[dict]:
        return [ev for _t, ev in self.calls]


def with_two_users():
    """挂两条连接：账号 1 和账号 2。返回 (我的队列, 别人的队列, 假事件循环)。"""
    hub = bc.hub
    loop = FakeLoop()
    old_loop = hub._loop
    hub._loop = loop
    mine = hub._register(1)
    theirs = hub._register(2)
    return hub, mine, theirs, loop, old_loop


def cleanup(hub, mine, theirs, loop, old_loop) -> None:
    hub._drop(mine)
    hub._drop(theirs)
    hub._loop = old_loop


def main() -> int:
    print("\n== 轻舟 · 推送中心（SSE）定向投递测试 ==\n")

    hub, mine, theirs, loop, old_loop = with_two_users()
    try:
        # --- 1. kick 必须点名 ---
        print("[1] kick：只该踢被封的那个人")
        n = bc.notify_kick(1, "账号已被封禁", permanent=True)
        sent = loop.calls[-1][0] if loop.calls else []
        check("★ 只投给目标账号那条连接（另一条一个字节都收不到）",
              sent == [mine], f"投给了 {len(sent)} 条，期望 1 条")
        check("★ 返回的目标数也是 1（不是全员广播）", n == 1, str(n))
        # permanent=True 现在会推两条（kick + 关闭哨兵），kick 是**倒数第二条**
        ev = loop.delivered()[-2]
        check("事件是 kick 且带 permanent", ev.get("type") == "kick" and ev.get("permanent") is True, str(ev))
        check("payload 里也带 user_id（客户端要拿它对名字）",
              ev.get("user_id") == 1, str(ev))

        # --- 2. 订阅变更：也是点名 ---
        print("\n[2] config_changed（订阅）：只叫醒这个人")
        before = len(loop.calls)
        bc.notify_subscription_changed(1, "rev-9")
        sent = loop.calls[before][0]
        check("★ 只投给自己的连接", sent == [mine], f"{len(sent)} 条")

        # --- 3. 全局的那两条：必须仍然是广播 ---
        #    别因为"kick 出过事"就把所有推送都改成定向 —— 全局配置版本变了
        #    和发了新版本，是所有人都要知道的。
        print("\n[3] 全局事件：仍旧广播给所有人")
        before = len(loop.calls)
        n = bc.notify_config_changed(7, "rev-x", "节点甲")
        sent = set(loop.calls[before][0])
        check("★ config_changed（全局）广播给两条连接",
              sent == {mine, theirs} and n == 2, f"{len(sent)} 条 / n={n}")

        before = len(loop.calls)
        n = bc.notify_release("9.9.9")
        sent = set(loop.calls[before][0])
        check("★ release 广播给两条连接", sent == {mine, theirs} and n == 2, f"{len(sent)} 条")

        # --- 4. 封禁要把连接**真的关掉** ---
        #    原来是"只发一条 kick 就算踢了"—— 客户端不理它，这条连接就一直
        #    挂着（吊销不彻底）。现在推完原因再断管子。
        print("\n[4] 封禁：推完原因就断管子")
        before = len(loop.calls)
        bc.notify_kick(1, "账号已被封禁", permanent=True)
        new = loop.calls[before:]
        check("★ 推了两条：先 kick、后关闭哨兵", len(new) == 2, f"{len(new)} 条")
        check("★ 第一条是 kick 事件", new and new[0][1].get("type") == "kick", str(new[:1]))
        # ⚠ 这里只能用 type 比、不能用 `is bc._CLOSE`：FakeLoop 记的是
        #   `dict(event)` 的副本，身份已经变了（真队列里进的是原对象，
        #   所以下面 [5] 那条"哨兵不会被发出去"仍然测得到身份判断）。
        check("★ 第二条是内部关闭哨兵，且只给目标账号",
              len(new) == 2 and new[1][1].get("type") == "__close__" and new[1][0] == [mine],
              str(new[1][0] if len(new) == 2 else None))

        before = len(loop.calls)
        bc.notify_kick(1, "管理员把你踢下线了", permanent=False)
        new = loop.calls[before:]
        check("★ 踢单个会话（非封禁）只通知，不硬断（别的设备还能用）",
              len(new) == 1, f"{len(new)} 条")

        # --- 5. 哨兵进了队列，stream() 必须自己收摊 ---
        print("\n[5] 哨兵不能变成一帧 SSE")
        import asyncio  # noqa: PLC0415

        # 先把上面手工挂的那条（账号 1）撤掉，免得下面数连接数时把它算进来
        hub._drop(mine)

        async def scenario() -> list[str]:
            hub._loop = asyncio.get_running_loop()      # publish 要跨线程投递
            seen: list[str] = []

            async def collect() -> None:
                async for ev in hub.stream(1, {"revision": "r"}):
                    seen.append(str(ev.get("type")))

            task = asyncio.create_task(collect())
            for _ in range(200):                        # 等它注册上
                await asyncio.sleep(0.01)
                if hub.connections_of(1):
                    break
            hub.disconnect_user(1)
            await asyncio.wait_for(task, timeout=5)
            return seen

        seen = asyncio.run(scenario())
        check("★ 收到 hello 之后就结束了（哨兵没被当成事件发出去）",
              seen == ["hello"], str(seen))
        check("★ 连接已从 hub 摘掉", hub.connections_of(1) == 0, str(hub.connections_of(1)))

        # --- 6. kick 和关闭哨兵必须**都**在队列里，且 kick 在前 ---
        #     踩过：给关闭哨兵加了"清空队列给它腾位置"，于是同一次事件循环里
        #     kick 刚塞进去就被 close 清掉了（消费者的唤醒排在两个回调之后）。
        #     真机表现是"封禁推不来 kick"——客户端只看到连接断了，不知道为什么。
        #     smoke_test 的 [15] 抓到的就是这个。
        print("\n[6] kick + 关闭哨兵：都在，且 kick 在前")
        q: asyncio.Queue = asyncio.Queue(maxsize=bc.QUEUE_SIZE)
        hub._fanout({"type": "kick", "user_id": 1}, [q])
        hub._fanout(bc._CLOSE, [q])
        drained = []
        while not q.empty():
            drained.append(q.get_nowait())
        check("★ 两条都在（kick 没被清掉）", len(drained) == 2, str(drained))
        check("★ 顺序是先 kick、后关闭",
              len(drained) == 2 and drained[0].get("type") == "kick"
              and drained[1] is bc._CLOSE, str(drained))

        # --- 7. 没人听的时候不能炸 ---
        print("\n[7] 边界")
        hub._drop(mine)
        hub._drop(theirs)
        check("★ 一条连接都没有时 publish 返回 0，不抛异常",
              bc.notify_kick(1, "x") == 0)
    finally:
        cleanup(hub, mine, theirs, loop, old_loop)

    print(f"\n{'=' * 48}")
    print(f"通过 {passed} 项，失败 {failed} 项")
    print(f"{'=' * 48}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
