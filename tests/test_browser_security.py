"""真实网关响应头回归；不启动lifespan或访问运行库。"""
import base64
import hashlib
from pathlib import Path
import re
import sys
import types

import pytest
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gateway(tmp_path):
    module = types.ModuleType("candidate_security_main")
    module.__file__ = str(ROOT / "src/api/main.py")
    sys.modules[module.__name__] = module
    exec(compile((ROOT / "src/api/main.py").read_text(encoding="utf-8"), module.__file__, "exec"), module.__dict__)

    # 干净CI不依赖被忽略的dist，也不构建或覆盖真实前端。
    (tmp_path / "index.html").write_text((ROOT / "frontend/index.html").read_text(encoding="utf-8"), encoding="utf-8")
    module._FRONTEND_DIST = tmp_path

    def forbidden():
        raise HTTPException(403, "合成拒绝")

    def broken():
        raise RuntimeError("不可公开的合成内部错误")

    def stream():
        return StreamingResponse(iter([b"event: ready\ndata: synthetic\n\n"]), media_type="text/event-stream")

    def download():
        return Response(b"synthetic-file", media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="synthetic.bin"'})

    for name, endpoint in [("forbidden", forbidden), ("broken", broken), ("events", stream), ("download", download)]:
        module.app.router.routes.insert(0, APIRoute(f"/__security/{name}", endpoint))
    return lambda: module.app


def assert_security(response):
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("x-frame-options") == "DENY"
    policy = response.headers.get("content-security-policy", "")
    for directive in ["default-src 'self'", "object-src 'none'", "frame-src 'none'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'", "script-src-attr 'none'"]:
        assert directive in policy
    script = next(part for part in policy.split(";") if part.strip().startswith("script-src "))
    assert "'unsafe-inline'" not in script and "'unsafe-eval'" not in script and "https:" not in script


@pytest.mark.parametrize("path,status", [("/api/health", 200), ("/data-prep", 200), ("/api/missing-security-endpoint", 404), ("/__security/forbidden", 403), ("/__security/broken", 500)])
def test_gateway_document_api_and_error_headers(gateway, path, status):
    response = TestClient(gateway(), raise_server_exceptions=False).get(path)
    assert response.status_code == status
    assert_security(response)
    if status == 500:
        assert response.text == "Internal Server Error"


def test_download_and_sse_preserve_body_and_existing_headers(gateway):
    client = TestClient(gateway())
    download = client.get("/__security/download")
    assert_security(download)
    assert download.content == b"synthetic-file"
    assert download.headers["content-disposition"] == 'attachment; filename="synthetic.bin"'
    stream = client.get("/__security/events")
    assert_security(stream)
    assert stream.content == b"event: ready\ndata: synthetic\n\n"
    assert stream.headers["content-type"].startswith("text/event-stream")


def test_only_frozen_theme_inline_script_is_allowed(gateway):
    response = TestClient(gateway()).get("/data-prep")
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode("ascii")
    assert f"'sha256-{digest}'" in response.headers.get("content-security-policy", "")


def test_http_cannot_spoof_hsts_with_forwarded_header(gateway):
    app = ProxyHeadersMiddleware(gateway(), trusted_hosts=["127.0.0.1"])
    response = TestClient(app, client=("203.0.113.7", 12345)).get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert_security(response)
    assert "strict-transport-security" not in response.headers


@pytest.mark.parametrize("trusted_proxy", [False, True])
def test_https_or_existing_trusted_proxy_sets_hsts(gateway, trusted_proxy):
    app = gateway()
    if trusted_proxy:
        app = ProxyHeadersMiddleware(app, trusted_hosts=["127.0.0.1"])
    client = TestClient(app, base_url="http://testserver" if trusted_proxy else "https://testserver", client=("127.0.0.1", 12345))
    response = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert response.headers.get("strict-transport-security") == "max-age=31536000"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/docs/oauth2-redirect"])
def test_existing_documentation_only_allows_its_exact_scripts(gateway, path):
    response = TestClient(gateway()).get(path)
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    for script in re.findall(r'<script[^>]*src="([^"]+)"[^>]*>', response.text):
        assert script in policy
    for inline in re.findall(r"<script>([\s\S]*?)</script>", response.text):
        digest = base64.b64encode(hashlib.sha256(inline.encode("utf-8")).digest()).decode("ascii")
        assert f"'sha256-{digest}'" in policy
    assert "'unsafe-inline'" not in next(part for part in policy.split(";") if part.strip().startswith("script-src "))


def test_cors_preflight_keeps_security_headers(gateway):
    response = TestClient(gateway()).options("/api/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert response.status_code in {200, 400}
    assert_security(response)


def test_documentation_root_path_hash_and_policy_stay_scoped(gateway):
    client = TestClient(gateway(), root_path="/mounted")
    response = client.get("/docs")
    for inline in re.findall(r"<script>([\s\S]*?)</script>", response.text):
        digest = base64.b64encode(hashlib.sha256(inline.encode("utf-8")).digest()).decode("ascii")
        assert f"'sha256-{digest}'" in response.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in client.get("/data-prep").headers["content-security-policy"]
