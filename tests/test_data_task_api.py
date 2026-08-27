# -*- coding: utf-8 -*-
"""数据准备任务 API 测试（Phase 2 Task 10）。

覆盖：connections/test、preview、create、get、manifest、rerun、跨用户归属。
用 TestClient + dependency_overrides 绕过 JWT，monkeypatch 隔离 upload/db/downloads 目录。
"""
from __future__ import annotations

import io
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.data_prep.artifact_store as as_mod
from src.api.auth import get_current_user
from src.api.routes import data_sources, data_tasks, downloads
from src.config.settings import settings
from src.services.document_extraction import (
    FieldCandidate,
    IntentFieldDraft,
    IntentSpecDraft,
)
from tests.database_migration_helpers import migrated_webui_database


def _make_client(tmp_path: Path, monkeypatch, *, user_id: str = "user-a") -> TestClient:
    monkeypatch.setattr(settings, "data_prep_upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "data_prep_max_upload_bytes", 10 * 1024 * 1024)
    monkeypatch.setattr(settings, "data_prep_max_task_bytes", 500 * 1024 * 1024)
    database = migrated_webui_database(tmp_path / "test.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None  # 重置 WebUIStore 单例，使其用新 db_path
    monkeypatch.setattr(as_mod, "_DEFAULT_ROOT", str(tmp_path / "downloads"))
    app = FastAPI()
    app.include_router(data_sources.router)
    app.include_router(data_tasks.router)
    app.include_router(downloads.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}
    return TestClient(app)


_CSV = b"id,name,value\n1,Alice,10\n2,Bob,20\n3,Carol,30\n4,Dave,40\n"


def _upload(
    client: TestClient,
    content: bytes = _CSV,
    name: str = "data.csv",
    media_type: str = "text/csv",
) -> str:
    resp = client.post(
        "/api/data-sources/uploads",
        files={"file": (name, content, media_type)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["upload_id"]


def test_connection_test_upload(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    resp = client.post(
        "/api/data-sources/connections/test",
        json={"source_type": "upload_file", "upload_id": upload_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reachable"] is True
    assert body["sample"]["size_bytes"] == len(_CSV)


def test_connection_test_other_user_404(tmp_path: Path, monkeypatch):
    client_a = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload_id = _upload(client_a)
    client_b = _make_client(tmp_path, monkeypatch, user_id="user-b")
    resp = client_b.post(
        "/api/data-sources/connections/test",
        json={"source_type": "upload_file", "upload_id": upload_id},
    )
    assert resp.status_code == 404


def test_upload_content_supports_owner_preview_recovery(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload_id = _upload(client)

    response = client.get(f"/api/data-sources/uploads/{upload_id}/content")

    assert response.status_code == 200
    assert response.content == _CSV
    client_b = _make_client(tmp_path, monkeypatch, user_id="user-b")
    assert client_b.get(
        f"/api/data-sources/uploads/{upload_id}/content"
    ).status_code == 404


def test_document_draft_generates_editable_extraction_spec(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client, name="contract.csv")

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            assert kwargs == {
                "provider": "local",
                "model": "Qwen3.6-35B-A3B",
            }

        def draft(self, intent: str):
            assert "付款" in intent
            return IntentSpecDraft(
                objective="提取合同付款安排",
                fields=[
                    IntentFieldDraft(
                        name="付款比例",
                        dtype="string",
                        description="合同约定的付款比例",
                    ),
                ],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    response = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "upload_ids": [upload_id],
            "intent": "提取付款比例",
            "provider": "local",
            "model": "Qwen3.6-35B-A3B",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SPEC_DRAFT"
    assert body["model_selection"] == {
        "provider": "local",
        "model": "Qwen3.6-35B-A3B",
    }
    assert body["extraction_spec"]["fields"][0]["name"] == "付款比例"
    task = client.get(f"/api/data-tasks/{body['task_id']}").json()
    assert task["spec"]["task_type"] == "document_extraction"


def test_document_intent_all_work_selects_exhaustive_record_contract(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client, name="work.docx")

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="搜索张三的工作内容",
                fields=[IntentFieldDraft(
                    name="工作内容",
                    description="张三负责的一项工作",
                )],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    response = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "upload_ids": [upload_id],
            "intent": "搜索张三所有工作内容，并输出成 Excel",
        },
    )

    assert response.status_code == 200, response.text
    contract = response.json()["extraction_spec"]["result_contract"]
    assert contract["shape"] == "records"
    assert contract["cardinality"] == "all"
    assert contract["exhaustive"] is True
    assert contract["renderer"] == "data_grid"
    assert "xlsx" in contract["output_formats"]


def test_document_workspace_removal_persists_without_deleting_upload(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    upload_a = _upload(client, name="a.csv")
    upload_b = _upload(client, name="b.csv")
    saved = client.put(
        "/api/data-tasks/document-workspace",
        json={
            "upload_ids": [upload_a, upload_b],
            "checked_upload_ids": [upload_a],
            "selected_upload_id": upload_a,
        },
    )
    assert saved.status_code == 200, saved.text

    removed = client.put(
        "/api/data-tasks/document-workspace",
        json={
            "upload_ids": [upload_a],
            "checked_upload_ids": [upload_a],
            "selected_upload_id": upload_a,
        },
    )

    assert removed.status_code == 200, removed.text
    workspace = client.get("/api/data-tasks/document-workspace").json()
    assert workspace["upload_ids"] == [upload_a]
    assert workspace["checked_upload_ids"] == [upload_a]
    assert client.get(f"/api/data-sources/uploads/{upload_b}").status_code == 200


def test_document_units_keep_separate_uploads_isolated_by_default(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    upload_a = _upload(client, name="a.docx")
    upload_b = _upload(client, name="b.docx")

    unit_a = client.post(
        "/api/data-tasks/document-units",
        json={
            "unit_type": "single_file",
            "name": "a.docx",
            "upload_ids": [upload_a],
        },
    )
    unit_b = client.post(
        "/api/data-tasks/document-units",
        json={
            "unit_type": "single_file",
            "name": "b.docx",
            "upload_ids": [upload_b],
        },
    )

    assert unit_a.status_code == 200, unit_a.text
    assert unit_b.status_code == 200, unit_b.text
    assert unit_a.json()["unit_id"] != unit_b.json()["unit_id"]

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective=intent,
                fields=[IntentFieldDraft(name="内容", description="文件内容")],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    draft = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "unit_id": unit_b.json()["unit_id"],
            "intent": "只处理文件 B",
        },
    )

    assert draft.status_code == 200, draft.text
    task = client.get(f"/api/data-tasks/{draft.json()['task_id']}").json()
    assert task["spec"]["unit_id"] == unit_b.json()["unit_id"]
    assert task["spec"]["upload_ids"] == [upload_b]


def test_document_file_set_runs_once_with_combined_members(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    upload_a = _upload(client, name="order-a.docx")
    upload_b = _upload(client, name="order-b.docx")
    created = client.post(
        "/api/data-tasks/document-units",
        json={
            "unit_type": "file_set",
            "name": "客户 A 订单集",
            "business_type": "订单",
            "upload_ids": [upload_a, upload_b],
        },
    )
    assert created.status_code == 200, created.text
    unit = created.json()
    assert unit["unit_type"] == "file_set"
    assert [item["upload_id"] for item in unit["members"]] == [
        upload_a,
        upload_b,
    ]

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="合并提取订单",
                fields=[IntentFieldDraft(name="订单号", description="订单唯一编号")],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    draft = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "unit_id": unit["unit_id"],
            "intent": "提取全部订单并合并输出",
        },
    )
    assert draft.status_code == 200, draft.text
    task_id = draft.json()["task_id"]
    task = client.get(f"/api/data-tasks/{task_id}").json()
    assert task["spec"]["upload_ids"] == [upload_a, upload_b]
    runs = client.get(
        f"/api/data-tasks/document-units/{unit['unit_id']}/runs"
    )
    assert runs.status_code == 200, runs.text
    assert [item["task_id"] for item in runs.json()] == [task_id]


def test_document_unit_archive_hides_workspace_entry_but_retains_data(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client, name="order.pdf")
    created = client.post(
        "/api/data-tasks/document-units",
        json={
            "unit_type": "single_file",
            "name": "order.pdf",
            "upload_ids": [upload_id],
        },
    )
    assert created.status_code == 200, created.text
    unit_id = created.json()["unit_id"]
    workspace = client.put(
        "/api/data-tasks/document-workspace",
        json={
            "upload_ids": [upload_id],
            "checked_upload_ids": [],
            "active_unit_id": unit_id,
            "selected_upload_id": upload_id,
        },
    )
    assert workspace.status_code == 200, workspace.text

    removed = client.delete(f"/api/data-tasks/document-units/{unit_id}")

    assert removed.status_code == 200, removed.text
    assert removed.json()["retained_uploads"] is True
    assert removed.json()["retained_history"] is True
    assert client.get("/api/data-tasks/document-units").json() == []
    assert client.get(f"/api/data-sources/uploads/{upload_id}").status_code == 200
    persisted = client.get("/api/data-tasks/document-workspace").json()
    assert persisted["active_unit_id"] is None
    assert persisted["selected_upload_id"] is None


def test_document_scope_revision_keeps_parent_and_updates_file_history(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    upload_a = _upload(client, name="a.csv")
    upload_b = _upload(client, name="b.csv")

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取所有记录",
                fields=[IntentFieldDraft(
                    name="内容",
                    description="每条记录内容",
                )],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    parent = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "upload_ids": [upload_a, upload_b],
            "intent": "提取所有记录",
        },
    ).json()

    revised = client.post(
        f"/api/data-tasks/{parent['task_id']}/scope-revisions",
        json={"upload_ids": [upload_a]},
    )

    assert revised.status_code == 200, revised.text
    child = revised.json()
    assert child["parent_task_id"] == parent["task_id"]
    assert child["revision"] == 2
    assert client.get(f"/api/data-tasks/{parent['task_id']}").json()[
        "spec"
    ]["upload_ids"] == [upload_a, upload_b]
    assert client.get(f"/api/data-tasks/{child['task_id']}").json()[
        "spec"
    ]["upload_ids"] == [upload_a]
    unit_id = client.get(f"/api/data-tasks/{child['task_id']}").json()[
        "spec"
    ]["unit_id"]
    units = client.get("/api/data-tasks/document-units").json()
    assert next(item for item in units if item["unit_id"] == unit_id)[
        "upload_ids"
    ] == [upload_a]
    history_a = client.get(
        f"/api/data-tasks/document-runs/by-upload/{upload_a}"
    ).json()
    history_b = client.get(
        f"/api/data-tasks/document-runs/by-upload/{upload_b}"
    ).json()
    assert [item["task_id"] for item in history_a] == [
        child["task_id"],
        parent["task_id"],
    ]
    assert [item["task_id"] for item in history_b] == [parent["task_id"]]


def test_document_follow_up_revises_same_task(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取付款安排",
                fields=[IntentFieldDraft(
                    name="付款比例",
                    description="合同约定的付款比例",
                )],
            )

        def revise(self, current_spec, intent_messages):
            assert current_spec.fields[0].name == "付款比例"
            assert intent_messages == ["提取付款比例", "再增加付款节点"]
            return IntentSpecDraft(
                objective="提取付款比例和节点",
                fields=[
                    IntentFieldDraft(
                        name="付款比例",
                        description="合同约定的付款比例",
                    ),
                    IntentFieldDraft(
                        name="付款节点",
                        description="触发付款的时间或条件",
                    ),
                ],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    created = client.post(
        "/api/data-tasks/document-drafts",
        json={"upload_ids": [upload_id], "intent": "提取付款比例"},
    ).json()

    revised = client.post(
        f"/api/data-tasks/{created['task_id']}/intent-messages",
        json={"intent": "再增加付款节点"},
    )

    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["task_id"] == created["task_id"]
    assert [item["name"] for item in body["extraction_spec"]["fields"]] == [
        "付款比例",
        "付款节点",
    ]
    task = client.get(f"/api/data-tasks/{created['task_id']}").json()
    assert task["spec"]["intent_messages"] == ["提取付款比例", "再增加付款节点"]


def test_document_model_selection_is_validated_and_persisted(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    seen: list[dict[str, str]] = []

    class FakeCatalog:
        default_provider = "local"

        def list_models(self):
            return {
                "local": ["Qwen3.6-35B-A3B"],
                "deepseek": ["deepseek-chat"],
                "qwen": ["qwen-plus"],
            }

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取订单金额",
                fields=[IntentFieldDraft(
                    name="订单金额",
                    description="订单总金额",
                )],
            )

    monkeypatch.setattr(data_tasks, "get_provider", lambda: FakeCatalog())
    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)

    created = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "upload_ids": [upload_id],
            "intent": "提取订单金额",
            "provider": "deepseek",
            "model": "deepseek-chat",
        },
    )
    assert created.status_code == 200, created.text
    assert seen == [{"provider": "deepseek", "model": "deepseek-chat"}]
    task_id = created.json()["task_id"]

    switched = client.put(
        f"/api/data-tasks/{task_id}/model-selection",
        json={"provider": "qwen", "model": "qwen-plus"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["model_selection"] == {
        "provider": "qwen",
        "model": "qwen-plus",
    }
    task = client.get(f"/api/data-tasks/{task_id}").json()
    assert task["spec"]["model_selection"] == {
        "provider": "qwen",
        "model": "qwen-plus",
    }
    invalid = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "upload_ids": [upload_id],
            "intent": "提取订单金额",
            "provider": "deepseek",
        },
    )
    assert invalid.status_code == 400


def test_document_spec_update_rejects_unowned_scope(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取订单金额",
                fields=[IntentFieldDraft(
                    name="订单金额",
                    description="订单总金额",
                )],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    created = client.post(
        "/api/data-tasks/document-drafts",
        json={"upload_ids": [upload_id], "intent": "提取订单金额"},
    ).json()
    spec = created["extraction_spec"]
    spec["discovery"]["artifact_ids"] = ["foreign-upload"]

    response = client.put(
        f"/api/data-tasks/{created['task_id']}/extraction-spec",
        json=spec,
    )

    assert response.status_code == 400


def test_confirmed_document_task_executes_with_real_evidence(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    pdf = (
        Path(__file__).parent
        / "fixtures/document_golden/contract_01_digital.pdf"
    ).read_bytes()
    upload_id = _upload(
        client,
        pdf,
        "contract.pdf",
        "application/pdf",
    )

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取合同字段",
                fields=[IntentFieldDraft(
                    name="合同字段",
                    description="任意可验证合同字段",
                )],
            )

    class FakeCandidateProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def extract(self, spec, elements):
            element = next(item for item in elements if item.text)
            return [FieldCandidate(
                field_name="合同字段",
                value=element.text,
                quote=element.text,
                element_ids=[element.element_id],
                confidence=0.99,
            )]

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    monkeypatch.setattr(data_tasks, "InstructorQwenCandidateProvider", FakeCandidateProvider)
    created = client.post(
        "/api/data-tasks/document-drafts",
        json={"upload_ids": [upload_id], "intent": "提取合同字段"},
    ).json()
    task_id = created["task_id"]
    ready = client.put(
        f"/api/data-tasks/{task_id}/extraction-spec",
        json=created["extraction_spec"],
    )
    assert ready.status_code == 200, ready.text

    executed = client.post(f"/api/data-tasks/{task_id}/extract")

    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "COMPLETED"
    assert body["artifacts"] == [{
        "artifact_id": body["fields"][0]["evidence_refs"][0]["artifact_id"],
        "upload_id": upload_id,
        "original_name": "contract.pdf",
    }]
    assert body["fields"][0]["status"] == "found"
    assert body["fields"][0]["evidence_refs"][0]["bbox"] is not None
    persisted = client.get(f"/api/data-tasks/{task_id}/extraction-results")
    assert persisted.status_code == 200
    assert persisted.json()["fields"][0]["evidence_refs"][0]["element_id"]
    assert persisted.json()["artifacts"][0]["upload_id"] == upload_id
    manifest = client.get(f"/api/data-tasks/{task_id}/manifest")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["spec_version"] == "3"
    assert manifest.json()["outputs"][0]["path"].endswith(
        "extraction/extracted_fields.jsonl"
    )
    task = client.get(f"/api/data-tasks/{task_id}").json()
    assert task["quality"]["overall"] == "pass"


def test_confirmed_docx_task_executes_with_structural_evidence(
    tmp_path: Path,
    monkeypatch,
):
    """DOCX 必须进入完整抽取链，结构位置可替代不存在的视觉 bbox。"""
    from docx import Document

    document = Document()
    document.add_paragraph("付款条件：验收后 30 日内付款")
    buffer = io.BytesIO()
    document.save(buffer)
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(
        client,
        buffer.getvalue(),
        "contract.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取付款条件",
                fields=[IntentFieldDraft(
                    name="付款条件",
                    description="合同约定的付款条件",
                )],
            )

    class FakeCandidateProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def extract(self, spec, elements):
            element = next(
                item for item in elements if "付款条件" in (item.text or "")
            )
            return [FieldCandidate(
                field_name="付款条件",
                value="验收后 30 日内付款",
                quote=element.text,
                element_ids=[element.element_id],
                confidence=0.99,
            )]

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    monkeypatch.setattr(
        data_tasks,
        "InstructorQwenCandidateProvider",
        FakeCandidateProvider,
    )
    created = client.post(
        "/api/data-tasks/document-drafts",
        json={"upload_ids": [upload_id], "intent": "提取付款条件"},
    ).json()
    task_id = created["task_id"]
    ready = client.put(
        f"/api/data-tasks/{task_id}/extraction-spec",
        json=created["extraction_spec"],
    )
    assert ready.status_code == 200, ready.text

    executed = client.post(f"/api/data-tasks/{task_id}/extract")

    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "COMPLETED"
    evidence = body["fields"][0]["evidence_refs"][0]
    assert evidence["bbox"] is None
    assert evidence["location"] == {
        "kind": "docx_paragraph",
        "paragraph": 1,
    }
    assert evidence["extractor"] == "python-docx"


def test_empty_full_table_result_is_failed_instead_of_completed(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    pdf = (
        Path(__file__).parent
        / "fixtures/document_golden/contract_01_digital.pdf"
    ).read_bytes()
    upload_id = _upload(client, pdf, "contract.pdf", "application/pdf")

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取所有表格",
                fields=[IntentFieldDraft(
                    name="完整表格",
                    description="文档中的全部表格",
                )],
            )

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    created = client.post(
        "/api/data-tasks/document-drafts",
        json={"upload_ids": [upload_id], "intent": "提取所有表格"},
    ).json()
    task_id = created["task_id"]
    client.put(
        f"/api/data-tasks/{task_id}/extraction-spec",
        json=created["extraction_spec"],
    )

    extracted = client.post(f"/api/data-tasks/{task_id}/extract")

    assert extracted.status_code == 200, extracted.text
    assert extracted.json()["status"] == "FAILED"
    persisted = client.get(f"/api/data-tasks/{task_id}/extraction-results")
    assert persisted.status_code == 200
    assert persisted.json()["status"] == "FAILED"
    task = client.get(f"/api/data-tasks/{task_id}").json()
    assert task["quality"]["overall"] == "fail"
    assert task["record_counts"]["extracted_table_rows"] == 0
    manifest = client.get(f"/api/data-tasks/{task_id}/manifest").json()
    assert manifest["outputs"] == []
    assert client.get(
        f"/api/downloads/{task_id}/extraction/document_extraction.xlsx"
    ).status_code == 409
    assert client.put(
        f"/api/data-tasks/{task_id}/extraction-spec",
        json=created["extraction_spec"],
    ).status_code == 409


def test_document_draft_rejects_total_size_over_task_limit(
    tmp_path: Path,
    monkeypatch,
):
    client = _make_client(tmp_path, monkeypatch)
    first = _upload(client, b"a" * 8, "a.txt", "text/plain")
    second = _upload(client, b"b" * 8, "b.txt", "text/plain")
    monkeypatch.setattr(settings, "data_prep_max_task_bytes", 15)

    response = client.post(
        "/api/data-tasks/document-drafts",
        json={
            "upload_ids": [first, second],
            "intent": "提取所有内容",
        },
    )

    assert response.status_code == 413
    assert "单任务文件总大小" in response.json()["detail"]


def test_document_review_decision_updates_result_and_audit(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    pdf = (
        Path(__file__).parent
        / "fixtures/document_golden/contract_01_digital.pdf"
    ).read_bytes()
    upload_id = _upload(client, pdf, "contract.pdf", "application/pdf")

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取待复核合同字段",
                fields=[IntentFieldDraft(
                    name="待复核字段",
                    description="低置信度但有真实证据的字段",
                )],
            )

    class LowConfidenceCandidateProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def extract(self, spec, elements):
            element = next(item for item in elements if item.text)
            return [FieldCandidate(
                field_name="待复核字段",
                value=element.text,
                quote=element.text,
                element_ids=[element.element_id],
                confidence=0.5,
            )]

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    monkeypatch.setattr(
        data_tasks,
        "InstructorQwenCandidateProvider",
        LowConfidenceCandidateProvider,
    )
    created = client.post(
        "/api/data-tasks/document-drafts",
        json={"upload_ids": [upload_id], "intent": "提取待复核字段"},
    ).json()
    task_id = created["task_id"]
    client.put(
        f"/api/data-tasks/{task_id}/extraction-spec",
        json=created["extraction_spec"],
    )
    extracted = client.post(f"/api/data-tasks/{task_id}/extract").json()
    assert extracted["status"] == "NEEDS_REVIEW"
    review_id = extracted["review_tasks"][0]["task_id"]

    decided = client.post(
        f"/api/data-tasks/{task_id}/review-decisions/{review_id}",
        json={"decision": "accept_candidate", "candidate_index": 0},
    )

    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "COMPLETED"
    assert body["fields"][0]["status"] == "found"
    assert body["fields"][0]["evidence_refs"]
    assert body["review_tasks"][0]["status"] == "resolved"
    assert body["review_decisions"][0]["user_id"] == "user-a"
    persisted = client.get(f"/api/data-tasks/{task_id}/extraction-results").json()
    assert persisted["review_decisions"][0]["decision"] == "accept_candidate"
    manifest = client.get(f"/api/data-tasks/{task_id}/manifest").json()
    assert "review_decisions" in {
        item["kind"] for item in manifest["artifacts"]
    }
    assert client.post(
        f"/api/data-tasks/{task_id}/review-decisions/{review_id}",
        json={"decision": "mark_not_found"},
    ).status_code == 409


def test_document_record_review_updates_only_target_row(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    pdf = (
        Path(__file__).parent
        / "fixtures/document_golden/contract_01_digital.pdf"
    ).read_bytes()
    upload_id = _upload(client, pdf, "contract.pdf", "application/pdf")

    class FakeIntentProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def draft(self, intent: str):
            return IntentSpecDraft(
                objective="提取全部条款记录",
                fields=[IntentFieldDraft(
                    name="条款",
                    description="一条合同条款",
                )],
            )

    class RecordCandidateProvider:
        def __init__(self, **kwargs):
            self.selection = kwargs

        def extract(self, spec, elements):
            text_elements = [item for item in elements if item.text]
            return [
                FieldCandidate(
                    field_name="条款",
                    value=text_elements[0].text,
                    quote=text_elements[0].text,
                    element_ids=[text_elements[0].element_id],
                    confidence=0.5,
                    record_id="row-1",
                ),
                FieldCandidate(
                    field_name="条款",
                    value=text_elements[1].text,
                    quote=text_elements[1].text,
                    element_ids=[text_elements[1].element_id],
                    confidence=0.99,
                    record_id="row-2",
                ),
            ]

    monkeypatch.setattr(data_tasks, "InstructorQwenIntentProvider", FakeIntentProvider)
    monkeypatch.setattr(
        data_tasks,
        "InstructorQwenCandidateProvider",
        RecordCandidateProvider,
    )
    created = client.post(
        "/api/data-tasks/document-drafts",
        json={"upload_ids": [upload_id], "intent": "提取所有条款记录"},
    ).json()
    task_id = created["task_id"]
    client.put(
        f"/api/data-tasks/{task_id}/extraction-spec",
        json=created["extraction_spec"],
    )
    extracted = client.post(f"/api/data-tasks/{task_id}/extract").json()
    assert len(extracted["records"]) == 2
    assert extracted["status"] == "NEEDS_REVIEW"
    review = extracted["review_tasks"][0]
    target_record_id = review["record_id"]

    decided = client.post(
        f"/api/data-tasks/{task_id}/review-decisions/{review['task_id']}",
        json={"decision": "accept_candidate", "candidate_index": 0},
    )

    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "COMPLETED"
    target = next(
        item for item in body["records"]
        if item["record_id"] == target_record_id
    )
    assert target["status"] == "found"
    assert target["review_required"] is False
    assert len(body["records"]) == 2


def test_preview_returns_bounded_sample_and_schema(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    resp = client.post(
        "/api/data-tasks/preview",
        json={"source": {"upload_id": upload_id}, "sample_records": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["sample"]) <= 3
    assert body["schema"]["fields"]
    assert body["estimated_records"] >= 4
    assert body["probe"]["reachable"] is True
    assert body["high_impact_rules"] == []  # 默认 Recipe 无高影响规则


def test_preview_other_user_404(tmp_path: Path, monkeypatch):
    client_a = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload_id = _upload(client_a)
    client_b = _make_client(tmp_path, monkeypatch, user_id="user-b")
    resp = client_b.post(
        "/api/data-tasks/preview",
        json={"source": {"upload_id": upload_id}},
    )
    assert resp.status_code == 404


def test_create_task_from_upload_succeeds(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    resp = client.post(
        "/api/data-tasks",
        json={
            "source": {"upload_id": upload_id},
            "intent": "CSV 数据准备",
            "outputs": ["jsonl"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"), body
    assert body["record_counts"]["clean"] > 0
    assert body["manifest_path"]
    assert body["user_id"] == "user-a"


def test_get_task_returns_status(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    create = client.post(
        "/api/data-tasks",
        json={"source": {"upload_id": upload_id}, "outputs": ["jsonl"]},
    )
    task_id = create.json()["task_id"]
    resp = client.get(f"/api/data-tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == task_id


def test_get_manifest(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    create = client.post(
        "/api/data-tasks",
        json={"source": {"upload_id": upload_id}, "outputs": ["jsonl"]},
    )
    task_id = create.json()["task_id"]
    resp = client.get(f"/api/data-tasks/{task_id}/manifest")
    assert resp.status_code == 200, resp.text
    manifest = resp.json()
    assert manifest["task_id"] == task_id
    assert manifest["outputs"]


def test_other_user_cannot_access_task(tmp_path: Path, monkeypatch):
    client_a = _make_client(tmp_path, monkeypatch, user_id="user-a")
    upload_id = _upload(client_a)
    create = client_a.post(
        "/api/data-tasks",
        json={"source": {"upload_id": upload_id}, "outputs": ["jsonl"]},
    )
    task_id = create.json()["task_id"]
    client_b = _make_client(tmp_path, monkeypatch, user_id="user-b")
    assert client_b.get(f"/api/data-tasks/{task_id}").status_code == 404
    assert client_b.get(f"/api/data-tasks/{task_id}/manifest").status_code == 404


def test_rerun_task_succeeds(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    create = client.post(
        "/api/data-tasks",
        json={"source": {"upload_id": upload_id}, "outputs": ["jsonl"]},
    )
    task_id = create.json()["task_id"]
    resp = client.post(f"/api/data-tasks/{task_id}/rerun")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] in ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS")


def test_list_tasks_returns_latest_task_with_quality(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    created = client.post(
        "/api/data-tasks",
        json={"source": {"upload_id": upload_id}, "outputs": ["jsonl"]},
    ).json()

    resp = client.get("/api/data-tasks")

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["task_id"] == created["task_id"]
    assert resp.json()[0]["quality"] is not None


def test_data_task_artifact_download_uses_data_task_ownership(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    upload_id = _upload(client)
    created = client.post(
        "/api/data-tasks",
        json={"source": {"upload_id": upload_id}, "outputs": ["jsonl"]},
    ).json()

    resp = client.get(f"/api/downloads/{created['task_id']}/manifest.json")

    assert resp.status_code == 200, resp.text
    assert resp.json()["task_id"] == created["task_id"]


def test_http_preview_spec_fetches_only_first_page():
    source = data_tasks.PreviewSourceIn.model_validate({
        "source_type": "http_api",
        "url": "https://api.example.com/items",
        "pagination": {
            "strategy": "page",
            "options": {"per_page": 2, "start_page": 3, "max_pages": 10},
        },
    })
    spec = data_tasks._source_spec(source, "user-a")

    preview_spec = data_tasks._http_preview_spec(spec)

    assert "pagination" not in preview_spec.options
    assert preview_spec.options["params"] == {"page": 3, "per_page": 2}
    assert spec.options["pagination"]["options"]["max_pages"] == 10


def test_http_task_rejects_unsupported_pagination_strategy(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post(
        "/api/data-tasks",
        json={
            "source": {
                "source_type": "http_api",
                "url": "https://api.example.com/items",
                "pagination": {"strategy": "cursor", "options": {}},
            },
        },
    )
    assert resp.status_code == 422


def test_create_http_task_builds_safe_source_spec(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    captured = {}

    async def fake_execute(spec, task_id):
        captured["spec"] = spec
        return {
            "task_id": task_id,
            "user_id": "user-a",
            "status": "SUCCEEDED",
            "record_counts": {"parsed": 4, "clean": 4},
            "quality": None,
            "manifest_path": None,
            "error": None,
        }

    monkeypatch.setattr(data_tasks, "_execute_task", fake_execute)
    resp = client.post(
        "/api/data-tasks",
        json={
            "source": {
                "source_type": "http_api",
                "url": "https://api.example.com/items",
                "pagination": {
                    "strategy": "page",
                    "options": {"page_param": "page", "per_page": 2, "max_pages": 3},
                },
            },
            "outputs": ["jsonl"],
        },
    )

    assert resp.status_code == 200, resp.text
    source = captured["spec"].sources[0]
    assert source.source_type.value == "http_api"
    assert source.locator == "https://api.example.com/items"
    assert source.options["pagination"]["strategy"] == "page"
    assert "headers" not in source.options
