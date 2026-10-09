"""开发用启动脚本：python run.py

生产建议：
    uvicorn canoe_server.app:app --host 0.0.0.0 --port 8000 --workers 4
前面放 Nginx/Caddy 终止 HTTPS。
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
