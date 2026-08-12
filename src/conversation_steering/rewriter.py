# -*- coding: utf-8 -*-
"""使用当前模型生成 ContextDelta；未确认外发时失败关闭。"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.config import settings
from src.llm.provider import get_provider

from .models import (
    ContextDelta,
    DeltaConfidence,
    RawUserTurn,
    SteeringRequest,
    TurnIntent,
)


_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "rewrite-v1.md"


class RewriteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: TurnIntent
    confidence: DeltaConfidence
    normalized_text: str
    direct_answer: str | None = None
    goal_delta: str | None = None
    source_scope_delta: tuple[str, ...] = ()
    selection_delta: dict[str, Any] = Field(default_factory=dict)
    coverage_delta: dict[str, Any] = Field(default_factory=dict)
    field_semantics_delta: dict[str, Any] = Field(default_factory=dict)
    output_delta: tuple[str, ...] = ()
    permission_delta: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()

    @field_validator(
        "selection_delta",
        "coverage_delta",
        "field_semantics_delta",
        mode="before",
    )
    @classmethod
    def normalize_empty_mapping(cls, value):
        return value or {}

    @field_validator(
        "source_scope_delta",
        "output_delta",
        "permission_delta",
        "open_questions",
        mode="before",
    )
    @classmethod
    def normalize_empty_sequence(cls, value):
        return tuple(value or ())


class DeferredExternalRewriter:
    async def rewrite(
        self,
        turn: RawUserTurn,
        request: SteeringRequest,
    ) -> ContextDelta:
        return ContextDelta(
            delta_id=f"delta_{uuid.uuid4().hex[:16]}",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            inherited_revision=request.revision,
            source_turn_ids=(turn.turn_id,),
            intent=TurnIntent.PERMISSION_REQUEST,
            confidence=DeltaConfidence.HIGH,
            normalized_text="需要使用当前外部模型理解这条追问",
            permission_delta=("external_model_context_rewrite",),
            open_questions=("是否允许把本条追问和最小任务摘要发送到当前外部模型？",),
        )


class InstructorContextRewriter:
    def __init__(self, *, provider: str, model: str | None) -> None:
        self._connection = get_provider().resolve_model(provider, model=model)

    async def rewrite(
        self,
        turn: RawUserTurn,
        request: SteeringRequest,
    ) -> ContextDelta:
        # Instructor 只负责外部结构化输出；权限与实质变化仍由本地差异门决定。
        import instructor

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
        extra_body = dict(self._connection.extra_body or {})
        if self._connection.provider == "local" and "qwen3" in self._connection.model.lower():
            chat_template = dict(extra_body.get("chat_template_kwargs") or {})
            chat_template["enable_thinking"] = False
            extra_body["chat_template_kwargs"] = chat_template
        payload = {
            "frozen_revision": request.revision,
            "current_goal": request.current_goal,
            "current_status": request.current_status,
            "status_summary": request.status_summary,
            "selection_reason": request.selection_reason,
            "recent_events": request.event_summaries[-8:],
            "user_turn": turn.text,
        }
        try:
            draft = await client.chat.completions.create(
                model=self._connection.model,
                response_model=RewriteDraft,
                max_retries=0,
                temperature=0,
                max_tokens=2048,
                messages=[
                    {
                        "role": "system",
                        "content": _PROMPT_PATH.read_text(encoding="utf-8"),
                    },
                    {"role": "user", "content": str(payload)},
                ],
                extra_body=extra_body or None,
            )
        finally:
            await raw_client.close()
            await http_client.aclose()
        return ContextDelta(
            delta_id=f"delta_{uuid.uuid4().hex[:16]}",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            inherited_revision=request.revision,
            source_turn_ids=(turn.turn_id,),
            **draft.model_dump(),
        )


def build_context_rewriter(request: SteeringRequest):
    if request.provider != "local" and not request.external_api_confirmed:
        return DeferredExternalRewriter()
    return InstructorContextRewriter(
        provider=request.provider,
        model=request.model,
    )
