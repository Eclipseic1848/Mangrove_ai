"""同源产品入口安全头；不缓存正文、不改变SSE/附件响应流。"""
import base64
import hashlib
import re

from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


# 仅允许已核验的防主题闪烁脚本；前端改动该脚本时须同时更新永久校验。
THEME_SCRIPT_HASH = "sha256-xEFnc2Y703AJ5K0ClIaBSm3ri5UwnKZMAN8a1wZjIvc="
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    f"script-src 'self' '{THEME_SCRIPT_HASH}'",
    "script-src-attr 'none'",
    # React布局、PDF文本层与Canvas缩放真实使用内联样式；不因此开放脚本。
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self' data:",
    "connect-src 'self' blob:",
    "worker-src 'self'",
    "frame-src 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
))


def content_security_policy(scope: Scope) -> str:
    root = scope.get("root_path", "").rstrip("/")
    path = scope.get("path", "")
    if root and path.startswith(root + "/"):
        path = path[len(root):]
    if path not in {"/docs", "/docs/oauth2-redirect", "/redoc"}:
        return CONTENT_SECURITY_POLICY
    # 文档沿用FastAPI既有资源，仅这三个固定入口放行；不从用户HTML收集脚本。
    scripts = ["'self'"]
    policy = CONTENT_SECURITY_POLICY
    if path == "/redoc":
        scripts.append("https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js")
        policy = policy.replace("style-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com")
        policy = policy.replace("font-src 'self' data:", "font-src 'self' data: https://fonts.gstatic.com")
    else:
        html = get_swagger_ui_oauth2_redirect_html() if path.endswith("oauth2-redirect") else get_swagger_ui_html(openapi_url=f"{root}/openapi.json", title="", oauth2_redirect_url=f"{root}/docs/oauth2-redirect")
        for script in re.findall(r"<script>([\s\S]*?)</script>", html.body.decode("utf-8")):
            scripts.append("'sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii") + "'")
        if path == "/docs":
            scripts.append("https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js")
            policy = policy.replace("style-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css")
    policy = policy.replace("img-src 'self' blob: data:", "img-src 'self' blob: data: https://fastapi.tiangolo.com/img/favicon.png")
    return policy.replace(f"script-src 'self' '{THEME_SCRIPT_HASH}'", "script-src " + " ".join(scripts))


def browser_security_headers(scope: Scope) -> dict[str, str]:
    headers = {
        "Content-Security-Policy": content_security_policy(scope),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
    }
    # 只信ASGI层已判定的scheme；代理头是否可信由既有服务器代理白名单负责。
    if scope.get("scheme") == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


class BrowserSecurityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(browser_security_headers(scope))
            await send(message)
        await self.app(scope, receive, secure_send)


async def browser_security_error(request: Request, _exception: Exception) -> PlainTextResponse:
    # Starlette的500在用户中间件外生成；维持原默认正文并补相同安全头，不泄露异常。
    return PlainTextResponse("Internal Server Error", status_code=500, headers=browser_security_headers(request.scope))
