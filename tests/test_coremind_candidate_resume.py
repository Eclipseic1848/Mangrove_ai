"""独立候选宿主的持久恢复身份；默认环境不运行候选代码。"""
import json
import os
from types import SimpleNamespace

import pytest
from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter
from src.agentic_runtime.kernel import AgentKernelCapabilityError

pytestmark = pytest.mark.skipif(os.environ.get("MANGROVE_COREMIND_CANDIDATE_TEST") != "1", reason="仅独立候选")


def test_unknown_resume_reuses_persisted_operation_after_new_client(tmp_path):
    binding = SimpleNamespace(external_run_id="candidate-run", runtime_artifact="fixed-candidate")
    calls = []

    class Client:
        def query(self, run_id):
            assert not calls, "结果未知时不得重新查询并建立新恢复身份"
            return {"runId": run_id, "derivedFromSequence": 7, "projection": {"status": "paused"}}

        def resume_run(self, run_id, **kwargs):
            saved = json.loads((tmp_path / ".mangrove-resume-operation.json").read_text(encoding="utf-8"))
            assert saved["operation_id"] == kwargs["operation_id"]
            assert saved["acknowledged"] is False
            calls.append(kwargs)
            if len(calls) == 1:
                raise OSError("合成响应丢失")
            return {"runId": run_id, "availableControls": []}

    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path)
    with pytest.raises(OSError):
        adapter._resume_operation(Client(), binding, tmp_path)
    replacement = CoreMindAgentKernelAdapter(execution_root=tmp_path)
    assert replacement._resume_operation(Client(), binding, tmp_path)["runId"] == binding.external_run_id
    assert calls[0] == calls[1]
    assert calls[0]["expected_sequence"] == 7


@pytest.mark.parametrize("saved", [{"run_id": "foreign"}, {}, [], None, False, "invalid"])
def test_corrupt_or_foreign_operation_never_sends(tmp_path, saved):
    path = tmp_path / ".mangrove-resume-operation.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path)
    with pytest.raises(AgentKernelCapabilityError):
        adapter._resume_operation(object(), SimpleNamespace(external_run_id="ours", runtime_artifact="fixed"), tmp_path)


def test_real_worker_resume_survives_lost_response_and_new_client():
    import time
    from pathlib import Path
    from unittest.mock import patch
    import coremind
    from tests.test_coremind_worker_contract import CoreMindWorkerContractTests, _worker_environment

    class Probe(CoreMindWorkerContractTests):
        def _probe_controls(self, client, handle, requests):
            run_id = handle["runId"]
            deadline = time.monotonic() + 12
            while client.query(run_id)["projection"]["status"] != "paused":
                assert time.monotonic() < deadline
                time.sleep(0.02)
            root = Path(client._cwd)
            binding = SimpleNamespace(external_run_id=run_id, runtime_artifact="candidate-worker-real")
            adapter = CoreMindAgentKernelAdapter(execution_root=root)
            original = client.resume_run

            def lose_response(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError("合成已应用后的响应丢失")

            with patch.object(client, "resume_run", side_effect=lose_response):
                with pytest.raises(OSError):
                    adapter._resume_operation(client, binding, root)
            pending = json.loads((root / ".mangrove-resume-operation.json").read_text(encoding="utf-8"))
            client.close()
            replacement = coremind.CoreMindClient(client._config, config_dir=root, cwd=root,
                                                  protocol_version="2.0", request_timeout=10)
            try:
                with patch.dict(os.environ, _worker_environment(str(root)), clear=True):
                    replacement.start()
                recovered = CoreMindAgentKernelAdapter(execution_root=root)
                assert recovered._resume_operation(replacement, binding, root)["runId"] == run_id
                saved = json.loads((root / ".mangrove-resume-operation.json").read_text(encoding="utf-8"))
                assert saved["operation_id"] == pending["operation_id"]
                assert saved["acknowledged"] is True
                events = replacement.events(run_id, after_sequence=0, limit=1000)["events"]
                assert len([e for e in events if e["eventType"] == "fact.resume"]) == 1
                assert len(requests) == 1, "恢复不得重复已完成模型调用"
            finally:
                replacement.close()

    Probe()._run_local_model(include_usage=True, host_gate=True, control_probe=True)


def test_candidate_admission_is_internal_and_unknown_required_fact_stays_closed():
    from src.agentic_runtime.coremind_events import project_coremind_event
    event = {"protocolVersion": "2.0", "eventSchemaVersion": 1, "runId": "run-a",
             "sequence": 1, "eventId": "event-a", "timestamp": "2026-09-25T00:00:00Z",
             "ignorable": False, "sensitivity": "local", "eventType": "fact.admission",
             "payload": {"protocolStart": {"fingerprint": "private-identity"}}}
    assert project_coremind_event(event, run_id="run-a", model="fixture") is None
    with pytest.raises(AgentKernelCapabilityError):
        project_coremind_event({**event, "eventType": "fact.future_required"}, run_id="run-a", model="fixture")


def test_policy_denial_is_visible_without_untrusted_reason():
    from src.agentic_runtime.coremind_events import project_coremind_event
    event={"protocolVersion":"2.0","eventSchemaVersion":1,"runId":"run-a","sequence":1,
           "eventId":"event-a","timestamp":"2026-09-25T00:00:00Z","ignorable":False,
           "sensitivity":"local","eventType":"policy_denied",
           "payload":{"type":"policy_denied","tool":"mangrove_submit_candidate","reason":"private-path-secret"}}
    result=project_coremind_event(event,run_id="run-a",model="fixture",tool_names={"mangrove_submit_candidate":"提交候选"})
    assert result.event_type == "runtime.policy_denied"
    assert "private-path-secret" not in result.model_dump_json()
    assert result.details["denied"] is True


def test_runtime_error_is_visible_without_raw_message():
    from src.agentic_runtime.coremind_events import project_coremind_event
    event={"protocolVersion":"2.0","eventSchemaVersion":1,"runId":"run-a","sequence":1,
           "eventId":"event-a","timestamp":"2026-09-25T00:00:00Z","ignorable":False,
           "sensitivity":"local","eventType":"error",
           "payload":{"type":"error","fatal":True,"message":"private-path-secret"}}
    result=project_coremind_event(event,run_id="run-a",model="fixture")
    assert result.event_type=="runtime.error"
    assert result.details["fatal"] is True
    assert "private-path-secret" not in result.model_dump_json()
