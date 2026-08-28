# -*- coding: utf-8 -*-
"""P1-01：任务模板、OwnerMemory 与冻结上下文契约。"""
from __future__ import annotations

import sqlite3

import pytest

from src.api.store import WebUIStore
from src.task_context import (
    MemorySelection,
    TaskContextRepository,
    TaskContextSelection,
    TaskContextService,
    TaskTemplateDraft,
    TaskTemplateRef,
)
from tests.database_migration_helpers import migrated_webui_database


def _service(tmp_path) -> tuple[TaskContextService, TaskContextRepository, WebUIStore]:
    database = migrated_webui_database(tmp_path / "workspace.db")
    repository = TaskContextRepository(database)
    return TaskContextService(repository), repository, WebUIStore(str(database))


def test_preview_requires_owner_purpose_and_explicit_memory_selection(tmp_path) -> None:
    service, repository, store = _service(tmp_path)
    repository.save_template(
        "user-a",
        TaskTemplateDraft(
            template_id="company-research",
            version=1,
            title="公司研究摘要",
            source="owner_created",
            purpose="web_research",
            goal_contract_draft="提取每家公司的名称、主营业务和来源证据",
            delivery_spec_draft={"formats": ["markdown"]},
            method_draft="逐页读取并按公司去重",
        ),
    )
    relevant = store.memory_add(
        "user-a",
        "公司名称优先使用官网全称；api_key=should-not-leak",
        purpose="web_research",
    )
    unrelated = store.memory_add(
        "user-a",
        "发票金额保留两位小数",
        purpose="invoice_processing",
    )
    other_owner = store.memory_add(
        "user-b",
        "其他用户的内部偏好",
        purpose="web_research",
    )

    preview = service.preview(
        owner_id="user-a",
        purpose="web_research",
        objective_text="研究当前网页列出的公司",
        output_formats=("markdown",),
        selection=TaskContextSelection(
            template=TaskTemplateRef(
                template_id="company-research",
                version=1,
            ),
            memories=(MemorySelection(memory_id=relevant["id"]),),
        ),
    )

    assert preview.template is not None
    assert preview.template.version == 1
    assert preview.template.source == "owner_created"
    assert preview.proposed_changes.goal_contract == (
        "提取每家公司的名称、主营业务和来源证据"
    )
    assert [item.memory_id for item in preview.memories] == [relevant["id"]]
    assert "should-not-leak" not in preview.memories[0].summary
    assert "should-not-leak" not in preview.compiled_context.content
    assert unrelated["id"] not in [item.memory_id for item in preview.memories]
    assert preview.preview_sha256.startswith("sha256:")

    with pytest.raises(KeyError, match="记忆不存在或无权访问"):
        service.preview(
            owner_id="user-a",
            purpose="web_research",
            objective_text="研究当前网页列出的公司",
            output_formats=("markdown",),
            selection=TaskContextSelection(
                memories=(MemorySelection(memory_id=other_owner["id"]),),
            ),
        )
    with pytest.raises(ValueError, match="用途"):
        service.preview(
            owner_id="user-a",
            purpose="web_research",
            objective_text="研究当前网页列出的公司",
            output_formats=("markdown",),
            selection=TaskContextSelection(
                memories=(MemorySelection(memory_id=unrelated["id"]),),
            ),
        )


def test_no_selection_does_not_inject_all_owner_memories(tmp_path) -> None:
    service, _repository, store = _service(tmp_path)
    store.memory_add("user-a", "我偏好 CSV", purpose="general")

    preview = service.preview(
        owner_id="user-a",
        purpose="web_research",
        objective_text="研究网页",
        output_formats=("markdown",),
        selection=TaskContextSelection(),
    )

    assert preview.template is None
    assert preview.memories == ()
    assert "我偏好 CSV" not in preview.compiled_context.content


def test_frozen_context_survives_template_drift_and_memory_deletion(tmp_path) -> None:
    service, repository, store = _service(tmp_path)
    repository.save_template(
        "user-a",
        TaskTemplateDraft(
            template_id="company-research",
            version=1,
            title="公司研究摘要",
            source="owner_created",
            purpose="web_research",
            goal_contract_draft="版本一目标",
            delivery_spec_draft={"formats": ["markdown"]},
            method_draft="版本一方法",
        ),
    )
    memory = store.memory_add(
        "user-a",
        "优先引用官网原文",
        purpose="web_research",
    )
    preview = service.preview(
        owner_id="user-a",
        purpose="web_research",
        objective_text="研究网页",
        output_formats=("markdown",),
        selection=TaskContextSelection(
            template=TaskTemplateRef(template_id="company-research", version=1),
            memories=(MemorySelection(memory_id=memory["id"]),),
        ),
    )

    database = repository.database
    with sqlite3.connect(database) as connection:
        service.freeze(
            connection,
            owner_id="user-a",
            task_id="workspace-1",
            revision=1,
            preview=preview,
            expected_preview_sha256=preview.preview_sha256,
        )
    repository.save_template(
        "user-a",
        TaskTemplateDraft(
            template_id="company-research",
            version=2,
            title="公司研究摘要",
            source="owner_created",
            purpose="web_research",
            goal_contract_draft="版本二目标",
            delivery_spec_draft={"formats": ["markdown"]},
            method_draft="版本二方法",
        ),
    )
    assert store.memory_delete("user-a", memory["id"])

    frozen = repository.get_frozen("user-a", "workspace-1", 1)
    assert frozen is not None
    assert frozen.template is not None
    assert frozen.template.version == 1
    assert frozen.proposed_changes.goal_contract == "版本一目标"
    assert frozen.memories[0].summary == "优先引用官网原文"
    assert repository.get_template(
        "user-a", TaskTemplateRef(template_id="company-research", version=2)
    ) is not None

    carried = service.carry_forward(
        owner_id="user-a",
        source_task_id="workspace-1",
        source_revision=1,
        target_task_id="workspace-1",
        target_revision=2,
        objective_text="研究网页，并把结果改为 CSV",
        output_formats=("csv",),
    )
    assert carried is not None
    assert carried.template is not None and carried.template.version == 1
    assert carried.memories[0].summary == "优先引用官网原文"
    assert "研究网页，并把结果改为 CSV" in carried.compiled_context.content


def test_freeze_rejects_stale_preview_hash(tmp_path) -> None:
    service, repository, _store = _service(tmp_path)
    preview = service.preview(
        owner_id="user-a",
        purpose="web_research",
        objective_text="研究网页",
        output_formats=("markdown",),
        selection=TaskContextSelection(),
    )

    with sqlite3.connect(repository.database) as connection:
        with pytest.raises(ValueError, match="预览已变化"):
            service.freeze(
                connection,
                owner_id="user-a",
                task_id="workspace-1",
                revision=1,
                preview=preview,
                expected_preview_sha256="sha256:" + "0" * 64,
            )
