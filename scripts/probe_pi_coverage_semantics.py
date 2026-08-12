# -*- coding: utf-8 -*-
"""用固定 Pi 镜像重复验证覆盖语义和文档能力自主调用。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time

from fastapi import FastAPI
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agentic_runtime.document_tools import (
    DocumentToolBroker,
    configure_default_document_tool_broker,
)
from src.agentic_runtime.models import SourceInput
from src.api.routes import document_tools
from src.config.settings import settings


class SemanticFixtureRetriever:
    """固定语料只模拟能力结果，不替 Pi 决定覆盖基数。"""

    _pages = {
        1: "TARGET 报销记录 A，姓名张三，金额 100。",
        2: "普通说明页。",
        3: "TARGET 报销记录 B，姓名李四，金额 200。",
    }

    async def inspect(self, source: SourceInput, *, owner_key: str) -> dict[str, object]:
        del owner_key
        return {
            "source_id": source.upload_id,
            "name": source.original_name,
            "unit_count": 3,
            "units": [
                {"unit_id": f"{source.upload_id}:page:{page}"}
                for page in self._pages
            ],
        }

    async def discover(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        query: str,
        unit_ids: tuple[str, ...],
    ) -> dict[str, object]:
        del owner_key
        selected = unit_ids or tuple(
            f"{source.upload_id}:page:{page}" for page in self._pages
        )
        term = query.casefold().strip()
        candidates = [
            unit_id
            for unit_id in selected
            if term in self._pages[int(unit_id.rsplit(":", 1)[1])].casefold()
        ]
        return {
            "source_id": source.upload_id,
            "observed_unit_ids": list(selected),
            "candidate_unit_ids": candidates,
            "low_quality_units": [],
            "unknown_units": [],
            "hits": [],
            "cache_hits": 0,
            "parser_versions": ["semantic-fixture-discovery-v1"],
        }

    async def read(
        self,
        source: SourceInput,
        *,
        owner_key: str,
        unit_ids: tuple[str, ...],
        needs: tuple[str, ...],
    ) -> dict[str, object]:
        del owner_key, needs
        items = []
        for unit_id in unit_ids:
            page = int(unit_id.rsplit(":", 1)[1])
            items.append({
                "evidence_ref": f"evidence:{unit_id}",
                "unit_id": unit_id,
                "quality_status": "trusted",
                "text": self._pages[page],
            })
        return {
            "source_id": source.upload_id,
            "source_unit_ids": list(unit_ids),
            "evidence_refs": [item["evidence_ref"] for item in items],
            "quality_status": "trusted",
            "authoritative_parser_versions": ["semantic-fixture-read-v1"],
            "items": items,
            "cache_hits": 0,
        }


def _wait_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("覆盖语义探针 Relay 未能启动")


def _mount(source: Path, target: str) -> str:
    return f"type=bind,source={source.resolve()},target={target}"


def _run_case(
    *,
    root: Path,
    temp: Path,
    broker: DocumentToolBroker,
    source: SourceInput,
    port: int,
    case_id: str,
    prompt: str,
    expected_cardinality: str | None,
    expected_ordinal: int | None = None,
) -> bool:
    grant = broker.issue_grant(
        owner_user_id="semantic-probe-user",
        task_id=f"semantic-{case_id}",
        revision=1,
        run_id=f"run-{case_id}",
        sources=(source,),
    )
    case_root = temp / case_id
    config = case_root / "config"
    work = case_root / "work"
    session = case_root / "session"
    for path in (config / "extensions", work, session):
        path.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        root / "src" / "agentic_runtime" / "assets" / "mangrove-document-tools.ts",
        config / "extensions" / "mangrove-document-tools.ts",
    )
    (config / "document-tools.json").write_text(
        json.dumps({
            "relayBaseUrl": f"http://host.docker.internal:{port}/internal/document-tools",
            "grantToken": grant.token,
            "grantId": grant.grant_id,
            "ownerBinding": grant.owner_binding,
            "taskId": grant.task_id,
            "revision": grant.revision,
            "runId": grant.run_id,
            "purpose": grant.purpose,
        }),
        encoding="utf-8",
    )
    (config / "models.json").write_text(
        json.dumps({"providers": {"mangrove-local": {
            "baseUrl": settings.llm_base_url,
            "api": "openai-completions",
            "apiKey": "local-runtime",
            "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False},
            "models": [{
                "id": settings.llm_model_name,
                "name": settings.llm_model_name,
                "reasoning": True,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 32768,
                "maxTokens": 4096,
            }],
        }}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (config / "system.md").write_text(
        "你只使用 Mangrove 文档工具完成任务。先 inspect_source，再自主理解范围和数量。"
        "明确任务应冻结覆盖契约并完成；实质歧义只能调用 request_clarification。"
        "发现文本不是证据，结果必须 read_evidence；最后用 propose_completion 提交逐结果"
        " unit_ids、evidence_refs、边界和必需字段证明。不得按固定问法选择路线。",
        encoding="utf-8",
    )
    container_name = f"mangrove-pi-semantic-{case_id}-{int(time.time())}"
    command = [
        "docker", "run", "--rm", "-i", "--name", container_name,
        "--add-host", "host.docker.internal:host-gateway",
        "--mount", _mount(config, "/root/.pi/agent"),
        "--mount", _mount(work, "/workspace/work"),
        "--mount", _mount(session, "/workspace/session"),
        "--workdir", "/workspace/work", settings.pi_runtime_image,
        "pi", "--mode", "rpc", "--provider", "mangrove-local",
        "--model", settings.llm_model_name, "--api-key", "local-runtime",
        "--session-dir", "/workspace/session",
        "--append-system-prompt", "/root/.pi/agent/system.md",
        "--no-builtin-tools", "--tools",
        "request_clarification,inspect_source,freeze_coverage,discover_content,"
        "read_evidence,propose_completion", "--approve",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps({"type": "prompt", "message": prompt}, ensure_ascii=False) + "\n")
    process.stdin.flush()
    events: list[dict[str, object]] = []
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            continue
        event = json.loads(line)
        events.append(event)
        if event.get("type") == "agent_end":
            break
    if process.poll() is None:
        process.terminate()
    _, stderr = process.communicate(timeout=20)
    serialized = json.dumps(events, ensure_ascii=False)
    if expected_cardinality is None:
        passed = '"toolName": "request_clarification"' in serialized
    else:
        state = broker.completion_state(grant.grant_id)
        passed = (
            state is not None
            and state[0].result_cardinality.value == expected_cardinality
            and state[0].result_ordinal == expected_ordinal
            and state[1].verifier_decision == "passed"
        )
    if not passed:
        (root / ".pytest-tmp" / f"pi-semantic-{case_id}-failure.json").write_text(
            json.dumps({
                "event_types": [str(event.get("type")) for event in events],
                "event_tail": serialized[-20_000:],
                "stderr_tail": stderr[-4_000:],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({"case": case_id, "passed": False}, ensure_ascii=False))
    broker.revoke_grant(grant.grant_id, "探针结束")
    return passed


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    temp_parent = root / ".pytest-tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    port = 18090
    broker = DocumentToolBroker(retriever=SemanticFixtureRetriever())
    configure_default_document_tool_broker(broker)
    app = FastAPI()
    app.include_router(document_tools.router)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    _wait_port(port)
    results: list[bool] = []
    try:
        with tempfile.TemporaryDirectory(prefix="pi-coverage-semantics-", dir=temp_parent) as raw_temp:
            temp = Path(raw_temp)
            source_path = temp / "fixture.pdf"
            source_path.write_bytes(b"semantic fixture")
            source = SourceInput(
                upload_id="semantic-source",
                original_name="fixture.pdf",
                host_path=source_path,
                sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                media_type="application/pdf",
            )
            scenarios = (
                ("first", "查找按页码顺序第一个 TARGET 完整记录；一页就是一个对象，必需字段为姓名和金额。", "first", None),
                ("ordinal", "查找按页码顺序第 2 个 TARGET 完整记录；一页就是一个对象，必需字段为姓名和金额。", "ordinal", 2),
                ("all", "为审计列出整份来源中每一条 TARGET 记录，不得漏项；一页就是一个对象，每条提取姓名和金额。", "all", None),
                ("ambiguous", "查一下 TARGET 的数据；如果返回一个还是全部会改变结果，请只问我一个问题。", None, None),
            )
            for round_no in range(1, 4):
                for name, prompt, cardinality, ordinal in scenarios:
                    case_passed = _run_case(
                        root=root,
                        temp=temp,
                        broker=broker,
                        source=source,
                        port=port,
                        case_id=f"{name}-{round_no}",
                        prompt=f"来源 ID 是 semantic-source。{prompt}",
                        expected_cardinality=cardinality,
                        expected_ordinal=ordinal,
                    )
                    results.append(case_passed)
                    print(json.dumps({
                        "case": f"{name}-{round_no}",
                        "passed": case_passed,
                    }, ensure_ascii=False))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    running = subprocess.run(
        ["docker", "ps", "--filter", "name=mangrove-pi-semantic-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout.strip()
    passed = all(results) and len(results) == 12 and not running
    report = {
        "passed": sum(results),
        "total": len(results),
        "residual_containers": running,
    }
    (temp_parent / "pi-coverage-semantics-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
