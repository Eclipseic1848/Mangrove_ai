"""模型连接深 Module：隐藏 Endpoint、凭证和验证协议差异。"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import uuid
from urllib.parse import urlsplit

import httpx

from src.config.settings import settings
from src.connectors.http_security import HostResolver, HttpSecurityGuard, SsrfError

from .catalog import PRESETS_BY_ID, ProviderPreset, public_presets
from .contracts import AccessGrant, ConnectionBinding, RelayResponse
from .pinned_transport import PinnedAsyncHTTPTransport
from .storage import ModelConnectionRepository
from .vault import FernetCredentialVault, VaultDecryptionError


_OFFICIAL_PRESET_HTTPS_HOSTS = tuple(sorted({
    str(urlsplit(preset.base_url).hostname or "").lower()
    for preset in PRESETS_BY_ID.values()
    if urlsplit(preset.base_url).scheme.lower() == "https"
}))


class ConnectionError(ValueError):
    """模型连接输入或验证失败。"""


class ConnectionValidationError(ConnectionError):
    """所有候选模型均失败；携带可安全返回的脱敏逐模型结果。"""

    def __init__(
        self,
        message: str,
        model_results: list[dict[str, object]],
    ) -> None:
        super().__init__(message)
        self.model_results = model_results


class ProviderVerificationError(ConnectionError):
    """单模型验证失败及其稳定产品分类。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GrantError(ConnectionError):
    """Grant 无效、越权、过期或协议不匹配。"""


class ProviderOutcomeUnknownError(ConnectionError):
    """请求可能已到 Provider，但无法确认是否形成可用响应。"""


class ConnectionBroker:
    """产品代码使用模型连接的唯一 Interface。"""

    def __init__(
        self,
        *,
        repository: ModelConnectionRepository,
        vault: FernetCredentialVault,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: HostResolver | None = None,
        clock: Callable[[], datetime] | None = None,
        grant_ttl_seconds: int = 900,
        provider_timeout_seconds: float = 120.0,
    ) -> None:
        self._repository = repository
        self._vault = vault
        self._transport = transport
        self._resolver = resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._grant_ttl_seconds = grant_ttl_seconds
        self._provider_timeout_seconds = provider_timeout_seconds

    def presets(self) -> list[dict[str, object]]:
        return public_presets()

    def list_connections(
        self,
        owner_user_id: str,
        *,
        can_manage: bool = False,
    ) -> list[dict[str, object]]:
        return self._repository.list_available(
            owner_user_id,
            expose_managed_key_hint=can_manage,
        )

    def get_usage_preference(self, owner_user_id: str) -> dict[str, object] | None:
        return self._repository.get_usage_preference(owner_user_id)

    def set_usage_preference(
        self,
        owner_user_id: str,
        connection_id: str,
        model_id: str,
    ) -> dict[str, object]:
        try:
            return self._repository.set_usage_preference(
                owner_user_id,
                connection_id,
                model_id,
            )
        except ValueError as exc:
            raise ConnectionError(str(exc)) from exc

    def import_legacy_connection(
        self,
        *,
        source_scope: str,
        source_key: str,
        owner_user_id: str | None,
        created_by: str,
        display_name: str,
        base_url: str,
        api_format: str,
        model: str,
        api_key: str,
        preset_id: str | None = None,
        locality: str = "public_external",
    ) -> dict[str, object]:
        """复制旧配置到隔离密文；不验证、不删除旧值、不改变旧路由。"""

        preset = PRESETS_BY_ID.get(preset_id or "")
        fingerprint = hashlib.sha256(
            "\x00".join(
                [source_scope, source_key, base_url, api_format, model, api_key]
            ).encode("utf-8")
        ).hexdigest()
        model_results = None
        if preset is not None:
            model_results = [
                {
                    "model_id": item.model_id,
                    "display_name": item.display_name,
                    "catalog_role": item.role,
                    "catalog_version": preset.version,
                    "status": "pending_validation",
                    "enabled": False,
                    "verified_at": None,
                    "error_code": None,
                    "usage_status": "unknown",
                }
                for item in preset.model_catalog
            ]
        return self._repository.create_imported(
            source_scope=source_scope,
            source_key=source_key,
            source_fingerprint=fingerprint,
            owner_scope="user_personal" if owner_user_id else "platform_shared",
            owner_user_id=owner_user_id,
            created_by=created_by,
            display_name=display_name,
            base_url=base_url.rstrip("/"),
            model=model,
            api_format=api_format,
            locality=locality,
            ciphertext=self._vault.encrypt(api_key) if api_key else None,
            key_hint=api_key[-4:] if api_key else "",
            preset_id=preset_id,
            preset_version=preset.version if preset else "legacy_imported",
            model_results=model_results,
        )

    def get_connection(
        self,
        owner_user_id: str,
        connection_id: str,
    ) -> dict[str, object] | None:
        """读取 Owner 可用的连接摘要，不暴露 Endpoint 或密文。"""

        return next(
            (
                item
                for item in self.list_connections(owner_user_id)
                if item["connection_id"] == connection_id
            ),
            None,
        )

    def delete_connection(
        self,
        connection_id: str,
        actor_user_id: str,
        *,
        can_manage: bool,
    ) -> bool:
        """按 Owner/管理权限删除连接并清除对应在线密文。"""

        return self._repository.delete_authorized(
            connection_id,
            actor_user_id,
            can_manage=can_manage,
        )

    async def retry_models(
        self,
        *,
        owner_user_id: str,
        connection_id: str,
        model_ids: list[str],
        can_manage: bool = False,
    ) -> dict[str, object]:
        """只重验当前连接中失败的模型，复用其 Secret 且不触碰其他模型。"""

        context = self._repository.get_model_context(
            connection_id,
            owner_user_id,
            can_manage=can_manage,
        )
        if context is None:
            raise ConnectionError("模型连接不存在")
        preset = PRESETS_BY_ID.get(str(context["preset_id"]))
        requested = list(dict.fromkeys(item.strip() for item in model_ids if item.strip()))
        if not requested:
            raise ConnectionError("至少选择一个待重验模型")
        states = {
            str(item["model_id"]): str(item["status"])
            for item in context["models"]
        }
        for model_id in requested:
            if model_id not in states or (preset is not None and model_id not in preset.models):
                raise ConnectionError("待重验模型不属于当前连接")
            if states[model_id] in {"available", "disabled"}:
                raise ConnectionError("只允许重试验证失败的模型")
        ciphertext = context.get("ciphertext")
        secret = self._vault.decrypt(str(ciphertext)) if ciphertext else ""
        if preset is not None:
            results = await self._verify_preset_models(
                preset=preset,
                api_key=secret,
                model_ids=requested,
            )
        else:
            results = []
            for model_id in requested:
                try:
                    usage = await self._verify_custom_model(
                        base_url=str(context["base_url"]),
                        api_format=str(context["api_format"]),
                        model=model_id,
                        api_key=secret,
                        allow_private=str(context.get("locality")) == "managed_private",
                    )
                    state = "available"
                    error = None
                except ProviderVerificationError as exc:
                    usage = {}
                    state = exc.code
                    error = exc.code
                results.append({
                    "model_id": model_id,
                    "status": state,
                    "enabled": state == "available",
                    "verified_at": datetime.now().isoformat(timespec="seconds"),
                    "error_code": error,
                    "usage_status": "reported" if usage else "unknown",
                    "native_usage_json": json.dumps(usage, separators=(",", ":")),
                })
        updated = self._repository.update_model_results(
            connection_id=connection_id,
            actor_user_id=owner_user_id,
            can_manage=can_manage,
            model_results=results,
        )
        if updated is None:
            raise ConnectionError("模型连接不存在")
        if (
            str(context.get("preset_id") or "") == settings.llm_default_provider
            and self._repository.get_usage_preference(owner_user_id) is None
            and updated.get("status") == "verified"
        ):
            preferred_model = str(
                getattr(
                    settings,
                    f"{settings.llm_default_provider}_model",
                    updated.get("default_model"),
                )
            )
            available_models = {
                str(item["model_id"])
                for item in updated.get("models", [])
                if item["status"] == "available" and item["enabled"]
            }
            selected = (
                preferred_model
                if preferred_model in available_models
                else str(updated["default_model"])
            )
            self._repository.set_usage_preference(
                owner_user_id,
                connection_id,
                selected,
            )
        return updated

    def set_default_model(
        self,
        *,
        owner_user_id: str,
        connection_id: str,
        model_id: str,
        can_manage: bool = False,
    ) -> dict[str, object]:
        """显式修改当前连接默认模型，不改写既有 TaskRevision。"""

        try:
            updated = self._repository.set_default_model(
                connection_id=connection_id,
                actor_user_id=owner_user_id,
                can_manage=can_manage,
                model_id=model_id.strip(),
            )
        except ValueError as exc:
            raise ConnectionError(str(exc)) from exc
        if updated is None:
            raise ConnectionError("模型连接不存在")
        return updated

    def set_model_enabled(
        self,
        *,
        owner_user_id: str,
        connection_id: str,
        model_id: str,
        enabled: bool,
        can_manage: bool = False,
    ) -> dict[str, object]:
        """独立启停连接模型；停用默认模型后失败关闭等待重新选择。"""

        try:
            updated = self._repository.set_model_enabled(
                connection_id=connection_id,
                actor_user_id=owner_user_id,
                can_manage=can_manage,
                model_id=model_id.strip(),
                enabled=enabled,
            )
        except ValueError as exc:
            raise ConnectionError(str(exc)) from exc
        if updated is None:
            raise ConnectionError("模型连接不存在")
        return updated

    def set_platform_enabled(
        self,
        connection_id: str,
        *,
        enabled: bool,
    ) -> dict[str, object]:
        """管理员启停整套平台连接；停用不自动切换其他连接。"""

        try:
            updated = self._repository.set_platform_enabled(
                connection_id,
                enabled=enabled,
            )
        except ValueError as exc:
            raise ConnectionError(str(exc)) from exc
        if updated is None:
            raise ConnectionError("平台连接不存在")
        return updated

    def freeze_connection(
        self,
        owner_user_id: str,
        connection_id: str,
    ) -> ConnectionBinding:
        """读取连接当前版本，供 TaskRevision 以非敏感引用冻结。"""

        connection = self._repository.get_authorized_internal(
            connection_id,
            owner_user_id,
        )
        if connection is None:
            raise GrantError("模型连接不存在或无权访问")
        return ConnectionBinding(
            connection_id=connection_id,
            connection_version=_connection_version(connection),
            model=str(connection["model"]),
        )

    def issue_grant(
        self,
        *,
        owner_user_id: str,
        connection_id: str,
        connection_version: str,
        task_id: str,
        revision: int,
        run_id: str,
        purpose: str,
        model_id: str | None = None,
        ttl_seconds: int | None = None,
        grant_id: str | None = None,
    ) -> AccessGrant:
        """为一个 Run 的单一用途签发短期权利，不返回 Provider Secret。"""

        if purpose not in {"agent_inference", "candidate_verify"}:
            raise GrantError("未知的模型连接 Grant 用途")
        if revision < 1:
            raise GrantError("Grant revision 必须大于等于 1")
        effective_ttl = (
            self._grant_ttl_seconds
            if ttl_seconds is None
            else ttl_seconds
        )
        if effective_ttl <= 0:
            raise GrantError("Grant TTL 必须大于 0")
        connection = self._repository.get_authorized_internal(
            connection_id,
            owner_user_id,
        )
        if connection is None:
            raise GrantError("模型连接不存在或无权访问")
        if _connection_version(connection) != connection_version:
            # 任务创建后的换 Key、换模型或换预设版本都必须进入新修订。
            raise GrantError("模型连接版本已变化，请创建新任务版本后重试")
        frozen_model = model_id or str(connection["model"])
        if not self._repository.connection_model_available(
            connection_id,
            frozen_model,
        ):
            raise GrantError("任务冻结模型已失效，请创建新任务版本后重试")
        connection = {**connection, "model": frozen_model}
        now = self._clock()
        expires_at = now + timedelta(seconds=effective_ttl)
        resolved_grant_id = grant_id or f"grant_{uuid.uuid4().hex}"
        if not resolved_grant_id.startswith("grant_") or len(resolved_grant_id) > 160:
            raise GrantError("Grant 身份格式无效")
        token = secrets.token_urlsafe(32)
        self._repository.create_grant(
            grant_id=resolved_grant_id,
            token_hash=_token_hash(token),
            owner_user_id=owner_user_id,
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            connection=connection,
            purpose=purpose,
            expires_at=expires_at.isoformat(),
        )
        return AccessGrant(
            grant_id=resolved_grant_id,
            token=token,
            connection_id=connection_id,
            connection_version=connection_version,
            owner_user_id=owner_user_id,
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            purpose=purpose,
            api_format=str(connection["api_format"]),
            model=str(connection["model"]),
            expires_at=expires_at,
        )

    def revoke_grant(self, grant_id: str, reason: str) -> bool:
        """幂等撤销单个 Grant。"""

        return self._repository.revoke_grant(
            grant_id,
            reason.strip() or "revoked",
        )

    def revoke_run_grants(
        self,
        owner_user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        *,
        reason: str,
    ) -> int:
        """在恢复轮换或 Run 终态一次撤销该 Run 的全部 Grant。"""

        return self._repository.revoke_run_grants(
            owner_user_id,
            task_id,
            revision,
            run_id,
            reason=reason.strip() or "run_closed",
        )

    def revoke_revision_grants(
        self,
        owner_user_id: str,
        task_id: str,
        revision: int,
        *,
        reason: str,
    ) -> int:
        """按持久 TaskRevision 身份关闭服务重启前遗留的 Grant。"""

        return self._repository.revoke_revision_grants(
            owner_user_id,
            task_id,
            revision,
            reason=reason.strip() or "revision_closed",
        )

    def list_usage(
        self,
        owner_user_id: str,
        *,
        task_id: str,
        revision: int,
    ) -> list[dict[str, object]]:
        """返回 Owner 可见的最小原生用量摘要。"""

        return self._repository.list_usage(
            owner_user_id,
            task_id=task_id,
            revision=revision,
        )

    def get_usage_for_grant(
        self,
        owner_user_id: str,
        *,
        task_id: str,
        revision: int,
        run_id: str,
        grant_id: str,
    ) -> dict[str, object] | None:
        """返回单次 Provider Attempt 的安全用量投影。"""

        usage = self._repository.get_usage_for_grant(
            owner_user_id,
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            grant_id=grant_id,
        )
        if usage is None:
            return None
        return {
            "provider_attempt_id": usage["grant_id"],
            "run_id": usage["run_id"],
            "status": usage["status"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "request_count": usage["request_count"],
            # 当前没有可信价格快照，费用必须明确保持未知，不能用 0 伪装。
            "cost_status": "unknown",
            "cost": None,
        }

    async def relay(
        self,
        *,
        grant_token: str,
        protocol_path: str,
        method: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> RelayResponse:
        """按 Grant 冻结的原生协议执行一次精确透传。"""

        grant = self._resolve_active_grant(grant_token)
        if method.upper() != "POST":
            raise GrantError("模型 Relay 只允许 POST")
        operation = _validate_protocol_path(
            str(grant["api_format"]),
            protocol_path,
            str(grant["model"]),
        )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GrantError("模型 Relay 请求体必须是 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise GrantError("模型 Relay 请求体必须是 JSON 对象")
        if str(grant["api_format"]) != "gemini_generate_content":
            if payload.get("model") != grant["model"]:
                raise GrantError("请求模型与 Grant 冻结模型不一致")

        endpoint = _provider_endpoint(grant, operation)
        allow_private = grant["locality"] == "managed_private"
        try:
            target = HttpSecurityGuard(
                allow_private=allow_private,
                proxy_fake_ip_host_allowlist=_OFFICIAL_PRESET_HTTPS_HOSTS,
                resolver=self._resolver,
            ).validate(endpoint)
        except SsrfError as exc:
            # Relay 面向不可信 Runtime，不回显 Endpoint、解析 IP 或内部策略细节。
            raise GrantError("Provider Endpoint 被安全策略拒绝") from exc

        outbound_headers = _safe_provider_headers(headers)
        outbound_headers.setdefault("Content-Type", "application/json")
        ciphertext = grant.get("ciphertext")
        try:
            provider_secret = (
                self._vault.decrypt(str(ciphertext)) if ciphertext else ""
            )
        except VaultDecryptionError as exc:
            raise ConnectionError("Provider 凭证密文无法解密") from exc
        _inject_provider_auth(
            outbound_headers,
            api_format=str(grant["api_format"]),
            provider_secret=provider_secret,
        )
        client_kwargs: dict[str, object] = {
            "timeout": self._provider_timeout_seconds,
            "follow_redirects": False,
            "trust_env": False,
        }
        client_kwargs["transport"] = PinnedAsyncHTTPTransport(
            target=target,
            transport=self._transport,
        )
        client = httpx.AsyncClient(**client_kwargs)
        request = client.build_request(
            "POST",
            endpoint,
            headers=outbound_headers,
            content=body,
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            self._record_unknown_usage(grant)
            raise ProviderOutcomeUnknownError(
                "Provider 连接失败，Relay 结果无法确认"
            ) from exc

        def finalize(response_body: bytes) -> None:
            usage = _extract_native_usage(
                str(grant["api_format"]),
                response_body,
            )
            self._repository.record_usage(
                grant=grant,
                status=usage["status"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                total_tokens=usage["total_tokens"],
                native_json=json.dumps(
                    usage["native"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )

        return RelayResponse(
            response=response,
            client=client,
            finalize=finalize,
        )

    def _record_unknown_usage(
        self,
        grant: Mapping[str, object],
    ) -> None:
        self._repository.record_usage(
            grant=dict(grant),
            status="unknown",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            native_json="{}",
        )

    def _resolve_active_grant(
        self,
        grant_token: str,
    ) -> dict[str, object]:
        grant = self._repository.resolve_grant(
            _token_hash(grant_token.strip())
        )
        if grant is None:
            raise GrantError("模型连接 Grant 无效")
        if grant["revoked_at"] is not None:
            raise GrantError("模型连接 Grant 已撤销")
        expires_at = datetime.fromisoformat(str(grant["expires_at"]))
        now = self._clock()
        if expires_at.tzinfo is None and now.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now >= expires_at:
            raise GrantError("模型连接 Grant 已过期")
        if grant["connection_status"] != "verified":
            raise GrantError("模型连接已停用")
        if grant["secret_id"] != grant["current_secret_id"]:
            raise GrantError("模型连接版本已轮换")
        if grant["secret_id"] is not None and not grant["ciphertext"]:
            raise GrantError("模型连接密文已销毁")
        return grant

    async def configure_personal(
        self,
        *,
        owner_user_id: str,
        preset_id: str,
        api_key: str,
        model: str | None = None,
    ) -> dict[str, object]:
        """旧版兼容入口：验证并覆盖 Owner 在该 Preset 下的兼容槽。"""

        preset, selected_model, secret = await self._verify_preset_credentials(
            preset_id=preset_id,
            api_key=api_key,
            model=model,
        )
        verified_at = datetime.now().isoformat(timespec="seconds")
        return self._repository.upsert_personal(
            owner_user_id=owner_user_id,
            preset_id=preset.preset_id,
            preset_version=preset.version,
            display_name=preset.display_name,
            base_url=preset.base_url,
            model=selected_model,
            api_format=preset.api_format,
            ciphertext=self._vault.encrypt(secret),
            key_hint=secret[-4:],
            verified_at=verified_at,
        )

    async def create_personal(
        self,
        *,
        owner_user_id: str,
        display_name: str,
        preset_id: str,
        api_key: str,
        model: str | None = None,
    ) -> dict[str, object]:
        """创建一套独立命名个人连接；不覆盖同 Provider 的其他连接。"""

        name = display_name.strip()
        if not name:
            raise ConnectionError("连接名称不能为空")
        if len(name) > 80:
            raise ConnectionError("连接名称不能超过 80 个字符")
        preset = PRESETS_BY_ID.get(preset_id)
        if preset is None:
            raise ConnectionError("未知的 Provider 预设")
        selected_model = model or preset.recommended_model
        if selected_model not in preset.models:
            raise ConnectionError("所选默认模型不在当前平台目录中")
        secret = api_key.strip()
        if len(secret) < 8:
            raise ConnectionError("API Key 过短或为空")
        model_results = await self._verify_preset_models(
            preset=preset,
            api_key=secret,
        )
        available_models = [
            str(item["model_id"])
            for item in model_results
            if item["status"] == "available" and bool(item["enabled"])
        ]
        if not available_models:
            raise ConnectionValidationError(
                "所有推荐模型验证失败，连接未保存",
                model_results,
            )
        if selected_model not in available_models:
            # 创建阶段帮助新手选中首个真实可用模型，并通过响应明确展示调整结果。
            selected_model = available_models[0]
        verified_at = datetime.now().isoformat(timespec="seconds")
        return self._repository.create_personal(
            owner_user_id=owner_user_id,
            preset_id=preset.preset_id,
            preset_version=preset.version,
            display_name=name,
            base_url=preset.base_url,
            model=selected_model,
            api_format=preset.api_format,
            ciphertext=self._vault.encrypt(secret),
            key_hint=secret[-4:],
            verified_at=verified_at,
            model_results=model_results,
        )

    async def _verify_preset_models(
        self,
        *,
        preset: ProviderPreset,
        api_key: str,
        model_ids: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """逐项验证少量推荐模型；一项失败不阻断其余模型。"""

        results: list[dict[str, object]] = []
        selected = set(model_ids) if model_ids is not None else None
        for item in preset.model_catalog:
            if selected is not None and item.model_id not in selected:
                continue
            verified_at = datetime.now().isoformat(timespec="seconds")
            try:
                usage = await self._verify_preset_model(
                    preset=preset,
                    model=item.model_id,
                    api_key=api_key,
                )
            except ProviderVerificationError as exc:
                results.append(
                    {
                        "model_id": item.model_id,
                        "display_name": item.display_name,
                        "catalog_role": item.role,
                        "catalog_version": preset.version,
                        "status": exc.code,
                        "enabled": False,
                        "verified_at": verified_at,
                        "error_code": exc.code,
                        "usage_status": "unknown",
                        "native_usage_json": "{}",
                    }
                )
                continue
            results.append(
                {
                    "model_id": item.model_id,
                    "display_name": item.display_name,
                    "catalog_role": item.role,
                    "catalog_version": preset.version,
                    "status": "available",
                    "enabled": True,
                    "verified_at": verified_at,
                    "error_code": None,
                    "usage_status": "reported" if usage else "unknown",
                    "native_usage_json": json.dumps(
                        usage or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        return results

    async def _verify_preset_credentials(
        self,
        *,
        preset_id: str,
        api_key: str,
        model: str | None,
    ) -> tuple[ProviderPreset, str, str]:
        """统一验证 Preset、模型与 Key，确保创建和兼容更新遵循同一协议。"""

        preset = PRESETS_BY_ID.get(preset_id)
        if preset is None:
            raise ConnectionError("未知的 Provider 预设")
        selected_model = model or preset.recommended_model
        if selected_model not in preset.models:
            raise ConnectionError("所选模型不在当前平台目录中")
        secret = api_key.strip()
        if len(secret) < 8:
            raise ConnectionError("API Key 过短或为空")

        await self._verify_preset_model(
            preset=preset,
            model=selected_model,
            api_key=secret,
        )
        return preset, selected_model, secret

    async def _verify_preset_model(
        self,
        *,
        preset: ProviderPreset,
        model: str,
        api_key: str,
    ) -> dict[str, int | float]:
        if preset.api_format == "openai_chat_completions":
            return await self._verify_openai_chat(
                base_url=preset.base_url,
                model=model,
                api_key=api_key,
                allow_private=False,
            )
        elif preset.api_format == "openai_responses":
            return await self._verify_openai_responses(
                base_url=preset.base_url,
                model=model,
                api_key=api_key,
            )
        elif preset.api_format == "anthropic_messages":
            return await self._verify_anthropic_messages(
                base_url=preset.base_url,
                model=model,
                api_key=api_key,
            )
        elif preset.api_format == "gemini_generate_content":
            return await self._verify_gemini_generate_content(
                base_url=preset.base_url,
                model=model,
                api_key=api_key,
            )
        raise ProviderVerificationError(
            "protocol_incompatible",
            "该 Provider 协议尚未接入连接验证",
        )

    async def register_managed(
        self,
        *,
        actor_user_id: str,
        display_name: str,
        base_url: str,
        api_format: str,
        model: str,
        models: list[str] | None = None,
        api_key: str = "",
    ) -> dict[str, object]:
        """管理员验证并发布一个精确的公网或 LAN 模型连接。"""

        supported_formats = {
            "anthropic_messages",
            "openai_chat_completions",
            "openai_responses",
            "gemini_generate_content",
        }
        if api_format not in supported_formats:
            raise ConnectionError("不支持的 API 格式")
        name = display_name.strip()
        selected_model = model.strip()
        endpoint_root = base_url.strip().rstrip("/")
        if not name or not selected_model:
            raise ConnectionError("连接名称和模型不能为空")
        parts = urlsplit(endpoint_root)
        if parts.query or parts.fragment:
            raise ConnectionError("Base URL 不允许 query 或 fragment")
        try:
            target = HttpSecurityGuard(
                allow_private=True,
                resolver=self._resolver,
            ).validate(endpoint_root)
        except SsrfError as exc:
            raise ConnectionError(f"管理 Endpoint 被安全策略拒绝：{exc}") from exc

        private_flags = tuple(
            ipaddress.ip_address(item).is_private for item in target.ips
        )
        if any(private_flags) and not all(private_flags):
            raise ConnectionError("Endpoint 同时解析到公网和私网地址，拒绝登记")
        is_private = bool(private_flags) and all(private_flags)
        if not is_private and target.scheme != "https":
            raise ConnectionError("公网自定义 Endpoint 只允许 HTTPS")

        secret = api_key.strip()
        if not is_private and not secret:
            # 公网 Provider 默认应有长期鉴权；空 Key 只保留给精确登记的无鉴权 LAN/本地服务。
            raise ConnectionError("公网自定义连接必须填写 API Key")
        requested_models = list(
            dict.fromkeys(
                item.strip()
                for item in (models or [selected_model])
                if item.strip()
            )
        )
        if not requested_models or len(requested_models) > 8:
            raise ConnectionError("一次必须验证 1–8 个模型")
        results: list[dict[str, object]] = []
        for index, model_id in enumerate(requested_models):
            try:
                usage = await self._verify_custom_model(
                    base_url=endpoint_root,
                    api_format=api_format,
                    model=model_id,
                    api_key=secret,
                    allow_private=is_private,
                )
                status = "available"
                error_code = None
            except ProviderVerificationError as exc:
                usage = {}
                status = exc.code
                error_code = exc.code
            results.append({
                "model_id": model_id,
                "display_name": model_id,
                "catalog_role": "custom",
                "catalog_version": "custom-v1",
                "status": status,
                "enabled": status == "available",
                "verified_at": datetime.now().isoformat(timespec="seconds"),
                "error_code": error_code,
                "usage_status": "reported" if usage else "unknown",
                "native_usage_json": json.dumps(usage, separators=(",", ":")),
                "catalog_order": index,
            })
        available = [
            str(item["model_id"]) for item in results if item["status"] == "available"
        ]
        if not available:
            raise ConnectionValidationError("全部模型验证失败，连接未发布", results)
        if selected_model not in available:
            selected_model = available[0]
        verified_at = datetime.now().isoformat(timespec="seconds")
        return self._repository.create_managed(
            created_by=actor_user_id,
            display_name=name,
            base_url=endpoint_root,
            model=selected_model,
            api_format=api_format,
            locality="managed_private" if is_private else "public_external",
            ciphertext=self._vault.encrypt(secret) if secret else None,
            key_hint=secret[-4:] if secret else "",
            verified_at=verified_at,
            model_results=results,
        )

    async def _verify_custom_model(
        self,
        *,
        base_url: str,
        api_format: str,
        model: str,
        api_key: str,
        allow_private: bool,
    ) -> dict[str, int | float]:
        if api_format == "openai_chat_completions":
            return await self._verify_openai_chat(
                base_url=base_url, model=model, api_key=api_key,
                allow_private=allow_private,
            )
        if api_format == "openai_responses":
            return await self._verify_openai_responses(
                base_url=base_url, model=model, api_key=api_key,
                allow_private=allow_private,
            )
        if api_format == "anthropic_messages":
            return await self._verify_anthropic_messages(
                base_url=base_url, model=model, api_key=api_key,
                allow_private=allow_private,
            )
        if api_format == "gemini_generate_content":
            return await self._verify_gemini_generate_content(
                base_url=base_url, model=model, api_key=api_key,
                allow_private=allow_private,
            )
        raise ConnectionError("不支持的 API 格式")

    async def discover_custom(
        self,
        *,
        base_url: str,
        api_key: str,
        model_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """发现模型并分别探测四种协议；模型列表成功本身不代表协议可用。"""

        endpoint_root = base_url.strip().rstrip("/")
        target = HttpSecurityGuard(
            allow_private=True,
            resolver=self._resolver,
        ).validate(endpoint_root)
        private_flags = tuple(ipaddress.ip_address(item).is_private for item in target.ips)
        if any(private_flags) and not all(private_flags):
            raise ConnectionError("Endpoint 同时解析到公网和私网地址，拒绝探测")
        is_private = bool(private_flags) and all(private_flags)
        if not is_private and target.scheme != "https":
            raise ConnectionError("公网自定义 Endpoint 只允许 HTTPS")
        secret = api_key.strip()
        if not is_private and not secret:
            raise ConnectionError("公网自定义连接必须填写 API Key")
        discovered: list[str] = []
        client_kwargs: dict[str, object] = {
            "timeout": 30.0,
            "follow_redirects": False,
            "trust_env": False,
        }
        client_kwargs["transport"] = PinnedAsyncHTTPTransport(
            target=target,
            transport=self._transport,
        )
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(
                    f"{endpoint_root}/models",
                    headers={"Authorization": f"Bearer {secret}"} if secret else {},
                )
                if response.status_code == 200:
                    payload = response.json()
                    candidates = payload.get("data") or payload.get("models") or []
                    for item in candidates:
                        value = item.get("id") or item.get("name") if isinstance(item, dict) else None
                        if value:
                            discovered.append(str(value).removeprefix("models/"))
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            discovered = []
        requested = list(dict.fromkeys(
            item.strip() for item in (model_ids or discovered) if item.strip()
        ))[:8]
        detected: list[str] = []
        failures: dict[str, str] = {}
        if requested:
            for api_format in (
                "anthropic_messages",
                "openai_chat_completions",
                "openai_responses",
                "gemini_generate_content",
            ):
                try:
                    await self._verify_custom_model(
                        base_url=endpoint_root,
                        api_format=api_format,
                        model=requested[0],
                        api_key=secret,
                        allow_private=is_private,
                    )
                    detected.append(api_format)
                except (ConnectionError, ProviderVerificationError) as exc:
                    failures[api_format] = (
                        exc.code if isinstance(exc, ProviderVerificationError)
                        else "network_unreachable"
                    )
        return {
            "models": requested,
            "models_discovered": bool(discovered),
            "detected_api_formats": detected,
            "recommended_api_format": detected[0] if detected else None,
            "failures": failures,
            "manual_models_required": not bool(requested),
        }

    async def configure_platform_preset(
        self,
        *,
        actor_user_id: str,
        display_name: str,
        preset_id: str,
        api_key: str,
        model: str | None = None,
    ) -> dict[str, object]:
        """验证并发布平台共享 Preset；API Key 对所有公网 Provider 必填。"""

        preset = PRESETS_BY_ID.get(preset_id)
        if preset is None:
            raise ConnectionError("未知的 Provider 预设")
        name = display_name.strip()
        if not name:
            raise ConnectionError("连接名称不能为空")
        selected_model = model or preset.recommended_model
        if selected_model not in preset.models:
            raise ConnectionError("所选模型不在当前平台目录中")
        secret = api_key.strip()
        if len(secret) < 8:
            raise ConnectionError("API Key 过短或为空")

        results = await self._verify_preset_models(
            preset=preset,
            api_key=secret,
            model_ids=list(preset.models),
        )
        available = [
            str(item["model_id"])
            for item in results
            if item["status"] == "available"
        ]
        if not available:
            raise ConnectionValidationError(
                "全部推荐模型验证失败，平台连接未发布",
                results,
            )
        if selected_model not in available:
            selected_model = available[0]

        verified_at = datetime.now().isoformat(timespec="seconds")
        return self._repository.create_managed(
            created_by=actor_user_id,
            display_name=name,
            base_url=preset.base_url,
            model=selected_model,
            api_format=preset.api_format,
            locality="public_external",
            ciphertext=self._vault.encrypt(secret),
            key_hint=secret[-4:],
            verified_at=verified_at,
            preset_id=preset.preset_id,
            preset_version=preset.version,
            model_results=results,
        )

    async def _verify_openai_chat(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        allow_private: bool,
    ) -> dict[str, int | float]:
        """用无业务数据的极小 Chat Completions 请求验证连接。"""

        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        payload = await self._post_json(
            endpoint=endpoint,
            headers=headers,
            body={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 16,
                "stream": False,
            },
            allow_private=allow_private,
        )
        if not isinstance(payload, dict) or not payload.get("choices"):
            raise ProviderVerificationError(
                "protocol_incompatible",
                "Provider 返回格式与 Chat Completions 不匹配",
            )
        return _numeric_usage(payload.get("usage"))

    async def _verify_openai_responses(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        allow_private: bool = False,
    ) -> dict[str, int | float]:
        """用无业务数据的极小 Responses 请求验证连接。"""

        endpoint = f"{base_url.rstrip('/')}/responses"
        payload = await self._post_json(
            endpoint=endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            body={
                "model": model,
                "input": "Reply with OK.",
                "max_output_tokens": 16,
                "store": False,
            },
            allow_private=allow_private,
        )
        if not isinstance(payload, dict) or payload.get("object") != "response":
            raise ProviderVerificationError(
                "protocol_incompatible",
                "Provider 返回格式与 Responses 不匹配",
            )
        return _numeric_usage(payload.get("usage"))

    async def _verify_anthropic_messages(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        allow_private: bool = False,
    ) -> dict[str, int | float]:
        """用无业务数据的极小 Anthropic Messages 请求验证连接。"""

        endpoint = f"{base_url.rstrip('/')}/v1/messages"
        payload = await self._post_json(
            endpoint=endpoint,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            body={
                "model": model,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            },
            allow_private=allow_private,
        )
        if not isinstance(payload, dict) or payload.get("type") != "message":
            raise ProviderVerificationError(
                "protocol_incompatible",
                "Provider 返回格式与 Anthropic Messages 不匹配",
            )
        return _numeric_usage(payload.get("usage"))

    async def _verify_gemini_generate_content(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        allow_private: bool = False,
    ) -> dict[str, int | float]:
        """用无业务数据的极小 Gemini generateContent 请求验证连接。"""

        endpoint = f"{base_url.rstrip('/')}/models/{model}:generateContent"
        payload = await self._post_json(
            endpoint=endpoint,
            headers={"x-goog-api-key": api_key},
            body={
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": "Reply with OK."}],
                    }
                ],
                "generationConfig": {"maxOutputTokens": 16},
            },
            allow_private=allow_private,
        )
        if not isinstance(payload, dict) or not payload.get("candidates"):
            raise ProviderVerificationError(
                "protocol_incompatible",
                "Provider 返回格式与 Gemini generateContent 不匹配",
            )
        return _numeric_usage(payload.get("usageMetadata"))

    async def _post_json(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        body: dict[str, object],
        allow_private: bool = False,
    ) -> object:
        """执行一次失败关闭的 Provider JSON 请求。"""

        try:
            target = HttpSecurityGuard(
                allow_private=allow_private,
                proxy_fake_ip_host_allowlist=_OFFICIAL_PRESET_HTTPS_HOSTS,
                resolver=self._resolver,
            ).validate(endpoint)
        except SsrfError as exc:
            raise ProviderVerificationError(
                "network_unreachable",
                f"Provider Endpoint 被安全策略拒绝：{exc}",
            ) from exc
        kwargs: dict[str, object] = {
            "timeout": 30.0,
            "follow_redirects": False,
            "trust_env": False,
        }
        kwargs["transport"] = PinnedAsyncHTTPTransport(
            target=target,
            transport=self._transport,
        )
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ProviderVerificationError(
                "network_unreachable",
                "Provider 连接失败",
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            if response.status_code == 401:
                code = "credentials_invalid"
            elif response.status_code in {403, 404}:
                code = "model_access_denied"
            elif response.status_code == 429:
                code = "rate_limited"
            elif response.status_code >= 500:
                code = "network_unreachable"
            else:
                code = "protocol_incompatible"
            raise ProviderVerificationError(
                code,
                f"Provider 验证失败（HTTP {response.status_code}）",
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderVerificationError(
                "protocol_incompatible",
                "Provider 返回的不是有效 JSON",
            ) from exc
        return payload


_default_broker: ConnectionBroker | None = None


def get_default_broker() -> ConnectionBroker:
    """延迟创建进程内 Broker；只在首次连接操作时生成独立密钥文件。"""

    global _default_broker
    if _default_broker is None:
        db_path = Path(settings.webui_db_path)
        key_path = db_path.with_name(f"{db_path.name}.model-connections.key")
        _default_broker = ConnectionBroker(
            repository=ModelConnectionRepository(str(db_path)),
            vault=FernetCredentialVault.from_key_file(key_path),
            provider_timeout_seconds=settings.pi_runtime_timeout_seconds,
        )
    return _default_broker


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _numeric_usage(value: object) -> dict[str, int | float]:
    """只保留 Provider Usage 中的数值字段，绝不持久化响应正文或错误消息。"""

    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    }


def _connection_version(connection: Mapping[str, object]) -> str:
    """密钥或默认模型变化都形成新版本，旧任务修订因此失败关闭。"""

    secret_version = str(
        connection.get("secret_id") or connection["connection_id"]
    )
    model = str(connection["model"])
    return hashlib.sha256(
        f"{secret_version}\0{model}".encode("utf-8")
    ).hexdigest()


def _validate_protocol_path(
    api_format: str,
    protocol_path: str,
    model: str,
) -> str:
    path = protocol_path.strip().lstrip("/")
    allowed: dict[str, set[str]] = {
        "openai_chat_completions": {
            "chat/completions",
            "v1/chat/completions",
        },
        "openai_responses": {"responses", "v1/responses"},
        "anthropic_messages": {"messages", "v1/messages"},
        "gemini_generate_content": {
            f"models/{model}:generateContent",
            f"models/{model}:streamGenerateContent",
            f"v1beta/models/{model}:generateContent",
            f"v1beta/models/{model}:streamGenerateContent",
        },
    }
    if path not in allowed.get(api_format, set()):
        raise GrantError("请求路径不属于 Grant 冻结的 Provider 协议")
    return path.rsplit(":", maxsplit=1)[-1] if ":" in path else path


def _provider_endpoint(
    grant: Mapping[str, object],
    operation: str,
) -> str:
    base_url = str(grant["base_url"]).rstrip("/")
    api_format = str(grant["api_format"])
    if api_format == "openai_chat_completions":
        return f"{base_url}/chat/completions"
    if api_format == "openai_responses":
        return f"{base_url}/responses"
    if api_format == "anthropic_messages":
        return f"{base_url}/v1/messages"
    if api_format == "gemini_generate_content":
        endpoint = (
            f"{base_url}/models/{grant['model']}:"
            f"{operation}"
        )
        if operation == "streamGenerateContent":
            return f"{endpoint}?alt=sse"
        return endpoint
    raise GrantError("Grant 的 Provider 协议尚未接入 Relay")


def _safe_provider_headers(
    headers: Mapping[str, str],
) -> dict[str, str]:
    allowed = {
        "accept",
        "anthropic-beta",
        "anthropic-version",
        "content-type",
        "openai-beta",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in allowed
    }


def _inject_provider_auth(
    headers: dict[str, str],
    *,
    api_format: str,
    provider_secret: str,
) -> None:
    if not provider_secret:
        return
    if api_format in {
        "openai_chat_completions",
        "openai_responses",
    }:
        headers["Authorization"] = f"Bearer {provider_secret}"
    elif api_format == "anthropic_messages":
        headers["x-api-key"] = provider_secret
        headers.setdefault("anthropic-version", "2023-06-01")
    elif api_format == "gemini_generate_content":
        headers["x-goog-api-key"] = provider_secret


def _extract_native_usage(
    api_format: str,
    body: bytes,
) -> dict[str, object]:
    observed: list[dict[str, object]] = []
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, (dict, list)):
        _collect_usage(payload, observed)
    for line in body.splitlines():
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            event = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _collect_usage(event, observed)

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    for usage in observed:
        if api_format == "gemini_generate_content":
            input_tokens = _last_int(
                input_tokens,
                usage.get("promptTokenCount"),
            )
            output_tokens = _last_int(
                output_tokens,
                usage.get("candidatesTokenCount"),
            )
            total_tokens = _last_int(
                total_tokens,
                usage.get("totalTokenCount"),
            )
        else:
            input_tokens = _last_int(
                input_tokens,
                usage.get("input_tokens"),
                usage.get("prompt_tokens"),
            )
            output_tokens = _last_int(
                output_tokens,
                usage.get("output_tokens"),
                usage.get("completion_tokens"),
            )
            total_tokens = _last_int(
                total_tokens,
                usage.get("total_tokens"),
            )
    recorded = any(
        value is not None
        for value in (input_tokens, output_tokens, total_tokens)
    )
    return {
        "status": "recorded" if recorded else "unknown",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "native": {"observed": observed},
    }


def _collect_usage(
    value: object,
    observed: list[dict[str, object]],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"usage", "usageMetadata"} and isinstance(item, dict):
                observed.append(item)
            else:
                _collect_usage(item, observed)
    elif isinstance(value, list):
        for item in value:
            _collect_usage(item, observed)


def _last_int(
    current: int | None,
    *values: object,
) -> int | None:
    for value in values:
        if isinstance(value, int) and value >= 0:
            current = value
    return current
