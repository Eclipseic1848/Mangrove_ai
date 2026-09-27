"""宿主保留已知故障类型，不依赖面向用户的错误措辞。"""
from types import SimpleNamespace
import asyncio

import pytest

from src.agentic_runtime.models import RuntimeVersion
from src.agentic_runtime.pi_runtime import PiRuntime, PiRuntimeError
from src.agentic_runtime.kernel import AgentKernelResultUnknownError
from src.api import semantic_workspace_runtime as workspace


@pytest.mark.parametrize("kind,expected_code,expected_stage", [
    ("source", "SOURCE_INTEGRITY_FAILED", "inspect"),
    ("runtime", "RUNTIME_IMAGE_INVALID", "execute"),
    ("unknown", "MODEL_OUTCOME_UNKNOWN", "execute"),
    ("unknown_ocr", "MODEL_OUTCOME_UNKNOWN", "execute"),
    ("generic", "PI_RUNTIME_FAILED", "execute"),
])
def test_failure_metadata_survives_host_mapping(monkeypatch, kind, expected_code, expected_stage):
    if kind in {"source", "runtime"}:
        error = PiRuntimeError("措辞可以变化", error_code=expected_code, stage=expected_stage)
    elif kind.startswith("unknown"):
        error = AgentKernelResultUnknownError("扫描 PDF OCR 服务不可用" if kind == "unknown_ocr" else "供应商尚未给出确定结果")
    else:
        error = RuntimeError("普通失败")
        error.error_code = "UNTRUSTED_CODE"
        error.stage = "untrusted"
    updates = []
    repository = SimpleNamespace(
        get=lambda *a: {"runtime_version": RuntimeVersion.PI, "run_id": "run"},
        list_events=lambda *a: [{"event_type": "tool.completed", "details": {"tool": "read_evidence"}}],
        update=lambda *a, **kw: updates.append(kw),
    )
    monkeypatch.setattr(workspace, "AgenticRuntimeRepository", lambda *a: repository)
    monkeypatch.setattr(workspace, "get_store", lambda: SimpleNamespace(
        get_semantic_workspace_task=lambda *a: {"active_revision": 1}))
    failure = workspace.SemanticWorkspaceManager._runtime_failure(
        "owner", "task", str(error), elapsed_ms=42, error=error)
    assert failure["error_code"] == expected_code
    assert failure["stage"] == expected_stage
    assert failure["source_read"] is True
    assert failure["delivery_published"] is False
    assert updates[-1]["status"].value == ("needs_input" if kind.startswith("unknown") else "failed")


@pytest.mark.parametrize("resume", [False, True])
def test_source_integrity_failure_origin_has_stable_code(tmp_path, resume):
    source = tmp_path / "source.txt"
    source.write_text("changed", encoding="utf-8")
    destination = tmp_path / "input"
    destination.mkdir()
    request = SimpleNamespace(sources=(SimpleNamespace(
        original_name="source.txt", host_path=source, sha256="0" * 64),))
    with pytest.raises(PiRuntimeError) as caught:
        if resume:
            PiRuntime._verify_copied_sources(request, destination)
        else:
            PiRuntime._copy_sources(request, destination)
    assert caught.value.error_code == "SOURCE_INTEGRITY_FAILED"
    assert caught.value.stage == "inspect"


@pytest.mark.parametrize("returncode,digest,code", [
    (1, b"", "RUNTIME_IMAGE_UNAVAILABLE"),
    (0, b"not-a-digest", "RUNTIME_IMAGE_INVALID"),
])
def test_image_failure_origin_has_stable_code(monkeypatch, returncode, digest, code):
    async def communicate():
        return digest, b"synthetic"

    async def spawn(*args, **kwargs):
        return SimpleNamespace(returncode=returncode, communicate=communicate)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(PiRuntimeError) as caught:
        asyncio.run(PiRuntime.resolve_runtime_artifact(SimpleNamespace(image="test-image")))
    assert caught.value.error_code == code
