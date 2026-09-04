# -*- coding: utf-8 -*-
"""固定 Pi/CoreMind 对同一黄金任务的真实 Runtime 对照。"""
from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest

from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter
from src.agentic_runtime.egress_policy import SmokescreenEgressController
from src.agentic_runtime.kernel import (
    AgentKernelError,
    AgentKernelResultUnknownError,
    PiAgentKernelAdapter,
    RuntimeBinding,
)
from src.agentic_runtime.models import (
    PiRuntimeCheckpoint,
    PiRuntimeRequest,
    RuntimeStatus,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from src.agentic_runtime.pi_runtime import PiRuntime, _resolve_host_ipv4_in_docker


PI_IMAGE = (
    "mangrove/pi-coding-agent:0.80.10@"
    "sha256:a241e5e428195a3f979f84a4ffaa67ad728c4f0cb9fb48839420fe268aaf4316"
)
EGRESS_IMAGE = "mangrove/smokescreen:da4840c9"


def _sse(*chunks: dict) -> bytes:
    return (
        "".join(
            f"data: {json.dumps(chunk, ensure_ascii=False, separators=(',', ':'))}\n\n"
            for chunk in chunks
        )
        + "data: [DONE]\n\n"
    ).encode("utf-8")


def _candidate_arguments() -> dict:
    evidence = {"source": "来源.txt", "locator": "line:1", "quote": "测试来源"}
    return {
        "filename": "result.txt",
        "format": "txt",
        "content": "测试来源\n",
        "description": "逐字返回冻结来源",
        "evidence": [evidence],
        "result_items": [{"result_id": "result-1", "label": "测试来源", **evidence}],
        "result_search_complete": True,
    }


def _pi_command() -> str:
    tool = "python /workspace/work/candidate_manifest_tool.py"
    return " && ".join((
        "grep -F '测试来源' /workspace/input/来源.txt",
        "printf '测试来源\\n' > /workspace/output/result.txt",
        f"{tool} init --filename result.txt --format txt "
        "--description '逐字返回冻结来源'",
        f"{tool} add-evidence --filename result.txt --source 来源.txt "
        "--locator line:1 --quote 测试来源",
        f"{tool} add-result --result-id result-1 --label 测试来源 "
        "--source 来源.txt --locator line:1 --quote 测试来源",
        f"{tool} complete-results",
    ))


class _Provider(BaseHTTPRequestHandler):
    requests: list[dict] = []
    block = False
    started = threading.Event()
    release = threading.Event()

    def log_message(self, *_args) -> None:
        return None

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        self.requests.append(request)
        if self.block:
            self.started.set()
            self.release.wait(timeout=30)
        tools = request.get("tools") or []
        names = {
            item.get("function", {}).get("name")
            for item in tools
            if isinstance(item, dict)
        }
        messages = request.get("messages") or []
        called = {
            call.get("function", {}).get("name")
            for message in messages
            if isinstance(message, dict)
            for call in message.get("tool_calls") or []
            if isinstance(call, dict)
        }
        if "mangrove_read_source" in names and "mangrove_read_source" not in called:
            tool_name, arguments, call_id = (
                "mangrove_read_source",
                {"source_id": "upload-a"},
                "call-read",
            )
        elif "mangrove_submit_candidate" in names and "mangrove_submit_candidate" not in called:
            tool_name, arguments, call_id = (
                "mangrove_submit_candidate",
                _candidate_arguments(),
                "call-submit",
            )
        else:
            bash = next(
                (
                    item.get("function", {})
                    for item in tools
                    if item.get("function", {}).get("name") in {"bash", "shell"}
                ),
                None,
            )
            bash_name = bash.get("name") if bash else None
            if bash_name and bash_name not in called:
                properties = (bash.get("parameters") or {}).get("properties") or {}
                argument_name = next(
                    (name for name in ("command", "cmd") if name in properties),
                    next(iter(properties), "command"),
                )
                tool_name, arguments, call_id = (
                    bash_name,
                    {argument_name: _pi_command()},
                    "call-pi-write",
                )
            else:
                tool_name = None

        if tool_name:
            delta = {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(
                            arguments,
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
        index = len(self.requests)
        body = _sse(
            {
                "id": f"golden-{index}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "golden-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"golden-{index}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "golden-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            # 运行中取消会主动断开正在等待 Provider 的 Worker 请求。
            if not self.block:
                raise


class _PassingVerification:
    async def verify_initial_current(self, **_values):
        report = VerificationReport(
            status=VerificationStatus.PASSED,
            summary="黄金候选通过独立验证",
            checks=(VerificationCheck(code="golden", passed=True, summary="来源与候选一致"),),
            evidence_count=1,
            formal_delivery_eligible=True,
        )
        return SimpleNamespace(
            status=SimpleNamespace(value="passed"),
            report_json=report.model_dump_json(),
        )


def _binding(adapter, run_id: str) -> RuntimeBinding:
    manifest = adapter.manifest
    return RuntimeBinding(
        adapter_id=manifest.adapter_id,
        adapter_version=manifest.adapter_version,
        runtime_artifact=manifest.runtime_artifact,
        protocol_version=manifest.protocol_version,
        event_schema_version=manifest.event_schema_version,
        runtime_version=manifest.runtime_version,
        runtime_protocol_version=manifest.runtime_protocol_version,
        runtime_event_schema_version=manifest.runtime_event_schema_version,
        capability_digest=manifest.digest,
        external_run_id=run_id,
        model="golden-model",
    )


def _canonical(result, events: list) -> dict:
    manifest = json.loads(
        (result.workspace_root / "output" / "candidate-manifest.json").read_text(encoding="utf-8")
    )
    event_types = {event.event_type for event in events}
    return {
        "status": result.status,
        "verification": result.verification.status,
        "candidate": result.candidates[0].host_path.read_text(encoding="utf-8"),
        "evidence": manifest["artifacts"][0]["evidence"],
        "result_items": manifest["result_items"],
        "event_stages": {
            "agent": "agent.started" in event_types,
            "tool": {"tool.started", "tool.completed"} <= event_types,
            "provider": "provider.usage" in event_types,
        },
    }


def _assert_events_bounded(events: list) -> None:
    assert {event.event_type for event in events} <= {
        "runtime.resuming",
        "agent.started",
        "agent.settled",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "provider.usage",
        "verification.completed",
        "candidate.ready",
    }
    projection = json.dumps(
        [event.model_dump(mode="json") for event in events],
        ensure_ascii=False,
    )
    assert "local-runtime" not in projection
    assert "逐字返回来源中的测试内容" not in projection


def _docker_exists(reference: str) -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ("docker", "image", "inspect", reference),
        check=False,
        capture_output=True,
    ).returncode == 0


@pytest.mark.skipif(
    os.environ.get("MANGROVE_RUNTIME_ADAPTER_GOLDEN_TEST") != "1",
    reason="需显式启用固定 Runtime 黄金对照",
)
@pytest.mark.asyncio
async def test_fixed_pi_and_coremind_have_the_same_golden_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _docker_exists(PI_IMAGE) or not _docker_exists(EGRESS_IMAGE):
        pytest.skip("固定 Pi 或 Smokescreen 镜像不存在")
    if shutil.which("node") is None:
        pytest.skip("固定 CoreMind Worker 所需 Node 不存在")
    try:
        import coremind
    except ImportError:
        pytest.skip("固定 CoreMind SDK 不存在")

    provider_host = _resolve_host_ipv4_in_docker("host.docker.internal", image=PI_IMAGE)
    if not provider_host:
        pytest.skip("Docker 无法解析宿主 Provider 地址")
    clients = []
    original_client = coremind.CoreMindClient

    class RecordingClient(original_client):
        def start(self):
            result = super().start()
            if self not in clients:
                clients.append(self)
            return result

    monkeypatch.setattr(coremind, "CoreMindClient", RecordingClient)
    _Provider.requests = []
    _Provider.block = False
    _Provider.started.clear()
    _Provider.release.clear()
    server = ThreadingHTTPServer(("0.0.0.0", 0), _Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = tmp_path / "来源.txt"
        source.write_text("测试来源\n", encoding="utf-8")
        common = {
            "user_id": "golden-user",
            "objective_text": "逐字返回来源中的测试内容",
            "requested_output_formats": ("txt",),
            "sources": ({
                "upload_id": "upload-a",
                "original_name": source.name,
                "host_path": source,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            },),
            "model": "golden-model",
            "api_key": "local-runtime",
        }
        verification = _PassingVerification()
        pi_runtime = PiRuntime(
            image=PI_IMAGE,
            execution_root=tmp_path / "pi",
            timeout_seconds=60,
            egress_controller=SmokescreenEgressController(image=EGRESS_IMAGE),
            configure_as_default_document_broker=False,
        )
        pi = PiAgentKernelAdapter(pi_runtime)
        pi.bind_candidate_verification(verification)
        await pi.prepare_manifest()
        coremind_adapter = CoreMindAgentKernelAdapter(
            execution_root=tmp_path / "coremind",
            candidate_verifier_factory=lambda _request, _run_id: object(),
            poll_interval_seconds=0.01,
            timeout_seconds=30,
        )
        coremind_adapter.bind_candidate_verification(verification)
        await coremind_adapter.prepare_manifest()

        pi_request = PiRuntimeRequest(
            task_id="golden-pi",
            revision=1,
            base_url=f"http://{provider_host[0]}:{server.server_port}/v1",
            **common,
        )
        coremind_request = PiRuntimeRequest(
            task_id="golden-coremind",
            revision=1,
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            **common,
        )
        pi_binding = _binding(pi, "pi_run_1111111111111111")
        coremind_binding = _binding(coremind_adapter, "cm_run_2222222222222222")
        pi_events, coremind_events = [], []

        async def collect_pi(event):
            pi_events.append(event)

        async def collect_coremind(event):
            coremind_events.append(event)

        pi_result = await pi.start(pi_request, binding=pi_binding, on_event=collect_pi)
        coremind_result = await coremind_adapter.start(
            coremind_request,
            binding=coremind_binding,
            on_event=collect_coremind,
        )

        assert _canonical(pi_result, pi_events) == _canonical(coremind_result, coremind_events)
        assert _canonical(pi_result, pi_events) == {
            "status": RuntimeStatus.CANDIDATE_READY,
            "verification": VerificationStatus.PASSED,
            "candidate": "测试来源\n",
            "evidence": [{"source": "来源.txt", "locator": "line:1", "quote": "测试来源"}],
            "result_items": [{
                "result_id": "result-1",
                "label": "测试来源",
                "evidence": [{"source": "来源.txt", "locator": "line:1", "quote": "测试来源"}],
            }],
            "event_stages": {"agent": True, "tool": True, "provider": True},
        }

        cancel_adapter = CoreMindAgentKernelAdapter(
            execution_root=tmp_path / "coremind-cancel",
            candidate_verifier_factory=lambda _request, _run_id: object(),
            poll_interval_seconds=0.01,
            timeout_seconds=30,
        )
        cancel_adapter.bind_candidate_verification(verification)
        cancel_request = coremind_request.model_copy(
            update={"task_id": "golden-coremind-cancel"},
        )
        cancel_binding = _binding(cancel_adapter, "cm_run_3333333333333333")
        _Provider.block = True
        cancel_task = asyncio.create_task(
            cancel_adapter.start(
                cancel_request,
                binding=cancel_binding,
                on_event=lambda _event: asyncio.sleep(0),
            )
        )
        assert await asyncio.to_thread(_Provider.started.wait, 10)
        await asyncio.wait_for(
            cancel_adapter.cancel(
                cancel_request.user_id,
                cancel_request.task_id,
                cancel_request.revision,
            ),
            timeout=10,
        )
        _Provider.release.set()
        try:
            cancelled = await cancel_task
        except Exception as exc:
            assert isinstance(exc, AgentKernelResultUnknownError) or (
                exc.__class__.__name__ == "CoreMindError" and "关闭" in str(exc)
            )
        else:
            assert cancelled.status is RuntimeStatus.CANCELLED
        assert all(client._process.poll() is not None for client in clients)
        _Provider.block = False

        pi_resume_events, coremind_resume_events = [], []

        async def collect_pi_resume(event):
            pi_resume_events.append(event)

        async def collect_coremind_resume(event):
            coremind_resume_events.append(event)

        pi_resumed = await pi.resume(
            pi_request,
            binding=pi_binding,
            checkpoint=PiRuntimeCheckpoint(
                run_id=pi_result.run_id,
                workspace_root=pi_result.workspace_root,
                container_name=pi_result.container_name,
                session_file=pi_result.session_file,
            ),
            on_event=collect_pi_resume,
        )
        coremind_resumed = await coremind_adapter.resume(
            coremind_request,
            binding=coremind_binding,
            checkpoint=PiRuntimeCheckpoint(
                run_id=coremind_result.run_id,
                workspace_root=coremind_result.workspace_root,
            ),
            on_event=collect_coremind_resume,
        )
        assert pi_resumed.status is RuntimeStatus.CANDIDATE_READY
        assert coremind_resumed.status is RuntimeStatus.CANDIDATE_READY, (
            coremind_resumed.model_dump(mode="json")
        )
        pi_resume_contract = _canonical(pi_resumed, pi_resume_events)
        coremind_resume_contract = _canonical(
            coremind_resumed,
            coremind_resume_events,
        )
        pi_resume_contract.pop("event_stages")
        coremind_resume_contract.pop("event_stages")
        assert pi_resume_contract == coremind_resume_contract
        _assert_events_bounded(pi_resume_events)
        _assert_events_bounded(coremind_resume_events)

        for adapter, request, binding, result in (
            (pi, pi_request, pi_binding, pi_result),
            (coremind_adapter, coremind_request, coremind_binding, coremind_result),
        ):
            with pytest.raises(AgentKernelError, match="恢复"):
                await adapter.resume(
                    request,
                    binding=binding,
                    checkpoint=PiRuntimeCheckpoint(
                        run_id="other-run",
                        workspace_root=result.workspace_root,
                    ),
                    on_event=lambda _event: asyncio.sleep(0),
                )
            await adapter.cancel(request.user_id, request.task_id, request.revision)

        assert not pi_runtime._containers
        assert not pi_runtime._egress_leases
        assert all(client._process.poll() is not None for client in clients)
        assert subprocess.run(
            ("docker", "container", "inspect", pi_result.container_name),
            check=False,
            capture_output=True,
        ).returncode != 0
    finally:
        _Provider.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
