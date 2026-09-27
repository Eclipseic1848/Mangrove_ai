"""默认镜像升级后，历史冻结运行仍使用获准保留的原镜像。"""
import pytest

from src.agentic_runtime.kernel import AgentKernel, AgentKernelCapabilityError, PiAgentKernelAdapter
from src.agentic_runtime.models import PiRuntimeCheckpoint
from tests.test_agent_kernel import _FakePiRuntimeEngine, _registered_repository, _request


def test_release_defaults_use_only_pi_0871():
    from src.config.settings import Settings

    fields = Settings.model_fields
    assert fields["pi_runtime_image"].default == "mangrove/pi-coding-agent:0.87.1"
    assert fields["pi_capability_host_image"].default == "mangrove/pi-coding-agent:0.87.1"
    assert fields["pi_runtime_resume_images"].default_factory() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_digest", [False, True])
async def test_upgrade_keeps_original_run_image_and_digest(tmp_path, monkeypatch, changed_digest):
    import src.api.semantic_workspace_runtime as workspace

    repository = _registered_repository(tmp_path)
    old_image, new_image = "mangrove/pi:old", "mangrove/pi:new"
    old = _FakePiRuntimeEngine(image=old_image, image_digest="sha256:" + "a" * 64)

    async def sink(_event):
        pass

    result = await AgentKernel(adapter=PiAgentKernelAdapter(old), repository=repository).start(
        _request(tmp_path), on_event=sink
    )
    monkeypatch.setattr(workspace.settings, "pi_runtime_resume_images", [old_image])
    monkeypatch.setattr(workspace.settings, "pi_runtime_image", new_image)
    monkeypatch.setattr(workspace.settings, "coremind_runtime_enabled", False)
    monkeypatch.setattr(workspace.settings, "pi_capability_host_enabled", False)
    monkeypatch.setattr(workspace.settings, "agent_kernel_primary_adapter", "pi-runtime")
    monkeypatch.setattr(workspace, "AgenticRuntimeRepository", lambda _: repository)
    shared_broker = object()
    monkeypatch.setattr(workspace, "get_default_document_tool_broker", lambda: shared_broker)

    def runtime(*, image=None, **kwargs):
        if image is not None:
            assert kwargs["document_tool_broker"] is shared_broker
            assert kwargs["configure_as_default_document_broker"] is False
        digest = "b" if changed_digest and image == old_image else "a"
        return _FakePiRuntimeEngine(image=image or new_image, image_digest="sha256:" + digest * 64)

    monkeypatch.setattr(workspace, "PiRuntime", runtime)
    service = workspace.SemanticWorkspaceManager()
    _, current = await service._kernel().prepare_binding(
        model_connection_id=None, model_connection_version=None, model="local-model"
    )
    assert new_image in current.runtime_artifact
    selected = service._kernel_for_run("user-a", "task-a", 1)
    checkpoint = PiRuntimeCheckpoint(run_id=result.run_id, workspace_root=tmp_path)
    if changed_digest:
        with pytest.raises(AgentKernelCapabilityError, match="RuntimeBinding"):
            await selected.resume(_request(tmp_path), checkpoint=checkpoint, on_event=sink)
    else:
        frozen = selected.frozen_binding("user-a", "task-a", 1)
        prepared, _ = await service.prepare_runtime_binding(
            model_connection_id=frozen.model_connection_id,
            model_connection_version=frozen.model_connection_version,
            model=frozen.model, expected_binding=frozen.model_dump(),
        )
        assert prepared.runtime_artifact == frozen.runtime_artifact
        resumed = await selected.resume(_request(tmp_path), checkpoint=checkpoint, on_event=sink)
        assert resumed.run_id == result.run_id
        assert old_image in selected.runtime_artifact
        assert service._kernel_for_run("user-a", "new-task", 1) is service._kernel()
