"""原样初稿在宿主确定性生成，不启动模型；仍使用已有冻结和确认边界。"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from src.agentic_runtime.models import PiRuntimeRequest, PiRuntimeCheckpoint, PiRuntimeResult, RuntimeStatus, SourceInput
from src.agentic_runtime.pi_runtime import PiRuntime, PiRuntimeError
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.agentic_runtime.draft_snapshot import host_copy_pending
from tests.database_migration_helpers import migrated_webui_database


def test_copy_draft_uses_no_model_or_container_and_keeps_owner_gate(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text('{"观察":"未核实"}', encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    request = PiRuntimeRequest(user_id="owner", task_id="task", revision=1,
        objective_text="原样整理为初稿", requested_output_formats=("json",),
        expected_sha256_by_format={"json": digest}, sources=(SourceInput(upload_id="source",
            original_name="source.json", host_path=source, sha256=digest),),
        model="test", base_url="http://127.0.0.1:1/v1", api_key="test")
    runtime = PiRuntime(execution_root=tmp_path / "runs",
        state_store=AgenticRuntimeRepository(migrated_webui_database(tmp_path / "test.db")))
    async def forbidden(*args, **kwargs):
        raise AssertionError("确定性初稿不应启动容器或模型")
    monkeypatch.setattr(runtime, "_assert_image", forbidden)
    monkeypatch.setattr(runtime, "_run_rpc", forbidden)
    events = []
    async def emit(event):
        events.append(event)
    result = asyncio.run(runtime._start(request, on_event=emit))
    assert result.status == RuntimeStatus.NEEDS_INPUT
    assert result.session_file is None and result.verification is None
    root = result.workspace_root
    assert (root / "output/data.json").read_bytes() == source.read_bytes()
    assert [event.event_type for event in events] == ["runtime.preparing", "draft.ready"]
    assert host_copy_pending(root, owner_id="owner", task_id="task", revision=1, run_id=result.run_id)
    with pytest.raises(ValueError):
        host_copy_pending(root, owner_id="different-owner", task_id="task", revision=1, run_id=result.run_id)
    marker = root / "host-state/host-copy-pending.json"
    original = marker.read_bytes()
    marker.rename(marker.with_name("host-copy-consumed.json"))
    (root / "work/host-copy-pending.json").write_bytes(original)
    marker.write_bytes(original)
    assert not host_copy_pending(root, owner_id="owner", task_id="task", revision=1, run_id=result.run_id)


def test_explicit_review_starts_first_model_on_same_run_and_consumes_host_state(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text('{"value":1}', encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    request = PiRuntimeRequest(user_id="owner", task_id="task", revision=1, objective_text="原样初稿",
        requested_output_formats=("json",), expected_sha256_by_format={"json": digest},
        sources=(SourceInput(upload_id="source", original_name="source.json", host_path=source, sha256=digest),),
        model="test", base_url="http://127.0.0.1:6012/v1", api_key="test")
    runtime = PiRuntime(execution_root=tmp_path / "runs",
        state_store=AgenticRuntimeRepository(migrated_webui_database(tmp_path / "test.db")),
        draft_review_required=lambda _: True)
    calls = []
    async def noop(*args, **kwargs):
        pass
    async def egress(**kwargs):
        calls.append("egress")
        return SimpleNamespace(network_name="synthetic", proxy_url="http://synthetic:4750")
    async def execute(req, **kwargs):
        calls.append("model")
        assert kwargs["run_id"] == first.run_id
        assert "--session" not in kwargs["command"]
        assert "host-state" not in " ".join(kwargs["command"])
        assert not host_copy_pending(first.workspace_root, owner_id="owner", task_id="task", revision=1, run_id=first.run_id)
        return PiRuntimeResult(status=RuntimeStatus.NEEDS_INPUT, run_id=kwargs["run_id"],
                               workspace_root=kwargs["root"], summary="合成模型核对")
    monkeypatch.setattr(runtime, "_assert_image", noop)
    monkeypatch.setattr(runtime.egress_controller, "start", egress)
    monkeypatch.setattr(runtime, "_release_supporting_resources", noop)
    monkeypatch.setattr(runtime, "_execute_run", execute)
    first = asyncio.run(runtime._start(request, on_event=noop))
    checkpoint = PiRuntimeCheckpoint(run_id=first.run_id, workspace_root=first.workspace_root)
    waiting = asyncio.run(runtime._resume(request, checkpoint=checkpoint, on_event=noop))
    assert waiting.status == RuntimeStatus.NEEDS_INPUT and calls == []
    runtime._draft_review_required = lambda _: False
    continued = asyncio.run(runtime._resume(request, checkpoint=checkpoint, on_event=noop))
    assert continued.run_id == first.run_id and calls == ["egress", "model"]
    marker = first.workspace_root / "host-state/host-copy-consumed.json"
    (first.workspace_root / "work/host-copy-pending.json").write_bytes(marker.read_bytes())
    with pytest.raises(PiRuntimeError, match="会话已丢失"):
        asyncio.run(runtime._resume(request, checkpoint=checkpoint, on_event=noop))
    assert calls == ["egress", "model"]


def test_mixed_sources_advertise_supported_reader_without_widening_grants(tmp_path):
    from src.agentic_runtime.document_tools import DocumentToolGrant
    sources = []
    for suffix in ("json", "xlsx", "pdf", "png"):
        path = tmp_path / ("source." + suffix)
        path.write_bytes(b"synthetic")
        sources.append(SourceInput(upload_id=suffix, original_name=path.name, host_path=path,
                                   sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    request = PiRuntimeRequest(user_id="owner", task_id="task", revision=1, objective_text="核对混合来源",
        sources=tuple(sources), requested_output_formats=("json",),
        model="test", base_url="http://127.0.0.1:6012/v1", api_key="test")
    grant = DocumentToolGrant(grant_id="synthetic", token="t" * 32, owner_user_id="owner", task_id="task",
        revision=1, run_id="run", owner_binding="o" * 16, expires_at=datetime.now(timezone.utc))
    config, work = tmp_path / "config", tmp_path / "work"
    config.mkdir()
    work.mkdir()
    PiRuntime._write_runtime_files(request, source_names=tuple(s.original_name for s in sources),
        config_dir=config, work_dir=work, document_grant=grant, document_relay_base_url="http://127.0.0.1:8088/tools")
    goal = json.loads((work / "goal.json").read_text(encoding="utf-8"))
    assert {s["source_id"]: s["reader"] for s in goal["sources"]} == {
        "json": "local_file_tools", "xlsx": "local_file_tools", "pdf": "document_tools", "png": "document_tools"}
