"""
FastAPI 主应用：装配路由 + CORS + 启动调度器 + 托管前端构建产物。

启动（端口固定为 settings.api_port=8088）：
    开发热更新：在项目根目录执行 python scripts/dev_reload.py
    普通单进程：python -m src.api.main
前端开发时另起 Vite（frontend/，默认 5173），通过 CORS 调用本网关。
生产：先 `npm run build` 生成 frontend/dist，再由本应用同源托管。

注意：不使用 uvicorn 自带 reload=True——Windows 下 reload/多进程会强制用 SelectorEventLoop
（uvicorn 的 use_subprocess 分支），而 SelectorEventLoop 不支持
asyncio.create_subprocess_exec，会导致 MediaCrawler 社媒采集/Cookie 验证等一切依赖
子进程的功能必现 NotImplementedError。开发环境改用 scripts/dev_reload.py 在进程外
监听源码和 .env 后重启普通单进程入口，既支持热更新又保留 Proactor 子进程能力。
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 确保可导入项目 src。
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings  # noqa: E402
from src.api.services import start_scheduler  # noqa: E402
from src.api.cookie_health_scanner import start_cookie_health_scanner  # noqa: E402
from src.api.library_dedup_scanner import start_library_dedup_scanner  # noqa: E402
from src.api.routes import (  # noqa: E402
    admin_routes, auth_routes, capability_governance, chat, config_routes, confirm, conversations, data_sources, data_tasks, downloads,
    feedback_routes, lessons_routes, library_dedup_routes, memory_routes, model_connections, model_relay, models, overview,
    semantic_bindings, semantic_deliveries, semantic_documents, semantic_executions, semantic_harness, semantic_plans,
    semantic_workspace, source_acquisition, document_tools,
    settings_routes, tasks, templates_routes,
)

_FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 先套用管理员在前端保存的全局运行时配置（.env 为兜底基线），再拉起调度器
    from src.api.auth import get_store
    from src.api.semantic_workspace_runtime import (
        get_semantic_workspace_manager,
    )
    from src.config.runtime_config import apply_global_overrides
    from src.observability.workspace_telemetry import (
        configure_workspace_telemetry,
        shutdown_workspace_telemetry,
    )
    apply_global_overrides(get_store())
    if settings.workspace_telemetry_enabled:
        configure_workspace_telemetry(
            endpoint=settings.workspace_otlp_endpoint,
        )
    start_scheduler()  # 启用时拉起定时任务后台轮询
    start_cookie_health_scanner()  # Cookie 健康巡检：循环常驻，开关关闭时内部自己空转
    start_library_dedup_scanner()  # 模板库/教训库定时巡检：循环常驻，开关关闭时内部自己空转
    workspace_manager = get_semantic_workspace_manager()
    workspace_manager.start()
    from src.api.capability_governance_runtime import (
        get_capability_validation_manager,
        get_platform_validation_manager,
    )
    capability_validation_manager = get_capability_validation_manager()
    capability_validation_manager.start()
    platform_validation_manager = get_platform_validation_manager()
    platform_validation_manager.start()
    try:
        yield
    finally:
        await capability_validation_manager.stop()
        await platform_validation_manager.stop()
        await workspace_manager.stop()
        shutdown_workspace_telemetry()


app = FastAPI(title="Mangrove Web UI Gateway", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in (settings.webui_cors_origins or "").split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth_routes, conversations, chat, confirm, tasks, models, downloads,
          memory_routes, overview, templates_routes, lessons_routes, library_dedup_routes,
          model_connections, model_relay,
          capability_governance,
          settings_routes, admin_routes, config_routes, feedback_routes, data_sources, data_tasks,
          semantic_plans, semantic_bindings, semantic_executions, semantic_documents,
          semantic_harness, semantic_deliveries, semantic_workspace,
          source_acquisition,
          document_tools):
    app.include_router(r.router)
app.include_router(capability_governance.admin_router)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "mangrove-webui"}


@app.get("/api/readiness")
def readiness():
    from src.api.auth import get_store
    from src.api.readiness import collect_workspace_readiness
    from src.api.semantic_workspace_runtime import get_semantic_workspace_manager

    report = collect_workspace_readiness(
        store=get_store(),
        manager=get_semantic_workspace_manager(),
        upload_root=Path(settings.data_prep_upload_root),
        execution_root=Path(settings.semantic_execution_root),
        artifact_root=Path(settings.data_prep_artifact_root),
    )
    return JSONResponse(
        status_code=200 if report.ready else 503,
        content=asdict(report),
    )


# ---------- 前端静态托管（构建后才有 dist；开发期用 Vite，不影响）----------
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # SPA 回退：非 /api 路径一律交给 index.html 由前端路由处理
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": "API 接口不存在"},
            )
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    # 不使用 uvicorn 自带 reload：Windows 下它会选用 SelectorEventLoop，导致依赖
    # create_subprocess_exec 的采集器报 NotImplementedError。开发热更新由 scripts/dev_reload.py
    # 在进程外监听文件并重启本单进程入口，从而保留 Proactor 子进程能力。
    uvicorn.run(
        "src.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
    )
