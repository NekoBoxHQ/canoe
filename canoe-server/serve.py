"""统一启动器：python serve.py

读 `.env` 决定监听地址、端口、要不要 TLS。systemd 也是调它，
所以"改端口 / 换证书"永远只需要动 .env，不用碰服务单元。

    PORT=58588              # 对外端口（客户端固定拿这个取更新和订阅）
    HOST=0.0.0.0
    TLS_CERT=/etc/canoe/fullchain.pem   # 这两项都填了就直接 HTTPS
    TLS_KEY=/etc/canoe/privkey.pem      # 留空则跑 HTTP，交给前面的 Nginx

⚠ 只能开 1 个 worker —— 推送中心是进程内的内存结构，多 worker 时
   管理端的广播只在它自己那个进程里扩散。见 services/broadcast.py。

命令行可以覆盖（方便临时试）：
    python serve.py --port 9000 --no-tls
"""
from __future__ import annotations

import argparse
import sys

from canoe_server.config import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="轻舟 / Canoe 服务端启动器")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--cert", default=settings.tls_cert, help="TLS 证书路径")
    parser.add_argument("--key", default=settings.tls_key, help="TLS 私钥路径")
    parser.add_argument("--no-tls", action="store_true", help="强制不用 TLS")
    parser.add_argument("--reload", action="store_true", help="改代码自动重启（开发用）")
    args = parser.parse_args(argv)

    use_tls = bool(args.cert and args.key) and not args.no_tls
    scheme = "https" if use_tls else "http"

    print(f"[*] {settings.app_name}")
    print(f"[*] {scheme}://{args.host}:{args.port}")
    print(f"[*] 管理面板 {scheme}://{args.host}:{args.port}{settings.panel_path}")
    if use_tls:
        print(f"[*] TLS 证书 {args.cert}")
    else:
        print("[*] 未启用 TLS（HTTP）—— 对外暴露时请务必放在 Nginx 后面")
    if not settings.public_base_url:
        print("[!] 没配 PUBLIC_BASE_URL，安装包下载地址会按请求里的 Host 现算")

    import uvicorn

    uvicorn.run(
        "canoe_server.app:app",
        host=args.host,
        port=args.port,
        workers=1,               # ← 见文件头说明，不要改
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        ssl_certfile=args.cert if use_tls else None,
        ssl_keyfile=args.key if use_tls else None,
        log_level="debug" if settings.debug else "info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
