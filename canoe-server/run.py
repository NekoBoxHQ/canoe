"""开发用启动脚本：python run.py

生产统一使用 ``serve.py``（或安装后的 systemd 服务）。SSE 推送中心和
进程内登录限速都要求单 Worker；不要按通用 FastAPI 模板改成多 Worker。
"""
from __future__ import annotations

from canoe_server.config import settings

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "canoe_server.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )
