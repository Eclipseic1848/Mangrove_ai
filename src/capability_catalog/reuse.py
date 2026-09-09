"""精确能力兼容匹配；治理许可与本次健康由调用方现有门判定。"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.semantic_harness.models import CapabilityManifest


class ToolNeed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    purpose: str = Field(min_length=1, max_length=500)
    operations: tuple[str, ...] = Field(min_length=1, max_length=10)
    input_formats: tuple[str, ...] = Field(min_length=1, max_length=10)
    output_formats: tuple[str, ...] = Field(min_length=1, max_length=10)

    @field_validator("operations", "input_formats", "output_formats")
    @classmethod
    def labels(cls, values):
        # 公开检索只允许通用短标签，不接受路径、正文或凭证。
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value) or re.search(r"secret|password|credential|token|api_key|^sk-", value) for value in values):
            raise ValueError("工具需求须为无秘密与路径的通用标签")
        return tuple(dict.fromkeys(values))


def _public_source(value):
    if not isinstance(value, str) or len(value) > 500:
        return False
    try:
        parts = urlsplit(value)
        return parts.scheme == "https" and bool(parts.hostname) and not (parts.username or parts.password or parts.query or parts.fragment) and not any(c.isspace() for c in value)
    except ValueError:
        return False


def validated_reuse_contract(pack) -> CapabilityManifest:
    metadata = dict(pack.manifest)
    contract = CapabilityManifest.model_validate_json(metadata.get("reuse_contract", ""))
    license_name = metadata.get("license", "")
    if len(pack.manifest) != len(metadata) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+() -]{0,119}", license_name) or re.search(r"secret|password|credential|token|api.key|sk-", license_name, re.I) or license_name.lower() in {"unknown", "none", "noassertion"}:
        raise ValueError("missing_license_or_ambiguous_metadata")
    if not pack.source_provenance or len(pack.source_provenance) > 10 or not all(_public_source(url) for url in pack.source_provenance):
        raise ValueError("missing_public_source")
    if contract.capability_id != pack.pack_id or contract.version != pack.version:
        raise ValueError("contract_identity_mismatch")
    if contract.side_effect != "read_only" or contract.network != "none":
        raise ValueError("requires_read_only_offline_contract")
    return contract


def validate_reuse_call(pack, runtime_manifest, arguments, tool) -> None:
    """手选与自动选择共用业务合同；不允许 Schema 校验触发外部取数。"""
    import json
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError, ValidationError

    contract = validated_reuse_contract(pack)
    if runtime_manifest.version != contract.version:
        raise ValueError("能力运行版本与业务合同不一致")
    schema = contract.parameters_schema
    if schema.get("type") != ("array" if runtime_manifest.kind in {"python", "node", "cli"} else "object"):
        raise ValueError("能力必须声明实际调用参数 Schema")
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            # 当前仅接受包内直接声明，禁止远程解析及递归引用造成外发或失控。
            if "$ref" in value or "$dynamicRef" in value:
                raise ValueError("调用参数 Schema 不允许引用")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    if runtime_manifest.kind == "mcp_local":
        allowed = json.loads(dict(pack.manifest).get("reuse_tools", "[]"))
        if not isinstance(allowed, list) or not allowed or any(not isinstance(name, str) for name in allowed) or tool not in allowed:
            raise ValueError("MCP 工具不在已批准的只读业务合同中")
    elif runtime_manifest.kind not in {"python", "node", "cli"} or tool is not None:
        raise ValueError("能力类型或工具参数不匹配")
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(arguments)
    except (SchemaError, ValidationError, RecursionError) as error:
        # 错误不得回显业务参数正文。
        raise ValueError("工具参数不符合已批准的业务合同") from error


def match_installed_tools(catalog, actor, need: ToolNeed) -> dict:
    matches, gaps = [], []
    for pack in catalog.list_visible_packs(actor):
        if str(pack.scope) != "platform" and pack.owner_id != actor.owner_id:
            continue
        ref = dict(pack_id=pack.pack_id, version=pack.version, digest=pack.digest)
        code = None
        try:
            metadata = dict(pack.manifest)
            contract = validated_reuse_contract(pack)
            license_name = metadata.get("license", "")
            if not (set(need.operations) <= set(contract.operations) and set(need.input_formats) <= set(contract.accepts) and set(need.output_formats) <= set(contract.produces)):
                code = "incompatible_contract"
        except (ValueError, TypeError):
            code = "missing_or_invalid_reuse_contract"
        if code:
            gaps.append(dict(ref=ref, code=code, remediation="补充并验证精确版本的兼容、许可证及公共来源声明；不自动换版"))
            continue
        matches.append(dict(ref=ref, license=license_name, source_provenance=list(pack.source_provenance),
                            compatibility=dict(operations=list(need.operations), input_formats=list(need.input_formats), output_formats=list(need.output_formats)),
                            authorization="not_checked", health="not_checked"))
    return dict(matches=matches, gaps=gaps)

async def discover_open_source_candidates(need: ToolNeed, *, allow_discovery: bool = False, transport=None, guard=None) -> dict:
    """仅由上层无获准匹配且明确同意后调用；不下载制品或自动安装。"""
    import asyncio
    import json
    import httpx
    from src.connectors.http_security import HttpSecurityGuard
    from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport

    if not allow_discovery:
        return dict(status="not_requested", candidates=[], gaps=[])
    candidates = []
    try:
        async with asyncio.timeout(15):
            target = await asyncio.to_thread((guard or HttpSecurityGuard()).validate, "https://api.github.com")
            pinned = PinnedAsyncHTTPTransport(target=target, transport=transport)
            async with httpx.AsyncClient(transport=pinned, trust_env=False, follow_redirects=False, timeout=5,
                                         headers={"Accept": "application/vnd.github+json", "User-Agent": "Mangrove-tool-discovery"}) as client:
                async def get(path, params=None):
                    async with client.stream("GET", "https://api.github.com" + path, params=params) as response:
                        if response.status_code == 404:
                            return {}
                        if response.status_code != 200:
                            raise ValueError("discovery_http_failure")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 256 * 1024:
                                raise ValueError("discovery_response_limit")
                        value = json.loads(data)
                        if not isinstance(value, dict):
                            raise ValueError("discovery_invalid_response")
                        return value
                # purpose严格留在本地；固定端点只接收已验证的通用标签。
                query = " ".join((*need.operations, *need.input_formats, *need.output_formats))
                result = await get("/search/repositories", {"q": query, "per_page": 3})
                items = result.get("items", [])
                if not isinstance(items, list):
                    raise ValueError("discovery_invalid_response")
                for item in items[:3]:
                    name = item.get("full_name") if isinstance(item, dict) else None
                    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", name) or any(part in {".", ".."} for part in name.split("/")):
                        raise ValueError("discovery_invalid_repository")
                    release = await get(f"/repos/{name}/releases/latest")
                    version = release.get("tag_name")
                    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}", version) or version.lower() in {"latest", "main", "master"}:
                        version = None
                    metadata = item.get("license")
                    license_name = metadata.get("spdx_id") if isinstance(metadata, dict) else None
                    if not isinstance(license_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,79}", license_name) or license_name.upper() == "NOASSERTION":
                        license_name = None
                    # Release标签不是不可变制品摘要；候选永不冒称已完成验证。
                    gaps = ["digest_unverified", "compatibility_unverified", "installation_requires_confirmation"]
                    if not version:
                        gaps.append("exact_version_missing")
                    if not license_name:
                        gaps.append("license_missing")
                    candidates.append(dict(source_uri=f"https://github.com/{name}", version=version,
                                           license=license_name, digest=None, status="needs_confirmation", gaps=gaps))
        return dict(status="needs_confirmation" if candidates else "no_candidates", candidates=candidates, gaps=[])
    except (ValueError, OSError, httpx.HTTPError, TimeoutError):
        # 不公开服务响应/异常文本；取消不捕获，交由宿主继续收口。
        return dict(status="discovery_failed", candidates=candidates, gaps=["public_discovery_unavailable"])
