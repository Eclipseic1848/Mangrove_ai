# -*- coding: utf-8 -*-
"""模板与个人记忆进入 TaskRevision 前的可见、可冻结上下文接缝。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.conversation_steering import (
    CompiledContext,
    ContextCompileRequest,
    ContextCompiler,
    ReferencedContextSummary,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|cookie|password|passwd)\s*[:=]\s*[^\s,;，；]+"
)


def _safe_summary(text: str, *, limit: int = 240) -> str:
    """任务上下文只接收脱敏摘要，不复制个人记忆全文。"""
    normalized = " ".join(text.strip().split())
    redacted = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[已隐藏]", normalized)
    return redacted[:limit]


class TaskTemplateRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    template_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)


class MemorySelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    memory_id: int = Field(ge=1)


class TaskContextSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    template: TaskTemplateRef | None = None
    memories: tuple[MemorySelection, ...] = ()

    @field_validator("memories")
    @classmethod
    def unique_memories(cls, value: tuple[MemorySelection, ...]) -> tuple[MemorySelection, ...]:
        if len({item.memory_id for item in value}) != len(value):
            raise ValueError("记忆引用不得重复")
        if len(value) > 12:
            raise ValueError("单次任务最多引用 12 条个人记忆")
        return value


class TaskTemplateDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    template_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=80)
    goal_contract_draft: str = Field(min_length=1, max_length=4_000)
    delivery_spec_draft: dict[str, Any] = Field(default_factory=dict)
    method_draft: str = Field(default="", max_length=4_000)


class FrozenTemplateRef(TaskTemplateDraft):
    summary_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class FrozenMemoryRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    memory_id: int = Field(ge=1)
    purpose: str
    source: str
    summary: str
    summary_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ProposedContextChanges(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    goal_contract: str | None = None
    delivery_spec: dict[str, Any] = Field(default_factory=dict)
    method: str | None = None


class TaskContextPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    owner_id: str
    purpose: str
    objective_text: str
    output_formats: tuple[str, ...]
    template: FrozenTemplateRef | None = None
    memories: tuple[FrozenMemoryRef, ...] = ()
    proposed_changes: ProposedContextChanges
    compiled_context: CompiledContext
    preview_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class TaskContextRepository:
    """把目录查询和不可变 Revision 快照藏在一个 Owner 隔离接口后。"""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def save_template(self, owner_id: str, draft: TaskTemplateDraft) -> None:
        payload_hash = _digest(draft.model_dump(mode="json"))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO task_templates "
                "(owner_id, template_id, version, title, source, purpose, "
                "goal_contract_draft, delivery_spec_json, method_draft, "
                "summary_sha256, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (owner_id, draft.template_id, draft.version, draft.title,
                 draft.source, draft.purpose, draft.goal_contract_draft,
                 json.dumps(draft.delivery_spec_draft, ensure_ascii=False),
                 draft.method_draft, payload_hash, _now()),
            )

    def get_template(self, owner_id: str, reference: TaskTemplateRef) -> FrozenTemplateRef | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_templates WHERE owner_id=? "
                "AND template_id=? AND version=? AND status='active'",
                (owner_id, reference.template_id, reference.version),
            ).fetchone()
        if row is None:
            return None
        return FrozenTemplateRef(
            template_id=row["template_id"], version=row["version"], title=row["title"],
            source=row["source"], purpose=row["purpose"],
            goal_contract_draft=row["goal_contract_draft"],
            delivery_spec_draft=json.loads(row["delivery_spec_json"]),
            method_draft=row["method_draft"], summary_sha256=row["summary_sha256"],
        )

    def list_templates(self, owner_id: str, purpose: str) -> tuple[FrozenTemplateRef, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT template_id, MAX(version) AS version FROM task_templates "
                "WHERE owner_id=? AND purpose=? AND status='active' "
                "GROUP BY template_id ORDER BY template_id", (owner_id, purpose)
            ).fetchall()
        return tuple(template for row in rows if (template := self.get_template(
            owner_id, TaskTemplateRef(template_id=row["template_id"], version=row["version"])
        )) is not None)

    def get_memory(self, owner_id: str, memory_id: int) -> FrozenMemoryRef | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, text, purpose, source FROM user_memory "
                "WHERE user_id=? AND id=? AND deleted_at IS NULL", (owner_id, memory_id)
            ).fetchone()
        if row is None:
            return None
        summary = _safe_summary(str(row["text"]))
        return FrozenMemoryRef(memory_id=row["id"], purpose=row["purpose"],
            source=row["source"], summary=summary, summary_sha256=_digest(summary))

    def list_memories(self, owner_id: str, purpose: str) -> tuple[FrozenMemoryRef, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM user_memory WHERE user_id=? AND deleted_at IS NULL "
                "AND purpose IN (?, 'general') ORDER BY id DESC LIMIT 50", (owner_id, purpose)
            ).fetchall()
        return tuple(memory for row in rows if (
            memory := self.get_memory(owner_id, int(row["id"]))) is not None)

    def get_frozen(self, owner_id: str, task_id: str, revision: int) -> TaskContextPreview | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM task_revision_contexts "
                "WHERE owner_id=? AND task_id=? AND revision=?", (owner_id, task_id, revision)
            ).fetchone()
        return TaskContextPreview.model_validate_json(row["snapshot_json"]) if row else None


class TaskContextService:
    """唯一公开接缝：列出选项、生成可见草案并冻结精确快照。"""

    def __init__(self, repository: TaskContextRepository) -> None:
        self._repository = repository

    def options(self, owner_id: str, purpose: str) -> dict[str, Any]:
        return {"templates": self._repository.list_templates(owner_id, purpose),
                "memories": self._repository.list_memories(owner_id, purpose)}

    def preview(self, *, owner_id: str, purpose: str, objective_text: str,
                output_formats: tuple[str, ...], selection: TaskContextSelection) -> TaskContextPreview:
        template = None
        if selection.template is not None:
            template = self._repository.get_template(owner_id, selection.template)
            if template is None:
                raise KeyError("模板不存在或无权访问")
            if template.purpose != purpose:
                raise ValueError("模板用途与当前任务不一致")
        memories: list[FrozenMemoryRef] = []
        for selected in selection.memories:
            memory = self._repository.get_memory(owner_id, selected.memory_id)
            if memory is None:
                raise KeyError("记忆不存在或无权访问")
            if memory.purpose not in {purpose, "general"}:
                raise ValueError("记忆用途与当前任务不一致")
            memories.append(memory)
        proposed = ProposedContextChanges(
            goal_contract=template.goal_contract_draft if template else None,
            delivery_spec=template.delivery_spec_draft if template else {},
            method=(template.method_draft or None) if template else None,
        )
        compiled = ContextCompiler().compile(ContextCompileRequest(
            owner_id=owner_id, task_id="draft", revision=1,
            system_boundaries=("模板和记忆不能扩大来源、权限、外发或发布范围，也不能替代来源证据与验证结论。",),
            goal_contract=objective_text,
            task_template_summaries=((ReferencedContextSummary(
                source_ref=f"template:{template.template_id}@{template.version}:{template.summary_sha256}",
                summary="\n".join(item for item in (
                    template.goal_contract_draft, template.method_draft) if item),
            ),) if template else ()),
            owner_memory_summaries=tuple(ReferencedContextSummary(
                source_ref=f"memory:{item.memory_id}:{item.summary_sha256}", summary=item.summary
            ) for item in memories), max_chars=12_000,
        ))
        digest_payload = {"owner_id": owner_id, "purpose": purpose,
            "objective_text": objective_text, "output_formats": output_formats,
            "template": template.model_dump(mode="json") if template else None,
            "memories": [item.model_dump(mode="json") for item in memories],
            "proposed_changes": proposed.model_dump(mode="json"),
            "compiled_context_sha256": compiled.summary_sha256}
        return TaskContextPreview(owner_id=owner_id, purpose=purpose,
            objective_text=objective_text, output_formats=output_formats,
            template=template, memories=tuple(memories), proposed_changes=proposed,
            compiled_context=compiled, preview_sha256=_digest(digest_payload))

    def carry_forward(
        self,
        *,
        owner_id: str,
        source_task_id: str,
        source_revision: int,
        target_task_id: str,
        target_revision: int,
        objective_text: str,
        output_formats: tuple[str, ...],
    ) -> TaskContextPreview | None:
        """沿用已确认引用并按新目标重编译；不回读已删除记忆或浮动模板。"""

        source = self._repository.get_frozen(
            owner_id, source_task_id, source_revision
        )
        if source is None:
            return None
        compiled = ContextCompiler().compile(
            ContextCompileRequest(
                owner_id=owner_id,
                task_id=target_task_id,
                revision=target_revision,
                system_boundaries=(
                    "模板和记忆不能扩大来源、权限、外发或发布范围，也不能替代来源证据与验证结论。",
                ),
                goal_contract=objective_text,
                task_template_summaries=(
                    (
                        ReferencedContextSummary(
                            source_ref=(
                                f"template:{source.template.template_id}@"
                                f"{source.template.version}:"
                                f"{source.template.summary_sha256}"
                            ),
                            summary="\n".join(
                                item
                                for item in (
                                    source.template.goal_contract_draft,
                                    source.template.method_draft,
                                )
                                if item
                            ),
                        ),
                    )
                    if source.template
                    else ()
                ),
                owner_memory_summaries=tuple(
                    ReferencedContextSummary(
                        source_ref=f"memory:{item.memory_id}:{item.summary_sha256}",
                        summary=item.summary,
                    )
                    for item in source.memories
                ),
                max_chars=12_000,
            )
        )
        digest_payload = {
            "owner_id": owner_id,
            "purpose": source.purpose,
            "objective_text": objective_text,
            "output_formats": output_formats,
            "template": (
                source.template.model_dump(mode="json")
                if source.template
                else None
            ),
            "memories": [
                item.model_dump(mode="json") for item in source.memories
            ],
            "proposed_changes": source.proposed_changes.model_dump(mode="json"),
            "compiled_context_sha256": compiled.summary_sha256,
        }
        return source.model_copy(
            update={
                "objective_text": objective_text,
                "output_formats": output_formats,
                "compiled_context": compiled,
                "preview_sha256": _digest(digest_payload),
            }
        )

    def freeze(self, connection: sqlite3.Connection, *, owner_id: str,
               task_id: str, revision: int, preview: TaskContextPreview,
               expected_preview_sha256: str) -> None:
        if preview.owner_id != owner_id:
            raise ValueError("上下文草案与任务 Owner 不一致")
        if preview.preview_sha256 != expected_preview_sha256:
            raise ValueError("上下文预览已变化，请重新确认")
        # 启动前尚无 task_id；冻结时只绑定身份，不改变用户已经检查过的内容与摘要哈希。
        bound_preview = preview.model_copy(
            update={
                "compiled_context": preview.compiled_context.model_copy(
                    update={"task_id": task_id, "revision": revision}
                )
            }
        )
        connection.execute(
            "INSERT INTO task_revision_contexts "
            "(owner_id, task_id, revision, preview_sha256, snapshot_json, "
            "compiled_context_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (owner_id, task_id, revision, preview.preview_sha256,
             bound_preview.model_dump_json(),
             bound_preview.compiled_context.model_dump_json(), _now()),
        )
