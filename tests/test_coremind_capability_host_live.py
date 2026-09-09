# -*- coding: utf-8 -*-
"""显式启用的本机真实 SDK/Worker 与 Docker 合成验收，不调用外网模型。"""
import asyncio
import json
import os
import threading
import subprocess
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter
from src.agentic_runtime.models import RuntimeStatus, PiRuntimeCheckpoint
from src.agentic_runtime.kernel import AgentKernelResultUnknownError
from src.capability_host import CapabilityHost
from src.capability_adapters import load_runtime_manifests
from src.capability_catalog.reuse import validate_reuse_call
from tests.test_coremind_agent_kernel_adapter import _account_execution, _request, _binding, _sse_response, _PassingCandidateService

IMAGE = "mangrove/pi-coding-agent:0.80.10"


def _installed_node_pack(tmp_path):
    # 只写合成运行合同，执行程序是镜像已安装的 Node，不制造临时业务脚本。
    path = tmp_path / "node-pack"
    path.mkdir()
    manifest = dict(schema_version=1, name="node-version", version="22.23.0", kind="node", purpose="读取安装版本",
        entrypoint=dict(program="node", arguments=["--version"]), permissions=["process:child", "network:none"])
    (path / "mangrove-capability.json").write_text(json.dumps(manifest), encoding="utf-8")
    runtime = load_runtime_manifests((path,))[0].manifest
    contract = dict(capability_id="node-version", version="22.23.0", accepts=["json"], produces=["text"], operations=["inspect"],
        deterministic=True, evidence_preserving=True, side_effect="read_only", network="none", resource_class="cpu_small",
        limits=dict(timeout_seconds=30), healthcheck="node-version", parameters_schema={"type":"array", "maxItems":0})
    pack = SimpleNamespace(pack_id="node-version", version="22.23.0", source_provenance=("https://nodejs.org/",),
        manifest=(("license", "MIT"), ("reuse_contract", json.dumps(contract))))
    validate_reuse_call(pack, runtime, [], None)
    return path, pack, runtime


@pytest.mark.skipif(
    os.environ.get("MANGROVE_ISSUE133_LIVE") != "1",
    reason="需显式启用锁定 CoreMind Adapter 纵切面",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "unknown-resume", "returned-resume", "accepted-resume", "cancel"])
async def test_real_worker_invokes_installed_node_through_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import coremind

    # 本验收仅复用已安装镜像；缺失时不得让 docker run 隐式拉取。
    image_check = subprocess.run(("docker", "image", "inspect", IMAGE), capture_output=True, timeout=15)
    assert image_check.returncode == 0, "本机固定镜像不可用，禁止隐式拉取"

    clients = []
    interrupted = False
    original_client = coremind.CoreMindClient

    class RecordingClient(original_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.event_pages = []
            self.snapshots = []
            self.resume_handles = []
            self.resume_before = []

        def start(self):
            result = super().start()
            if self not in clients:
                clients.append(self)
            return result

        def events(self, *args, **kwargs):
            page = super().events(*args, **kwargs)
            self.event_pages.append(page)
            return page

        def query(self, *args, **kwargs):
            result = super().query(*args, **kwargs)
            self.snapshots.append({key: value for key, value in (result.get("projection") or {}).items() if key in {"status", "outcome", "recovery"} or any(label in key.lower() for label in ("sequence", "start", "updated", "cursor"))})
            return result

        def resume_run(self, *args, **kwargs):
            before = super().query(args[0])
            if mode == "accepted-resume":
                recovery = (before.get("projection") or {}).get("recovery") or {}
                assert recovery.get("resumable") is True
                assert recovery.get("requiresHuman") is False
            self.resume_before.append({key: value for key, value in (before.get("projection") or {}).items() if key in {"status", "outcome", "recovery"} or any(label in key.lower() for label in ("sequence", "start", "updated", "cursor"))})
            self.events(args[0], after_sequence=0)
            result = super().resume_run(*args, **kwargs)
            self.resume_handles.append(result)
            return result

        def submit_tool_result(self, *args, **kwargs):
            nonlocal interrupted
            if mode == "returned-resume" and not interrupted:
                # Host 已真实返回且桥已持久化，仅在首次发送 SDK 回执之前注入断连。
                interrupted = True
                raise TimeoutError("合成提交回执前断连")
            return super().submit_tool_result(*args, **kwargs)

        def submit_verification(self, *args, **kwargs):
            nonlocal interrupted
            if mode == "accepted-resume" and not interrupted:
                page = self.events(args[0], after_sequence=0)
                payloads = [event.get("payload") or {} for event in page.get("events", [])]
                committed = {item.get("callId") for item in payloads if item.get("type") == "effect_receipt" and item.get("status") == "committed"}
                assert {"call-read", "call-submit"} <= committed
                assert any(item.get("type") == "step_end" for item in payloads)
                interrupted = True
                # execute step 已稳定，尚未提交宿主验证；只终止本测试持有的 Worker。
                self._process.kill()
                self._process.wait(timeout=5)
                raise TimeoutError("稳定执行步骤后、宿主验证提交前 Worker 异常退出")
            return super().submit_verification(*args, **kwargs)

    monkeypatch.setattr(coremind, "CoreMindClient", RecordingClient)
    requests: list[dict] = []
    candidate_args = {
        "filename": "result.txt",
        "format": "txt",
        "content": "测试来源",
        "description": "来源内容",
        "evidence": [{
            "source": "upload-a",
            "locator": "全文",
            "quote": "测试来源",
        }],
        "result_items": [{
            "result_id": "result-1",
            "label": "测试来源",
            "source": "upload-a",
            "locator": "全文",
            "quote": "测试来源",
        }],
        "result_search_complete": True,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return None

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(json.loads(self.rfile.read(length)))
            index = len(requests)
            if index == 1:
                delta = {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-read",
                        "type": "function",
                        "function": {
                            "name": "mangrove_invoke_capability",
                            "arguments": json.dumps(
                                {"capability": "node-version", "arguments": []},
                                separators=(",", ":"),
                            ),
                        },
                    }],
                }
                finish_reason = "tool_calls"
            elif index == 2:
                delta = {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-submit",
                        "type": "function",
                        "function": {
                            "name": "mangrove_submit_candidate",
                            "arguments": json.dumps(
                                candidate_args,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }],
                }
                finish_reason = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "候选已提交。"}
                finish_reason = "stop"
            body = _sse_response(
                {
                    "id": f"fixture-{index}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "chosen-model",
                    "choices": [{
                        "index": 0,
                        "delta": delta,
                        "finish_reason": None,
                    }],
                },
                {
                    "id": f"fixture-{index}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "chosen-model",
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": finish_reason,
                    }],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                },
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        pack_path, pack, runtime = _installed_node_pack(tmp_path)
        class ObservedHost(CapabilityHost):
            calls = 0
            containers = []
            completed = asyncio.Event()
            release = asyncio.Event()

            async def start(self, request):
                lease = await super().start(request)
                self.containers.append(lease.container_name)
                return lease

            async def invoke(self, lease, payload):
                self.calls += 1
                result = await super().invoke(lease, payload)
                self.completed.set()
                if mode == "unknown-resume":
                    # 真实进程已返回，但模拟传输回执丢失，不伪造执行成功或再次执行。
                    raise TimeoutError("合成回执丢失")
                if mode == "cancel":
                    await self.release.wait()
                return result

        host = ObservedHost(image=IMAGE, execution_root=tmp_path / "hosts")
        service = _PassingCandidateService()
        contract = json.loads(dict(pack.manifest)["reuse_contract"])
        description = {"capability": "node-version", "operations": contract["operations"],
            "input_formats": contract["accepts"], "output_formats": contract["produces"],
            "parameters_schema": contract["parameters_schema"], "tools": []}
        adapter = CoreMindAgentKernelAdapter(
            execution_root=tmp_path / "runs",
            candidate_verifier_factory=lambda _request, _run_id: object(),
            poll_interval_seconds=0.01,
            timeout_seconds=60,
            capability_tools_enabled=True, capability_host=host,
            capability_mount_resolver=lambda *identity: (pack_path,),
            capability_call_validator=lambda owner, task, revision, name, arguments, tool: validate_reuse_call(pack, runtime, arguments, tool),
            capability_contract_describer=lambda owner, task, revision: [description],
        )
        adapter.bind_candidate_verification(service)
        request = _request(tmp_path).model_copy(update={
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        })
        events = []

        async def on_event(event):
            events.append(event)

        await adapter.prepare_manifest()
        binding = _binding(adapter, "cm_run_real_adapter")
        if mode == "unknown-resume":
            with pytest.raises(AgentKernelResultUnknownError):
                await adapter.start(request, binding=binding, on_event=on_event)
            journal = next((tmp_path / "runs").rglob(".capability-call-*.json"))
            assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "pending"
            with pytest.raises(AgentKernelResultUnknownError):
                await adapter.resume(request, binding=binding,
                    checkpoint=PiRuntimeCheckpoint(run_id=binding.external_run_id, workspace_root=journal.parent), on_event=on_event)
            assert host.calls == 1
            assert len(clients) == 2
            assert not adapter._capability_leases
            assert all(client._process.poll() is not None for client in clients)
            return
        if mode == "cancel":
            pending = asyncio.create_task(adapter.start(request, binding=binding, on_event=on_event))
            completed = asyncio.create_task(host.completed.wait())
            try:
                done, _ = await asyncio.wait((completed, pending), timeout=60, return_when=asyncio.FIRST_COMPLETED)
                if pending in done:
                    await pending
                assert completed in done
                await adapter.cancel(request.user_id, request.task_id, request.revision)
                host.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert host.calls == 1
                assert len(requests) == 1
                assert not adapter._capability_leases
                assert all(client._process.poll() is not None for client in clients)
            finally:
                completed.cancel()
                await asyncio.gather(completed, return_exceptions=True)
                host.release.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            return
        if mode in {"returned-resume", "accepted-resume"}:
            with pytest.raises(AgentKernelResultUnknownError):
                await adapter.start(request, binding=binding, on_event=on_event)
            journal = next((tmp_path / "runs").rglob(".capability-call-*.json"))
            assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "returned"
            if mode == "returned-resume":
                with pytest.raises(AgentKernelResultUnknownError):
                    await adapter.resume(request, binding=binding,
                        checkpoint=PiRuntimeCheckpoint(run_id=binding.external_run_id, workspace_root=journal.parent), on_event=on_event)
                assert host.calls == 1
                assert len(clients) == 2
                assert all(client._process.poll() is not None for client in clients)
                return
            result = await adapter.resume(request, binding=binding,
                checkpoint=PiRuntimeCheckpoint(run_id=binding.external_run_id, workspace_root=journal.parent), on_event=on_event)
            assert host.calls == 1
        else:
            result = await adapter.start(request, binding=binding, on_event=on_event)

        assert result.status is RuntimeStatus.CANDIDATE_READY, (
            result.model_dump(mode="json"),
            requests,
            [event.model_dump(mode="json") for event in events],
        )
        assert "v22." in json.dumps(requests[1])
        assert json.dumps([description], ensure_ascii=False) in json.dumps(requests[0], ensure_ascii=False).replace('\\"', '"')
        assert {event.event_type for event in events if event.details.get("tool") == "mangrove_invoke_capability"} >= {"tool.started", "tool.completed"}
        assert not adapter._capability_leases
        assert requests[0].get("tools")
        assert len(requests) == 3
        assert [item.filename for item in result.candidates] == ["result.txt"]
        assert all(item["model"] == "chosen-model" for item in requests)
        assert len(clients) == (2 if mode == "accepted-resume" else 1)
        if mode == "accepted-resume":
            assert any(event.get("eventType") == "fact.resume" for page in clients[1].event_pages for event in page.get("events", []))
        assert all(client._process.poll() is not None for client in clients)
        runtime_payloads = [
            event.get("payload") or {}
            for client in clients
            for page in client.event_pages
            for event in page.get("events", [])
        ]
        observed_types = [payload.get("type") for payload in runtime_payloads]
        assert any(
            payload.get("type") == "checkpoint_created"
            and payload.get("callId") == "call-submit"
            for payload in runtime_payloads
        ), observed_types
        assert any(
            payload.get("type") == "effect_receipt"
            and payload.get("callId") == "call-submit"
            and payload.get("status") == "committed"
            for payload in runtime_payloads
        ), observed_types
        assert not any(
            key.startswith("MANGROVE_COREMIND_RUN_GRANT_")
            for key in os.environ
        )
    finally:
        removed = {}
        for name in host.containers:
            inspection = subprocess.run(("docker", "container", "inspect", name), capture_output=True, timeout=15)
            # 守护进程错误不等于已清理，必须明确返回该容器不存在。
            removed[name] = inspection.returncode != 0 and b"no such" in inspection.stderr.lower()
        evidence = {"mode": mode, "host_calls": host.calls, "container_removed": removed, "worker_errors": [event.get("payload") for client in clients for page in client.event_pages for event in page.get("events", []) if event.get("eventType") == "error"], "worker_closed": [client._process.poll() is not None for client in clients]}
        evidence["recovery"] = [{"handles": client.resume_handles, "snapshots": client.snapshots,
            "before": client.resume_before,
            "event_metadata": [{"sequence": event.get("sequence"), "eventType": event.get("eventType"), "payloadType": (event.get("payload") or {}).get("type"), "status": (event.get("payload") or {}).get("status"), "timestamp": event.get("timestamp")} for page in client.event_pages for event in page.get("events", [])],
            "receipts": [event.get("payload") for page in client.event_pages for event in page.get("events", []) if (event.get("payload") or {}).get("type") == "effect_receipt"]} for client in clients]
        evidence["relay_requests"] = len(requests)
        Path(f".artifacts/issue-133/live-{mode}-events.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert removed and all(removed.values())
