"""正式交付教训反馈通过公开回执及库读取验证，全部使用隔离数据。"""
import sqlite3
import time
from contextlib import closing

import yaml
import pytest

from src.agentic_runtime.models import RuntimeTaskConfig, VerificationCheck, VerificationReport, VerificationStatus
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.conversation_steering.context import ContextCompiler
from src.conversation_steering.models import ContextCompileRequest, ReferencedContextSummary
from src.delivery_publishing.models import canonical_hash
from src.memory import lessons
from src.memory._library_scope import content_digest
from src.memory.learning_receipts import LearningReceipts, LESSON_USE_KINDS
from src.task_context import TaskContextPreview, FrozenLessonRef, ProposedContextChanges
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.parametrize("explicit", [False, True])
def test_task_api_delivery_records_frozen_lesson_effect_once(tmp_path, monkeypatch, explicit):
    from src.config.settings import settings
    from src.task_context import TaskContextRepository, TaskTemplateDraft
    from tests.test_pi_runtime_workspace_api import FakePiRuntime, _client, _uploads, _wait_for_delivery

    class LessonRuntime(FakePiRuntime):
        def _verification_report(self):
            request = self.requests[-1]
            frozen = TaskContextRepository(settings.webui_db_path).get_frozen(
                request.user_id, request.task_id, request.revision)
            assert len(frozen.lessons) == 1
            ref = frozen.lessons[0]
            report = super()._verification_report()
            return report.model_copy(update={"checks": (*report.checks, VerificationCheck(
                code=f"lesson_effect:lesson:{ref.slug}:{ref.content_digest}", passed=True,
                summary="模拟独立核验已采用金额核对建议"))})

    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=LessonRuntime())
    root = tmp_path / "lessons"
    root.mkdir()
    monkeypatch.setattr(lessons, "LESSONS_DIR", root)
    (root / "amount.md").write_text("---\n" + yaml.safe_dump(dict(
        owner_id="user-a", scope="owner", title="费用核对", data_type="workspace_document",
        keywords=["费用汇总"], status="draft", occurrences=2, helped_avoid=0), allow_unicode=True)
        + "---\n逐项核对费用金额，缺失值不得猜测。", encoding="utf-8")
    document, _ = _uploads(tmp_path)
    with client:
        payload = {
            "objective_text": "费用汇总，不猜测缺失项", "upload_ids": [document],
            "output_formats": ["json"], "runtime_version": "pi"}
        if explicit:
            TaskContextRepository(settings.webui_db_path).save_template("user-a", TaskTemplateDraft(
                template_id="amount-method", version=1, title="指定费用方法", source="owner_created",
                purpose="data_prep", method_draft="按人名整理费用", goal_contract_draft="保留明细"))
            selection = {"template": {"template_id": "amount-method", "version": 1}}
            preview = client.post("/api/semantic-workspace/context-preview", json={
                "objective_text": payload["objective_text"], "purpose": "data_prep",
                "output_formats": ["json"], "selection": selection})
            assert preview.status_code == 200, preview.text
            payload.update(context_purpose="data_prep", context_selection=selection,
                context_preview_sha256=preview.json()["preview_sha256"])
        response = client.post("/api/semantic-workspace/tasks", json=payload)
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        detail = _wait_for_delivery(client, task_id)
        if explicit:
            assert detail["task_context"]["template"]["template_id"] == "amount-method"
            assert detail["task_context"]["automatically_selected"] is False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if lessons.load_lessons(owner_id="user-a")[0]["helped_avoid"] == 1:
                break
            time.sleep(0.05)
        for _ in range(3):
            assert client.get(f"/api/semantic-workspace/tasks/{task_id}").status_code == 200
        entry = lessons.load_lessons(owner_id="user-a")[0]
        assert entry["helped_avoid"] == 1 and entry["status"] == "active"
        assert lessons.load_lessons(owner_id="user-b") == []


def seed_feedback(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "webui.db")
    root = tmp_path / "lessons"
    root.mkdir()
    monkeypatch.setattr(lessons, "LESSONS_DIR", root)
    refs = []
    for index in range(3):
        entry = dict(owner_id="owner-a", scope="owner", title=f"金额核对{index}",
            data_type="workspace_table", keywords=["金额"], status="draft", occurrences=2,
            helped_avoid=0, body="逐人核对金额，不能混淆。")
        (root / f"lesson-{index}.md").write_text("---\n" + yaml.safe_dump(
            {key: value for key, value in entry.items() if key != "body"}, allow_unicode=True)
            + "---\n" + entry["body"], encoding="utf-8")
        refs.append(FrozenLessonRef(slug=f"lesson-{index}", title=entry["title"],
            data_type=entry["data_type"], advice=entry["body"], content_digest=content_digest(entry)))
    source_refs = [f"lesson:{ref.slug}:{ref.content_digest}" for ref in refs]
    context = ContextCompiler().compile(ContextCompileRequest(owner_id="owner-a", task_id="task-a",
        revision=1, goal_contract="核对金额", system_boundaries=("只读来源",), max_chars=12000,
        lesson_summaries=tuple(ReferencedContextSummary(source_ref=ref, summary="逐人核对金额，不能混淆。") for ref in source_refs)))
    snapshot = TaskContextPreview(owner_id="owner-a", purpose="data_prep", objective_text="核对金额",
        output_formats=("csv",), lessons=tuple(refs), proposed_changes=ProposedContextChanges(),
        compiled_context=context, preview_sha256="sha256:" + "a" * 64)
    report = VerificationReport(status=VerificationStatus.PASSED, summary="独立核验通过", evidence_count=1,
        checks=tuple(VerificationCheck(code="lesson_effect:" + ref, passed=True, summary="候选及来源的人名金额一致") for ref in source_refs))
    runtime = AgenticRuntimeRepository(database)
    runtime.register(RuntimeTaskConfig(user_id="owner-a", task_id="task-a", revision=1, run_id="run-a"))
    runtime.update("owner-a", "task-a", 1, verification=report, candidates=())
    candidate_hash = runtime.get("owner-a", "task-a", 1)["verified_candidate_set_hash"]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO task_revision_contexts VALUES (?,?,?,?,?,?,?)",
            ("owner-a", "task-a", 1, snapshot.preview_sha256, snapshot.model_dump_json(), context.model_dump_json(), "2026-09-18"))
        connection.execute("INSERT INTO formal_delivery_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("delivery-a", "publication-a", "run-a", "owner-a", "task-a", 1, candidate_hash,
             "verification-a", canonical_hash(report.model_dump(mode="json")), "d" * 64, "succeeded", "{}", "unused", "2026-09-18"))
    key = dict(owner_id="owner-a", task_id="task-a", revision=1, run_id="run-a")
    receipts = LearningReceipts(database, root)
    for kind in LESSON_USE_KINDS:
        receipts.enqueue(**key, kind=kind)
    return database, key, receipts


def test_verified_delivery_counts_each_lesson_once_and_promotes_draft(tmp_path, monkeypatch):
    from src.memory.workspace_learning import record_verified_lesson_uses
    database, key, receipts = seed_feedback(tmp_path, monkeypatch)
    for _ in range(2):
        record_verified_lesson_uses(database, **key, delivery_id="delivery-a")
    entries = lessons.load_lessons(owner_id="owner-a")
    assert len(entries) == 3
    assert [(entry["helped_avoid"], entry["status"]) for entry in entries] == [(1, "active")] * 3
    assert lessons.load_lessons(owner_id="owner-b") == []
    assert all(receipts.get(**key, kind=kind)["state"] == "applied" for kind in LESSON_USE_KINDS)


@pytest.mark.parametrize("case", ["no_delivery", "wrong_owner", "wrong_revision", "wrong_run",
    "no_evidence", "duplicate_evidence", "changed", "deleted", "retired", "other_owner"])
def test_missing_delivery_evidence_or_changed_lesson_never_counts(tmp_path, monkeypatch, case):
    from src.memory.workspace_learning import record_verified_lesson_uses
    database, key, _ = seed_feedback(tmp_path, monkeypatch)
    if case == "no_delivery":
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE formal_delivery_runs SET status='failed'")
    elif case.startswith("wrong_"):
        field = {"wrong_owner": "owner_id", "wrong_revision": "revision", "wrong_run": "run_id"}[case]
        key[field] = 2 if field == "revision" else "other"
    elif case in {"no_evidence", "duplicate_evidence"}:
        runtime = AgenticRuntimeRepository(database)
        previous = runtime.get("owner-a", "task-a", 1)["verification"]
        report = previous.model_copy(update={"checks": () if case == "no_evidence" else previous.checks * 2})
        runtime.update("owner-a", "task-a", 1, verification=report)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE formal_delivery_runs SET verification_report_hash=?",
                (canonical_hash(report.model_dump(mode="json")),))
    else:
        for path in lessons.LESSONS_DIR.glob("*.md"):
            if case == "deleted":
                path.unlink()
            else:
                text = path.read_text(encoding="utf-8")
                text = text.replace("逐人核对金额", "改写后的建议") if case == "changed" else text.replace(
                    "status: draft", "status: retired") if case == "retired" else text.replace("owner-a", "owner-b")
                path.write_text(text, encoding="utf-8")
    record_verified_lesson_uses(database, **key, delivery_id="delivery-a")
    assert all(item["helped_avoid"] == 0 for owner in ("owner-a", "owner-b") for item in lessons.load_lessons(owner_id=owner))


@pytest.mark.parametrize("after_write", [False, True])
def test_interrupted_feedback_replays_once_without_resurrecting_deleted_lesson(tmp_path, monkeypatch, after_write):
    from src.memory import learning_receipts
    from src.memory.workspace_learning import record_verified_lesson_uses, pending_template_uses
    database, key, receipts = seed_feedback(tmp_path, monkeypatch)
    original_write = learning_receipts.atomic_write

    def interrupted(path, content):
        if after_write:
            original_write(path, content)
        raise OSError("模拟回执提交前中断")

    with monkeypatch.context() as patch:
        patch.setattr(learning_receipts, "atomic_write", interrupted)
        with pytest.raises(OSError):
            record_verified_lesson_uses(database, **key, delivery_id="delivery-a")
    assert receipts.get(**key, kind="lesson_use_1")["state"] == "pending"
    assert pending_template_uses(database)[0]["delivery_id"] == "delivery-a"
    if not after_write:
        (lessons.LESSONS_DIR / "lesson-0.md").unlink()
    for _ in range(2):
        record_verified_lesson_uses(database, **key, delivery_id="delivery-a")
    assert receipts.get(**key, kind="lesson_use_1")["state"] == ("applied" if after_write else "conflict")
    entries = lessons.load_lessons(owner_id="owner-a")
    assert len(entries) == (3 if after_write else 2)
    assert all(item["helped_avoid"] == 1 for item in entries)
