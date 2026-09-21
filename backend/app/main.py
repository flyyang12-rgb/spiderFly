"""SpiderFly 应用、接口与页面入口组装。"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import ai_agent, maintenance, task_versions
from .api import auth, users, audit, apps, overview, settings, tasks, executions, hosts
from .services import runtime


app = FastAPI(
    title="SpiderFly",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.on_event("startup")(runtime.startup)
app.on_event("shutdown")(runtime.shutdown)
for router in (
    ai_agent.router, maintenance.router, task_versions.router,
    settings.router, auth.router, users.router, audit.router, apps.router,
    overview.router, tasks.router, executions.router, hosts.router,
):
    app.include_router(router)


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        requested = (FRONTEND_DIST / path).resolve()
        if requested.is_file() and FRONTEND_DIST.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
