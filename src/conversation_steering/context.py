# -*- coding: utf-8 -*-
"""按冻结优先级编译有界上下文，不复制完整历史或日志。"""
from __future__ import annotations

import hashlib
import math
import uuid

from .models import (
    CompiledContext,
    ContextCompileRequest,
    ContextCompositionItem,
)


class ContextCompiler:
    def compile(self, request: ContextCompileRequest) -> CompiledContext:
        mandatory: list[tuple[str, str, str]] = []
        mandatory.extend(
            ("system", f"system:{index}", text)
            for index, text in enumerate(request.system_boundaries)
        )
        mandatory.append(("goal", f"revision:{request.revision}", request.goal_contract))
        mandatory.extend(
            ("confirmed_semantics", f"semantic:{index}", text)
            for index, text in enumerate(request.confirmed_semantics)
        )
        optional: list[tuple[str, str, str]] = []
        if request.run_summary:
            optional.append(("run", "run:current", request.run_summary))
        optional.extend(
            ("procedure", f"procedure:{index}", text)
            for index, text in enumerate(request.procedure_summaries)
        )
        optional.extend(
            ("task_template", item.source_ref, item.summary)
            for item in request.task_template_summaries
        )
        optional.extend(
            ("owner_memory", item.source_ref, item.summary)
            for item in request.owner_memory_summaries
        )
        optional.extend(
            ("turn", turn.turn_id, turn.text)
            for turn in request.relevant_turns
        )
        optional.extend(
            ("evidence", f"evidence:{index}", text)
            for index, text in enumerate(request.evidence_snippets)
        )

        rendered: list[str] = []
        composition: list[ContextCompositionItem] = []

        def add(category: str, source_ref: str, text: str, *, protected: bool) -> bool:
            block = f"[{category}]\n{text.strip()}"
            separator = "\n\n" if rendered else ""
            if sum(len(item) for item in rendered) + len(separator) + len(block) > request.max_chars:
                return False
            if separator:
                rendered.append(separator)
            rendered.append(block)
            composition.append(
                ContextCompositionItem(
                    category=category,
                    source_ref=source_ref,
                    char_count=len(text.strip()),
                    protected=protected,
                )
            )
            return True

        for category, source_ref, text in mandatory:
            if not add(category, source_ref, text, protected=True):
                raise ValueError("上下文预算不足以保留权限、目标和已确认数据含义")

        omitted: list[str] = []
        for category, source_ref, text in optional:
            if not add(category, source_ref, text, protected=False) and category not in omitted:
                omitted.append(category)

        content = "".join(rendered)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return CompiledContext(
            context_id=f"context_{uuid.uuid4().hex[:16]}",
            owner_id=request.owner_id,
            task_id=request.task_id,
            revision=request.revision,
            content=content,
            composition=tuple(composition),
            char_count=len(content),
            estimated_tokens=math.ceil(len(content) / 4),
            summary_sha256=f"sha256:{digest}",
            omitted_categories=tuple(omitted),
        )
