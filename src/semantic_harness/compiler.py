# -*- coding: utf-8 -*-
"""Instructor 结构化输出适配；所有重试由外层 LangGraph 统一控制。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, Sequence

import httpx
import instructor
from openai import AsyncOpenAI

from src.config import settings
from src.llm.provider import get_provider

from .compiler_models import CompileDiagnostic, CompileRequest, PlanSemanticsDraft


_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stp-v1.md"
PROMPT_VERSION = "stp-v1"


def load_compiler_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def compiler_prompt_sha256() -> str:
    return hashlib.sha256(load_compiler_prompt().encode("utf-8")).hexdigest()


class PlanDraftGenerator(Protocol):
    provider: str
    model: str
    prompt_version: str
    prompt_sha256: str

    async def generate(
        self,
        request: CompileRequest,
        *,
        diagnostics: Sequence[CompileDiagnostic],
        attempt: int,
    ) -> PlanSemanticsDraft:
        """生成一个草案；一次调用只允许请求模型一次。"""


class DeferredPlanDraftGenerator:
    """外部调用尚未确认时只提供来源信息，不解析连接配置。"""

    prompt_version = PROMPT_VERSION

    def __init__(self, *, provider: str, model: str | None = None) -> None:
        self.provider = provider
        self.model = model or "default"
        self.prompt_sha256 = compiler_prompt_sha256()

    async def generate(
        self,
        request: CompileRequest,
        *,
        diagnostics: Sequence[CompileDiagnostic],
        attempt: int,
    ) -> PlanSemanticsDraft:
        del request, diagnostics, attempt
        raise RuntimeError("外部模型未确认，不得生成计划草案")


class InstructorPlanDraftGenerator:
    """复用项目 OpenAI-compatible 端点，关闭 Instructor 内部自动重试。"""

    prompt_version = PROMPT_VERSION

    def __init__(self, *, provider: str = "local", model: str | None = None) -> None:
        connection = get_provider().resolve_model(provider, model=model)
        self._connection = connection
        self.provider = connection.provider
        self.model = connection.requested_model
        self.prompt_sha256 = compiler_prompt_sha256()

    async def generate(
        self,
        request: CompileRequest,
        *,
        diagnostics: Sequence[CompileDiagnostic],
        attempt: int,
    ) -> PlanSemanticsDraft:
        repair_context = [
            {
                "code": item.code,
                "message": item.message,
                "path": item.path,
            }
            for item in diagnostics
            if item.repairable
        ]
        payload = {
            "objective": request.objective_text,
            "trusted_scope_summary": {
                "has_artifacts": bool(request.artifact_ids),
                "has_sources": bool(request.source_ids),
                "table_scope": request.table_scope,
                "section_patterns": request.section_patterns,
                "time_ranges": request.time_ranges,
                "accepted_formats": request.accepted_formats,
                "accepted_media_types": request.accepted_media_types,
                "requested_output_formats": tuple(
                    item.value
                    for item in request.requested_output_formats
                ),
            },
            "clarification_context": (
                request.clarification.model_dump(mode="json")
                if request.clarification
                else None
            ),
            "prior_plan": (
                request.prior_plan.model_dump(mode="json", exclude_none=True)
                if request.prior_plan
                else None
            ),
            "repair_attempt": attempt,
            "validation_errors": repair_context,
        }
        timeout = min(
            self._connection.timeout,
            settings.semantic_compiler_timeout_seconds,
        )
        http_client = httpx.AsyncClient(
            trust_env=self._connection.trust_env,
            timeout=timeout,
        )
        raw_client = AsyncOpenAI(
            api_key=self._connection.api_key,
            base_url=self._connection.base_url,
            timeout=timeout,
            http_client=http_client,
        )
        client = instructor.from_openai(raw_client, mode=instructor.Mode.JSON)
        extra_body: dict[str, Any] = dict(
            self._connection.extra_body or {}
        )
        if self.provider == "local" and "qwen3" in self.model.lower():
            chat_template = dict(
                extra_body.get("chat_template_kwargs") or {}
            )
            chat_template["enable_thinking"] = False
            extra_body["chat_template_kwargs"] = chat_template
        elif "enable_thinking" in extra_body:
            extra_body["enable_thinking"] = False
        extra: dict[str, Any] = {}
        if extra_body:
            extra["extra_body"] = extra_body
        try:
            return await client.chat.completions.create(
                model=self._connection.model,
                response_model=PlanSemanticsDraft,
                messages=[
                    {"role": "system", "content": load_compiler_prompt()},
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ],
                temperature=0,
                max_tokens=settings.semantic_compiler_max_tokens,
                max_retries=0,
                **extra,
            )
        finally:
            await raw_client.close()
