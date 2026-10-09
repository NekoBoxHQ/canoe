"""轻舟 / Canoe Server —— FastAPI 应用入口。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from canoe_core import BRAND_CN, SLOGAN_CN, VERSION, Api

from .config import settings
from .database import init_db
from .routers import admin, auth, client
from .services.broadcast import hub
from .services.updates import release_dir

app = FastAPI(
    title=settings.app_name,
    version=VERSION,
    description=f"{BRAND_CN} 服务端 · {SLOGAN_CN}",
)

# 桌面客户端不走浏览器 CORS，这里留一个受控的默认值方便你用 web 面板调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """调试时回原始异常，生产只回笼统信息。"""
    detail = repr(exc) if settings.debug else "服务器内部错误"
    return JSONResponse(
        status_code=500, content={"code": "internal_error", "detail": detail}
    )


#: 面板**实际**监听的端口，由 serve.py 在启动前填。0 = 与客户端同口（不拦截）。
#:
#: 为什么不用 settings.panel_port：那个值可能被命令行 --panel-port 覆盖，
#: 拿配置值去比对会得出错误结论（表现为"拦不住"）。这里要的是事实，不是配置。
PANEL_LISTEN_PORT: int = 0


@app.middleware("http")
async def panel_port_guard(request: Request, call_next):
    """面板另开了端口时，其它端口就不响应 /panel。

    客户端固定拿一个口取更新和订阅，那个口必须对所有用户开放；
    管理面板没必要让所有人看到 —— 扫描器少一个入口是一个。
    面板口仍然提供 /api/*，因为面板自己要调（同源，不需要 CORS）。

    本地端口从 scope["server"] 拿（uvicorn 会填）。
    """
    if PANEL_LISTEN_PORT:
        local_port = (request.scope.get("server") or ("", 0))[1]
        if local_port != PANEL_LISTEN_PORT and request.url.path.startswith(settings.panel_path):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    # 推送是从同步路由（线程池）发起的，得先记住事件循环才能跨线程投递
    hub.bind_loop(asyncio.get_running_loop())


# 客户端安装包自托管：/api/client/latest 返回的下载地址就落在这里
app.mount(
    Api.DOWNLOAD_PREFIX,
    StaticFiles(directory=str(release_dir())),
    name="downloads",
)


# 管理面板：纯静态（HTML+CSS+JS，无构建步骤），和 API 同一个端口。
# 注意 —— 面板本身只是个前端，**权限由 /api/admin/* 服务端二次校验**，
# 把 HTML 藏起来不算防护。见 docs/02-api.md 安全约定。
_panel_dir = Path(settings.panel_dir)
if _panel_dir.is_dir():
    app.mount(
        settings.panel_path,
        StaticFiles(directory=str(_panel_dir), html=True),
        name="panel",
    )


@app.get("/", include_in_schema=False)
def root():
    """根路径直接进面板。Nginx 前置时这条一般用不上。"""
    return RedirectResponse(settings.panel_path)


# 路由挂在 /api 下；/api/health 由 client 路由提供
app.include_router(auth.router, prefix="")
app.include_router(client.router, prefix="")
app.include_router(admin.router, prefix="")

_ = Api  # 路径常量在路由里直接用，这里只是保持 import 可见
