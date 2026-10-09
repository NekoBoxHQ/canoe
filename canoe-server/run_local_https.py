"""本地 HTTPS 调试：python run_local_https.py [端口]

为什么需要它：客户端只在 HTTPS 下工作（`entry.insecure` 恒为 false），
所以拿 HTTP 起服务端是测不出真实链路的。这里用一张自签证书起一条 TLS 服务，
把客户端指向 https://localhost:8443 就能跑通全流程。

生产不这么用 —— 生产是 Nginx/Caddy 在前面终止 TLS，
uvicorn 只监听 127.0.0.1（见 deploy/nginx.example.conf）。

证书缺了会自动用 openssl 签一张（localhost + 127.0.0.1 都在 SAN 里）。
自签证书浏览器会报警告 —— 客户端那边用 CANOE_CA_BUNDLE 指这张证书即可
（见 canoe_client/config.py 的 ca_bundle），**不要**去关校验。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CERT_DIR = Path(__file__).resolve().parent / "data" / "certs"
CERT = CERT_DIR / "local-cert.pem"
KEY = CERT_DIR / "local-key.pem"
DEFAULT_PORT = 8443


def ensure_cert() -> None:
    if CERT.is_file() and KEY.is_file():
        return
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    print("[*] 没有本地证书，用 openssl 签一张自签的…")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "825",
            "-keyout", str(KEY),
            "-out", str(CERT),
            "-subj", "//CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,DNS:canoe.local,IP:127.0.0.1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[+] 证书已生成：{CERT}")


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    ensure_cert()

    # 发布出来的下载地址是拿 PUBLIC_BASE_URL 拼的。.env 里那个值是给生产
    # 用的（https://canoe.s-ui.com:58588），本地不改的话，服务端会告诉
    # 客户端"去 58588 下载"，而这台机器上根本没那个口 —— 冒烟测试走到
    # "把安装包下回来验 sha256" 直接连接被拒。
    # 必须在 import serve 之前设：settings 是在导入时就实例化的，
    # 而真实环境变量优先于 .env（pydantic-settings 的默认行为）。
    os.environ["PUBLIC_BASE_URL"] = f"https://127.0.0.1:{port}"

    # 直接复用 serve.py —— 端口/TLS 的处理只写一份，免得两边行为漂移。
    #
    # 显式把面板也钉在同一个口。不写的话 serve.py 会去读 .env 的 PANEL_PORT
    # （生产上那个是 58589），本地就变成"客户端在这口、面板在那口"，
    # 而下面的说明又写着"指向 8443 就能跑通全流程" —— 对不上，
    # 冒烟测试的面板那一段会整段 404。本机调试要的就是一个口。
    from serve import main as serve_main

    return serve_main(["--host", "127.0.0.1", "--port", str(port),
                       "--panel-port", str(port),
                       "--cert", str(CERT), "--key", str(KEY)])


if __name__ == "__main__":
    raise SystemExit(main())
