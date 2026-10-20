"""只在新 OpenSSL 的两个搜索 API 上兼容握手，保留工厂路由和 HTTPX 信任边界。"""
import ssl
from pathlib import Path

import certifi
import httpx
import pytest

from src.collectors import _net as module


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr(ssl, "OPENSSL_VERSION_INFO", (3, 5, 0, 0, 0))
    monkeypatch.setattr(module, "get_http_proxy", lambda: None)
    monkeypatch.setattr(httpx._client, "get_environment_proxies", lambda: {})
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    clients, curves, trust_sources = [], [], []
    original_init = httpx.AsyncClient.__init__
    original_curve = ssl.SSLContext.set_ecdh_curve
    original_context = ssl.create_default_context

    def init(self, *args, **kwargs):
        clients.append(dict(kwargs))
        original_init(self, *args, **kwargs)

    def curve(self, value):
        curves.append(value)
        return original_curve(self, value)

    def context(*args, **kwargs):
        trust_sources.append(dict(kwargs))
        return original_context(*args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    monkeypatch.setattr(ssl.SSLContext, "set_ecdh_curve", curve)
    monkeypatch.setattr(ssl, "create_default_context", context)
    return clients, curves, trust_sources


@pytest.mark.asyncio
@pytest.mark.parametrize("host,trust_env", [("api.anysearch.com", False), ("api.tavily.com", True)])
async def test_exact_search_api_builds_real_verified_context_and_keeps_default_ca(factory, host, trust_env):
    clients, curves, trust_sources = factory
    async with module.smart_client("https://" + host + "/search", timeout=7.0) as client:
        assert curves == ["X25519"]
        context = clients[0]["verify"]
        assert isinstance(context, ssl.SSLContext)
        assert client._transport._pool._ssl_context is context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        assert context.maximum_version in (ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.MAXIMUM_SUPPORTED)
        assert context.get_ca_certs(binary_form=True)
        assert trust_sources == [{"cafile": certifi.where()}]
        assert clients[0]["trust_env"] is trust_env
        assert clients[0]["follow_redirects"] is True and clients[0]["timeout"] == 7.0


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [(3, 0, 2, 0, 0), (3, 0, 16, 0, 0), (3, 4, 9, 0, 0)])
async def test_older_openssl_keeps_existing_factory_without_calling_curve_api(factory, monkeypatch, version):
    clients, curves, _ = factory
    monkeypatch.setattr(ssl, "OPENSSL_VERSION_INFO", version)
    async with module.smart_client("https://api.anysearch.com/search"):
        assert curves == [] and "verify" not in clients[0]
        assert clients[0]["trust_env"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://api.anysearch.com/search", "https://anysearch.com/search", "https://www.api.anysearch.com/search",
    "https://api.anysearch.com.evil.example/search", "https://api.anysearch.com@evil.example/search",
    "https://tavily.com/search", "https://api.tavily.com.evil.example/search",
    "https://api.deepseek.com/v1", "https://www.jd.com", "http://localhost:8088",
])
async def test_other_hosts_schemes_and_lookalikes_keep_existing_tls(factory, url):
    clients, curves, _ = factory
    async with module.smart_client(url):
        assert curves == [] and "verify" not in clients[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["context", "true", "false"])
async def test_explicit_verify_is_passed_through_without_compatibility_override(factory, kind):
    clients, curves, _ = factory
    verify = httpx.create_ssl_context(verify=True, trust_env=False) if kind == "context" else kind == "true"
    async with module.smart_client("https://api.tavily.com/search", verify=verify):
        assert curves == [] and clients[0]["verify"] is verify


@pytest.mark.asyncio
@pytest.mark.parametrize("host,proxy", [("api.anysearch.com", None), ("api.tavily.com", "http://proxy.example:8080")])
async def test_direct_or_configured_proxy_keeps_ca_env_disabled(factory, monkeypatch, tmp_path, host, proxy):
    clients, curves, trust_sources = factory
    monkeypatch.setattr(module, "get_http_proxy", lambda: proxy)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing-ca-dir"))
    async with module.smart_client("https://" + host + "/search"):
        assert curves == ["X25519"] and clients[0]["trust_env"] is False
        assert trust_sources == [{"cafile": certifi.where()}]
        assert clients[0].get("proxy") == proxy


@pytest.mark.asyncio
@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
async def test_overseas_without_configured_proxy_keeps_original_ca_env(factory, monkeypatch, tmp_path, variable):
    clients, curves, trust_sources = factory
    if variable == "SSL_CERT_FILE":
        target = tmp_path / "trusted.pem"
        target.write_bytes(Path(certifi.where()).read_bytes())
        key = "cafile"
    else:
        target = tmp_path / "ca-dir"
        target.mkdir()
        key = "capath"
    monkeypatch.setenv(variable, str(target))
    async with module.smart_client("https://api.tavily.com/search") as client:
        assert curves == ["X25519"] and clients[0]["trust_env"] is True
        assert trust_sources == [{key: str(target)}]
        assert client._transport._pool._ssl_context.check_hostname is True


@pytest.mark.asyncio
async def test_invalid_trusted_ca_env_fails_instead_of_using_another_store(factory, monkeypatch, tmp_path):
    clients, _, _ = factory
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca.pem"))
    client = None
    try:
        with pytest.raises(FileNotFoundError):
            client = module.smart_client("https://api.tavily.com/search")
    finally:
        if client is not None:
            await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["context", "curve"])
async def test_compatibility_construction_failure_never_returns_unverified_client(factory, monkeypatch, stage):
    clients, _, _ = factory
    def fail(*args, **kwargs):
        raise ssl.SSLError("合成兼容上下文失败")
    if stage == "context":
        monkeypatch.setattr(httpx, "create_ssl_context", fail)
    else:
        monkeypatch.setattr(ssl.SSLContext, "set_ecdh_curve", fail)
    client = None
    try:
        with pytest.raises(ssl.SSLError):
            client = module.smart_client("https://api.anysearch.com/search")
        assert clients == []
    finally:
        if client is not None:
            await client.aclose()
