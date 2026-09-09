"""工具合同在运行前冻结；同一 CoreMind 内核不偷偷切换合同或 Pi。"""
from types import SimpleNamespace

import pytest

from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.agentic_runtime.kernel import RuntimeBinding


class Kernel:
    adapter_id = "coremind-runtime"

    def __init__(self, artifact):
        self.runtime_artifact = artifact
        self.binding = None
        self.calls = 0

    async def prepare_binding(self, **values):
        self.calls += 1
        return RuntimeBinding(adapter_id=self.adapter_id, adapter_version="1", runtime_artifact=self.runtime_artifact,
            protocol_version="p", event_schema_version="e", capability_digest="a" * 64,
            external_run_id="new-run", model=values["model"]), SimpleNamespace()

    def frozen_binding(self, *args):
        return self.binding


@pytest.mark.asyncio
async def test_prepare_and_resume_use_frozen_coremind_tool_contract_without_switching():
    plain, tools = Kernel("plain"), Kernel("tools")
    manager = SemanticWorkspaceManager(agent_kernel=plain, capability_agent_kernel=tools)
    values = dict(model_connection_id=None, model_connection_version=None, model="chosen-model")
    binding, _ = await manager.prepare_runtime_binding(**values, capability_tools_enabled=True)
    assert binding.runtime_artifact == "tools" and binding.model == "chosen-model"
    assert plain.calls == 0 and tools.calls == 1
    plain.binding = binding
    assert manager._kernel_for_run("owner", "task", 1) is tools
    await manager.prepare_runtime_binding(**values, expected_binding=binding.model_dump())
    assert tools.calls == 2 and plain.calls == 0
    plain.binding = binding.model_copy(update={"runtime_artifact": "plain"})
    assert manager._kernel_for_run("owner", "old-task", 1) is plain


@pytest.mark.asyncio
async def test_disabled_tool_contract_fails_before_starting_plain_runtime():
    plain = Kernel("plain")
    manager = SemanticWorkspaceManager(agent_kernel=plain)
    with pytest.raises(RuntimeError, match="工具"):
        await manager.prepare_runtime_binding(model_connection_id=None, model_connection_version=None,
            model="chosen-model", capability_tools_enabled=True)
    assert plain.calls == 0


def test_call_rechecks_mount_gate_and_maps_runtime_name_to_frozen_pack(monkeypatch):
    import src.capability_catalog as catalog_module
    import src.capability_adapters.manifest as manifests
    import src.api.semantic_workspace_runtime as runtime
    from tests.test_capability_call_contract import declared_pack

    candidate = declared_pack()
    ref = SimpleNamespace(pack_id=candidate.pack_id, version=candidate.version, digest=candidate.digest)
    selection = SimpleNamespace(pack_refs=(ref,))
    catalog = SimpleNamespace(resolve_selection=lambda *a, **k: selection, resolve_pack=lambda *a: candidate)
    monkeypatch.setattr(catalog_module, 'SqliteCapabilityCatalogRepository', lambda *a: object())
    monkeypatch.setattr(catalog_module, 'CapabilityCatalog', lambda *a: catalog)
    monkeypatch.setattr(runtime, '_resolve_actor_role', lambda *a: 'admin')
    monkeypatch.setattr(manifests, 'load_runtime_manifests', lambda *a: (SimpleNamespace(
        mount_index=1, manifest=SimpleNamespace(name='native-echo', version='1.0.0', kind='node')),))
    manager = SemanticWorkspaceManager(agent_kernel=Kernel('plain'))
    calls = []
    def mounts(*args):
        calls.append(args)
        return ('frozen-mount',)
    manager._coremind_capability_mounts = mounts
    candidate.manifest += (('internal_path', 'C:/private/host'),)
    descriptions = manager._describe_capability_contracts('owner', 'task', 1)
    assert descriptions[0]['parameters_schema']['items']['enum'] == ['sample']
    assert 'C:/private/host' not in str(descriptions)
    manager._validate_capability_call('owner', 'task', 1, 'native-echo', ['sample'], None)
    with pytest.raises(ValueError):
        manager._validate_capability_call('owner', 'task', 1, 'not-selected', ['sample'], None)
    assert calls == [('owner', 'task', 1)] * 3
    def revoked(*args):
        raise PermissionError('治理撤销')
    manager._coremind_capability_mounts = revoked
    with pytest.raises(PermissionError):
        manager._validate_capability_call('owner', 'task', 1, 'native-echo', ['sample'], None)
