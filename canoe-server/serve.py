"""统一启动器：python serve.py

读 `.env` 决定监听地址、端口、要不要 TLS。systemd 也是调它，
所以"改端口 / 换证书"永远只需要动 .env，不用碰服务单元。

    PORT=58588              # 客户端固定拿这个口取更新和订阅
    PANEL_PORT=0            # 0 = 和 PORT 同口；填别的就另开一个面板口
    HOST=0.0.0.0
    TLS_CERT=/etc/canoe/live/fullchain.pem
    TLS_KEY=/etc/canoe/live/privkey.pem

两个端口是**同一个进程**监听的（见 _serve_all）——
拆成两个进程的话，推送中心（services/broadcast.py）会各存一份，
管理端在面板口发的广播就到不了客户端在另一个口上的长连接。

⚠ 此外也只能开 1 个 worker，原因同上。

    python serve.py                     # 按 .env
    python serve.py --port 9000 --no-tls
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from canoe_server import app as app_module
from canoe_server.config import settings


def _uvicorn_config(host: str, port: int, cert: str | None, key: str | None, reload: bool):
    import uvicorn

    return uvicorn.Config(
        "canoe_server.app:app",
        host=host,
        port=port,
        workers=1,               # ← 见文件头说明，不要改
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ssl_certfile=cert,
        ssl_keyfile=key,
        log_level="debug" if settings.debug else "info",
    )


def _serve_all(configs) -> None:
    """同一个事件循环里跑多个 uvicorn —— 共享同一个 app，也就共享推送中心。

    两个细节必须处理，否则 Ctrl+C 会只停掉其中一个：

      1. 只让第一个监听器接管信号，其余的把 capture_signals 换成空实现；
      2. 任意一个开始退出，就把其余的也置上 should_exit。
    """
    import contextlib

    import uvicorn

    servers = [uvicorn.Server(cfg) for cfg in configs]

    # 只让第一个监听器接管 SIGINT/SIGTERM，其余的用一个空上下文顶掉。
    # 直接赋实例属性即可 —— 这样不会被绑成方法，调用时不会多塞一个 self。
    for server in servers[1:]:
        server.capture_signals = contextlib.nullcontext

    async def runner() -> None:
        tasks = [asyncio.create_task(s.serve()) for s in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for s in servers:
            s.should_exit = True
        # 别把异常吞掉：某个监听器启动失败时，必须让人看到原因，
        # 否则表现成"只有一个端口起来了"这种莫名其妙的症状
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                print(f"[x] 监听器退出：{result!r}", file=sys.stderr)

    asyncio.run(runner())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="轻舟 / Canoe 服务端启动器")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port,
                        help="客户端口（更新 / 订阅 / API）")
    parser.add_argument("--panel-port", type=int, default=settings.panel_port,
                        help="面板口；0 表示和 --port 同口")
    parser.add_argument("--cert", default=settings.tls_cert, help="TLS 证书路径")
    parser.add_argument("--key", default=settings.tls_key, help="TLS 私钥路径")
    parser.add_argument("--no-tls", action="store_true", help="强制不用 TLS")
    parser.add_argument("--reload", action="store_true", help="改代码自动重启（开发用）")
    args = parser.parse_args(argv)

    use_tls = bool(args.cert and args.key) and not args.no_tls
    cert = args.cert if use_tls else None
    key = args.key if use_tls else None
    scheme = "https" if use_tls else "http"

    panel_port = args.panel_port or args.port
    if panel_port <= 0:
        print(f"[x] 面板端口不合法：{panel_port}", file=sys.stderr)
        return 2

    base = f"{scheme}://{args.host}"

    print(f"[*] {settings.app_name}")
    print(f"[*] 客户端口 {base}:{args.port}   （更新 / 订阅 / API）")
    if panel_port != args.port:
        print(f"[*] 面板口   {base}:{panel_port}   （客户端口不再响应 {settings.panel_path}）")
    else:
        print(f"[*] 面板     {base}:{args.port}{settings.panel_path}   （与客户端同口）")
    if use_tls:
        print(f"[*] TLS 证书 {cert}")
    else:
        print("[!] 未启用 TLS（HTTP）—— 对外暴露时请放在 HTTPS 反代后面")
    if not settings.public_base_url:
        print("[!] 没配 PUBLIC_BASE_URL，安装包下载地址会按请求里的 Host 现算")

    # 告诉 app 面板到底监听在哪 —— 中间件靠它判断"这个连接是不是客户端口"。
    # 用实际值而不是 settings，因为命令行可能覆盖配置。
    app_module.PANEL_LISTEN_PORT = panel_port if panel_port != args.port else 0

    configs = [_uvicorn_config(args.host, args.port, cert, key, args.reload)]
    if panel_port != args.port:
        configs.append(_uvicorn_config(args.host, panel_port, cert, key, args.reload))

    if len(configs) == 1:
        import uvicorn

        uvicorn.Server(configs[0]).run()
    else:
        _serve_all(configs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
