# -*- coding: utf-8 -*-
"""真实验证固定 Pi 镜像可调用生产版文档工具 Extension。"""
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
from reportlab.pdfgen import canvas
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agentic_runtime.document_retrieval import DocumentRetrievalModule
from src.agentic_runtime.document_tools import (
    DocumentToolBroker,
    configure_default_document_tool_broker,
)
from src.agentic_runtime.models import SourceInput
from src.api.routes import document_tools
from src.config.settings import settings


def _wait_port(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("文档工具探针 Relay 未能启动")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    temp_parent = root / ".pytest-tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    port = 18089
    server: uvicorn.Server | None = None
    result_code = 1
    with tempfile.TemporaryDirectory(
        prefix="pi-document-tool-probe-",
        dir=temp_parent,
    ) as raw_temp:
        temp = Path(raw_temp)
        source_path = temp / "source.pdf"
        pdf = canvas.Canvas(str(source_path))
        pdf.drawString(72, 720, "DR00 production document tool bridge")
        pdf.save()
        source = SourceInput(
            upload_id="probe-source",
            original_name="source.pdf",
            host_path=source_path,
            sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            media_type="application/pdf",
        )
        broker = DocumentToolBroker(
            retriever=DocumentRetrievalModule(
                execution_root=temp / "cache"
            )
        )
        grant = broker.issue_grant(
            owner_user_id="probe-user",
            task_id="probe-task",
            revision=1,
            run_id="probe-run",
            sources=(source,),
        )
        configure_default_document_tool_broker(broker)
        app = FastAPI()
        app.include_router(document_tools.router)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",
                port=port,
                log_level="error",
            )
        )
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        _wait_port(port)

        config = temp / "config"
        work = temp / "work"
        output = temp / "output"
        session = temp / "session"
        for path in (config / "extensions", work, output, session):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            root
            / "src"
            / "agentic_runtime"
            / "assets"
            / "mangrove-document-tools.ts",
            config / "extensions" / "mangrove-document-tools.ts",
        )
        (config / "document-tools.json").write_text(
            json.dumps(
                {
                    "relayBaseUrl": (
                        f"http://host.docker.internal:{port}"
                        "/internal/document-tools"
                    ),
                    "grantToken": grant.token,
                    "grantId": grant.grant_id,
                    "ownerBinding": grant.owner_binding,
                    "taskId": grant.task_id,
                    "revision": grant.revision,
                    "runId": grant.run_id,
                    "purpose": grant.purpose,
                }
            ),
            encoding="utf-8",
        )
        models = {
            "providers": {
                "mangrove-local": {
                    "baseUrl": settings.llm_base_url,
                    "api": "openai-completions",
                    "apiKey": "local-runtime",
                    "compat": {
                        "supportsDeveloperRole": False,
                        "supportsReasoningEffort": False,
                    },
                    "models": [{
                        "id": settings.llm_model_name,
                        "name": settings.llm_model_name,
                        "reasoning": True,
                        "input": ["text"],
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "contextWindow": 32768,
                        "maxTokens": 4096,
                    }],
                }
            }
        }
        (config / "models.json").write_text(
            json.dumps(models, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (config / "probe-system.md").write_text(
            "这是生产文档工具桥探针。必须依次检查来源、冻结覆盖契约、"
            "发现 DR00、权威读取命中页并提议完成。不得使用其他工具。",
            encoding="utf-8",
        )

        def mount(source_dir: Path, target: str) -> str:
            return (
                f"type=bind,source={source_dir.resolve()},target={target}"
            )

        container_name = f"mangrove-pi-document-probe-{int(time.time())}"
        command = [
            "docker", "run", "--rm", "-i", "--name", container_name,
            "--add-host", "host.docker.internal:host-gateway",
            "--mount", mount(config, "/root/.pi/agent"),
            "--mount", mount(work, "/workspace/work"),
            "--mount", mount(output, "/workspace/output"),
            "--mount", mount(session, "/workspace/session"),
            "--workdir", "/workspace/work",
            settings.pi_runtime_image,
            "pi", "--mode", "rpc",
            "--provider", "mangrove-local",
            "--model", settings.llm_model_name,
            "--api-key", "local-runtime",
            "--session-dir", "/workspace/session",
            "--append-system-prompt", "/root/.pi/agent/probe-system.md",
            "--no-builtin-tools",
            "--tools", (
                "inspect_source,freeze_coverage,discover_content,"
                "read_evidence,propose_completion"
            ),
            "--approve",
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
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(
            json.dumps(
                {
                    "type": "prompt",
                    "message": (
                        "来源 ID 是 probe-source。目标是严格返回按页码顺序的第一个 "
                        "DR00 匹配；对象边界是一页，必需字段为空。完成后只报告完成门结论。"
                    ),
                },
                ensure_ascii=False,
            ) + "\n"
        )
        process.stdin.flush()
        events: list[dict[str, object]] = []
        deadline = time.monotonic() + 180
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
        required = (
            '"toolName": "inspect_source"',
            '"toolName": "freeze_coverage"',
            '"toolName": "read_evidence"',
            '"toolName": "propose_completion"',
            '"decision": "passed"',
        )
        if not all(marker in serialized for marker in required):
            print(serialized[-8000:])
            print(stderr[-2000:])
        else:
            print("PRODUCTION_DOCUMENT_TOOL_PROBE=PASS")
            result_code = 0
    if server is not None:
        server.should_exit = True
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
