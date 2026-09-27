"""隔离真实交付链上的多轮引用；模型语义替身不冒充在线理解率。"""
import csv
import hashlib
from pathlib import Path

from src.api.auth import get_store
from src.api.routes import semantic_workspace as routes
from src.api import semantic_workspace_runtime as runtime
from src.conversation_steering.models import ContextDelta
from tests.test_workspace_canvas import canvas, execution_owner


def test_published_result_followups_rejection_and_selection_do_not_rerun(canvas, monkeypatch):
    client, owner, task_id, task, _ = canvas
    preview = client.get(f"/api/semantic-workspace/tasks/{task_id}/preview").json()
    store = get_store()
    output_id = preview["output_id"]
    output = store.get_semantic_delivery_output("user-a", output_id)
    path = Path(output["file_path"])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError("解释和拒绝修改不得启动执行")

    monkeypatch.setattr(runtime._manager, "enqueue", forbidden)

    class Rewrite:
        async def rewrite(self, turn, request):
            calls.append((turn, request))
            assert turn.result_context.output_id == output_id
            assert turn.result_context.representation_sha256 == preview["representation"]["sha256"]
            changes = {"intent": "task_refinement", "output_delta": ("csv",)} if len(calls) == 2 else {"intent": "rationale_question"}
            return ContextDelta(delta_id="selected-" + turn.turn_id, owner_id=turn.owner_id,
                task_id=task_id, inherited_revision=1, source_turn_ids=(turn.turn_id,),
                confidence="high", normalized_text="解释结果或提出格式修改", direct_answer="依据所选记录解释", **changes)

    monkeypatch.setattr(routes, "build_context_rewriter", lambda *a, **k: Rewrite())
    for index, (row, text) in enumerate([(1,"解释这一行"),(1,"把交付格式改成CSV"),(2,"先不改格式，解释另一行"),(2,"继续解释，不要重新采集")]):
        selection = {"revision":1, "output_id":output_id, "representation_sha256":preview["representation"]["sha256"], "item_ref":preview["item_refs"][row]}
        body = {"text":text, "result_context":selection}
        headers = {"Idempotency-Key":f"chain-{index}"}
        response = client.post(f"/api/semantic-workspace/tasks/{task_id}/turns", json=body, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["action"] == ("revision_proposal" if index == 1 else "answer_only")
        assert calls[-1][0].result_context.item_ref == selection["item_ref"]
        assert len(calls[-1][1].recent_messages) == index * 2
        if index >= 2:
            assert calls[-1][1].prior_delta is None
        assert client.post(f"/api/semantic-workspace/tasks/{task_id}/turns", json=body, headers=headers).json() == result
        assert len(calls) == index + 1
        if index == 1:
            rejected = client.post(f"/api/semantic-workspace/tasks/{task_id}/revision-proposals/{result['proposal_id']}/reject")
            assert rejected.status_code == 200, rejected.text
        current = store.get_semantic_workspace_task("user-a", task_id)
        assert current["active_revision"] == 1
        assert current["status"] == "completed"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    owner["value"] = "user-b"
    assert client.post(f"/api/semantic-workspace/tasks/{task_id}/turns", json=body).status_code == 404
    assert len(calls) == 4


def test_confirmed_format_revision_reuses_frozen_source_and_preserves_old_delivery(canvas, monkeypatch):
    from src.api.routes import semantic_plans
    from src.collectors.social_media_collector import SocialMediaCollector
    from tests.test_semantic_plan_api import ApiFakeGenerator
    from tests.test_semantic_workspace_api import _wait

    client, _, task_id, task, upload = canvas
    store = get_store()
    old_output = store.get_semantic_delivery_output("user-a", task["delivery"]["outputs"][0]["output_id"])
    old_path = Path(old_output["file_path"])
    before = hashlib.sha256(old_path.read_bytes()).hexdigest()
    preview = client.get(f"/api/semantic-workspace/tasks/{task_id}/preview").json()
    source_ids = []
    collections = []

    async def forbidden(*args, **kwargs):
        collections.append(True)
        raise AssertionError("格式修订应复用冻结来源，不得重新采集")

    class FormatGenerator(ApiFakeGenerator):
        async def generate(self, request, **kwargs):
            source_ids.append(request.artifact_ids)
            draft = await super().generate(request, **kwargs)
            return draft.model_copy(update={"delivery": draft.delivery.model_copy(update={"formats": request.requested_output_formats})})

    class Rewrite:
        async def rewrite(self, turn, request):
            return ContextDelta(delta_id="format-"+turn.turn_id, owner_id=turn.owner_id, task_id=task_id,
                inherited_revision=1, source_turn_ids=(turn.turn_id,), intent="task_refinement", confidence="high",
                normalized_text="仅改为CSV，其余保持", output_delta=("csv",))

    monkeypatch.setattr(SocialMediaCollector, "collect", forbidden)
    monkeypatch.setattr(semantic_plans, "_build_generator", lambda **_: FormatGenerator())
    monkeypatch.setattr(routes, "build_context_rewriter", lambda *a, **k: Rewrite())
    response = client.post(f"/api/semantic-workspace/tasks/{task_id}/turns", json={"text":"仅将格式改为CSV，不重新采集"})
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal_id"]
    assert store.get_semantic_workspace_task("user-a", task_id)["active_revision"] == 1
    applied = client.post(f"/api/semantic-workspace/tasks/{task_id}/revision-proposals/{proposal}/decision", json={"mode":"cancel_now"})
    assert applied.status_code == 202, applied.text
    current = _wait(client, task_id, {"completed", "failed", "needs_input"})
    assert current["status"] == "completed", current.get("error") or current.get("summary")
    assert current["active_revision"] == 2
    assert current["output_formats"] == ["csv"]
    assert source_ids and all(ids == (upload.upload_id,) for ids in source_ids)
    assert collections == []
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == before
    fresh = client.get(f"/api/semantic-workspace/tasks/{task_id}/preview").json()
    assert fresh["output_id"] != preview["output_id"]
    delivered = next(item for item in current["delivery"]["outputs"] if item["output_id"] == fresh["output_id"])
    assert delivered["format"] == "csv"
    actual_output = store.get_semantic_delivery_output("user-a", fresh["output_id"])
    with Path(actual_output["file_path"]).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == preview["columns"]
        actual_rows = list(reader)
    assert actual_rows == [{key: str(value) if value is not None else "" for key, value in row.items() if key in preview["columns"]} for row in preview["rows"]]
    assert fresh["columns"] == preview["columns"]
    assert [[r[c] for c in fresh["columns"]] for r in fresh["rows"]] == [[r[c] for c in preview["columns"]] for r in preview["rows"]]
    stale = {"revision":1,"output_id":preview["output_id"],"representation_sha256":preview["representation"]["sha256"],"item_ref":preview["item_refs"][0]}
    assert client.post(f"/api/semantic-workspace/tasks/{task_id}/turns",json={"text":"解释旧结果","result_context":stale}).status_code == 409
