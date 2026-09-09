import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.capability_catalog.models import CatalogActor
from src.capability_catalog.reuse import ToolNeed, match_installed_tools
from src.semantic_harness.capabilities import TABLE_DUCKDB_MANIFEST


def need():
    return ToolNeed(purpose="本地筛选资料", operations=("filter",), input_formats=("csv",), output_formats=("parquet",))


def pack(**changes):
    values = dict(pack_id="table.duckdb", version="1.0.0", digest="sha256:" + "a" * 64,
                  owner_id="a", scope="personal", source_provenance=("https://github.com/duckdb/duckdb",),
                  manifest=(("reuse_contract", TABLE_DUCKDB_MANIFEST.model_dump_json()), ("license", "MIT")))
    values.update(changes)
    return SimpleNamespace(**values)


def match(*packs):
    return match_installed_tools(SimpleNamespace(list_visible_packs=lambda actor: packs), CatalogActor(owner_id="a", role="user"), need())


def test_matching_preserves_exact_identity_without_claiming_authorization_or_health():
    result = match(pack(), pack(owner_id="other", pack_id="secret-other"))
    assert len(result["matches"]) == 1
    assert result["matches"][0]["ref"]["digest"] == "sha256:" + "a" * 64
    assert result["matches"][0]["authorization"] == "not_checked"
    assert result["matches"][0]["health"] == "not_checked"
    assert "secret-other" not in json.dumps(result)

@pytest.mark.parametrize("change", [dict(version="2"), dict(manifest=()), dict(source_provenance=("https://example.test/?token=secret",))])
def test_invalid_metadata_is_gap_not_match(change):
    result = match(pack(**change))
    assert not result["matches"] and result["gaps"]
    assert "token=secret" not in json.dumps(result)


@pytest.mark.parametrize("label", ["/home/file", "C:\\private", "sk-secret", "api_key", "read source"])
def test_need_rejects_non_generic_export_labels(label):
    with pytest.raises(ValidationError):
        need().model_validate(dict(purpose="仅本地", operations=[label], input_formats=["csv"], output_formats=["json"]))


@pytest.mark.asyncio
async def test_discovery_is_explicit_bounded_and_never_sends_purpose():
    import httpx
    from src.capability_catalog.reuse import discover_open_source_candidates
    from src.connectors.http_security import HttpSecurityGuard
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json={"items": [dict(full_name=f"org/repo{i}", license={"spdx_id": "MIT"}) for i in range(5)]})
        return httpx.Response(200, json={"tag_name": "v1.2.3", "assets": []})
    transport = httpx.MockTransport(handler)
    guard = HttpSecurityGuard(resolver=lambda _: ["93.184.216.34"])
    blocked = await discover_open_source_candidates(need(), transport=transport, guard=guard)
    assert blocked["status"] == "not_requested" and not calls
    result = await discover_open_source_candidates(need(), allow_discovery=True, transport=transport, guard=guard)
    assert len(calls) == 4 and len(result["candidates"]) == 3
    assert all(item["status"] == "needs_confirmation" and "digest_unverified" in item["gaps"] for item in result["candidates"])
    assert all(request.url.host == "93.184.216.34" and request.headers["host"] == "api.github.com" for request in calls)
    assert all("authorization" not in request.headers and "本地" not in str(request.url) for request in calls)

@pytest.mark.parametrize("field,value", [("license", "token secret-value"), ("reuse_contract", "bad-json")])
def test_bad_declarations_never_leak_into_match(field, value):
    p = pack()
    metadata = dict(p.manifest)
    metadata[field] = value
    assert not match(pack(manifest=tuple(metadata.items())))["matches"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "oversized", "cancel", "unsafe_repo"])
async def test_discovery_refuses_redirect_size_and_propagates_cancel(mode):
    import asyncio
    import httpx
    from src.capability_catalog.reuse import discover_open_source_candidates
    from src.connectors.http_security import HttpSecurityGuard
    calls = []
    def handler(request):
        calls.append(request)
        if mode == "cancel":
            raise asyncio.CancelledError()
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://outside.test/"})
        if mode == "oversized":
            return httpx.Response(200, content=b"x" * 262145)
        return httpx.Response(200, json={"items": [{"full_name": "../evil"}]})
    args = dict(allow_discovery=True, transport=httpx.MockTransport(handler), guard=HttpSecurityGuard(resolver=lambda _: ["93.184.216.34"]))
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await discover_open_source_candidates(need(), **args)
    else:
        result = await discover_open_source_candidates(need(), **args)
        assert result["status"] == "discovery_failed"
    assert len(calls) == 1
