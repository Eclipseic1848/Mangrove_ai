"""维护期间只暂停新写入；已有任务 Relay 仍执行原鉴权。"""
from __future__ import annotations

import re

from starlette.responses import JSONResponse

from src.services.deployment_drain import get_deployment_gate


_CONTINUATIONS = re.compile(
    r"(?:/internal/model-relay/.+|/internal/document-tools/[^/]+"
    r"|/api/auth/(?:login|refresh|logout|logout-all)"
    r"|/api/chat/[^/]+/cancel"
    r"|/api/semantic-workspace/tasks/[^/]+/cancel"
    r"|/api/semantic-workspace/deletion-operations/[^/]+/resume"
    r"|/api/semantic-workspace/source-acquisitions/[^/]+/cancel"
    r"|/api/capability-governance/validations/[^/]+/cancel"
    r"|/api/admin/users/[^/]+/execution-hold/retry)"
)


class DeploymentAdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        reading = scope["method"] in {"GET", "HEAD"}
        if reading and (not scope["path"].startswith("/api/") or scope["path"] in {"/api/health", "/api/readiness"}):
            await self.app(scope, receive, send)
            return
        # API 读取也可能生成下载或写审计/限流记录；封口时须一起暂停。
        continuation = reading or (scope["method"] == "POST" and _CONTINUATIONS.fullmatch(scope["path"]) is not None)
        with get_deployment_gate().admission("http_read" if reading else "http", continuation=continuation) as admitted:
            if not admitted:
                await JSONResponse(
                    {"detail": "服务正在更新，请稍后重试；已有任务继续处理"},
                    status_code=503, headers={"Retry-After": "5", "Cache-Control": "no-store"},
                )(scope, receive, send)
                return
            scope.setdefault("state", {})["deployment_admitted"] = True
            # 覆盖流式响应与 BackgroundTask；收到响应头不代表上游已关闭。
            await self.app(scope, receive, send)
