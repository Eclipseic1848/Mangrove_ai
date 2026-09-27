"""在独立无网络容器验证候选 Pi 的 RPC 和现有扩展加载，不调用模型。"""
from __future__ import annotations

import argparse
import hashlib
import json
import queue
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid


def remove_container(name):
    """清理失败不能掩盖主异常，也不能冒充无残留。"""
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
        result = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^/{name}$"],
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0 and not result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False


def next_line(lines, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise queue.Empty
    return lines.get(timeout=remaining)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scripted-model", action="store_true")
    parser.add_argument("--cancel-running", action="store_true")
    parser.add_argument("--unknown-outcome", action="store_true")
    args = parser.parse_args()
    if args.cancel_running and not args.scripted_model:
        parser.error("--cancel-running 需要 --scripted-model")
    if args.unknown_outcome and (not args.scripted_model or args.cancel_running):
        parser.error("--unknown-outcome 需要 --scripted-model 且不能与取消模式并用")
    root = Path(__file__).resolve().parents[1]
    image_id = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True, timeout=10).strip()
    report = {"image": args.image, "image_id": image_id, "model_calls": 0, "network": "none", "checks": {}}
    with tempfile.TemporaryDirectory(prefix="pi-candidate-") as temporary:
        directory = Path(temporary)
        config, work = directory / "config", directory / "work"
        extensions = config / "extensions"
        extensions.mkdir(parents=True)
        work.mkdir()
        endpoint = "http://127.0.0.1:19190" if args.scripted_model else "http://127.0.0.1:9"
        settings = {"retry": {"enabled": False, "provider": {"maxRetries": 0}}, "cacheWarming": "off",
                    "enableInstallTelemetry": False, "enableAnalytics": False}
        (config / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        (config / "models.json").write_text(json.dumps({"providers": {"fixture": {
            "baseUrl": endpoint + "/v1", "api": "openai-completions", "apiKey": "synthetic-not-secret",
            "models": [{"id": "fixture", "name": "fixture", "contextWindow": 32768, "maxTokens": 1024}],
        }}}), encoding="utf-8")
        for name, body in {
            "document-tools": {"relayBaseUrl": endpoint, "grantToken": "fixture", "grantId": "fixture",
                               "ownerBinding": "fixture", "taskId": "fixture", "revision": 1, "runId": "fixture", "purpose": "read"},
            "capability-host": {"relayUrl": endpoint, "relayToken": "fixture",
                                "capabilities": [{"name": "fixture", "kind": "python"}]},
        }.items():
            (config / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
        report["extension_sha256"] = {}
        for path in sorted((root / "src/agentic_runtime/assets").glob("*.ts")):
            shutil.copy2(path, extensions / path.name)
            report["extension_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (extensions / "candidate-probe.ts").write_text('''
import { writeFileSync } from "node:fs";
export default function probe(pi) {
  pi.registerCommand("candidate-tools", {
    description: "列出已装载工具，不请求模型",
    handler: async () => {
      writeFileSync("/workspace/work/tools.json", JSON.stringify(pi.getAllTools().map(t => t.name)), "utf8");
    }
  });
}
''', encoding="utf-8")
        name = "mangrove-pi-qualification-" + uuid.uuid4().hex[:12]
        command = ["docker", "run", "--rm", "-i", "--name", name, "--network", "none", "--read-only",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "-e", "PI_OFFLINE=1", "-e", "PI_TELEMETRY=0", "-v", f"{config}:/root/.pi/agent:rw",
                   "-v", f"{work}:/workspace/work:rw", image_id, "pi", "--mode", "rpc", "--no-session",
                   "--provider", "fixture", "--model", "fixture", "--no-skills", "--no-context-files", "--offline"]
        commands = [{"id": str(i), **value} for i, value in enumerate([
            {"type": "get_state"}, {"type": "prompt", "message": "/candidate-tools"},
            {"type": "abort"}, {"type": "get_messages"}, {"type": "new_session"},
        ])]
        if args.scripted_model:
            shutil.copy2(root / "tests/fixtures/pi_candidate_provider.py", work / "provider.py")
            (work / "source.txt").write_text("".join(f"{i:03d}:" + "合成内容" * 40 + "\n" for i in range(100)), encoding="utf-8")
            index = command.index(image_id) + 1
            command[index:index + 1] = ["python", "/workspace/work/provider.py"]
            index = command.index("--no-session")
            command[index:index + 1] = ["--session", "/workspace/session/qualification.jsonl"]
            session_directory = directory / "session"
            session_directory.mkdir()
            index = command.index(image_id)
            command[index:index] = ["-v", f"{session_directory}:/workspace/session:rw"]
            commands.insert(2, {"id": "fixture-run", "type": "prompt",
                                "message": "fixture-cancel-running" if args.cancel_running else
                                    ("fixture-unknown-outcome" if args.unknown_outcome else "执行合成工具验证")})
            # 冷恢复验证原会话；新会话命令已由不带模型的探针单独覆盖。
            commands = [value for value in commands if value["type"] != "new_session"]
        try:
            # RPC 是异步长会话；逐条等对应响应，不能以发送完毕后的 EOF 代替完成。
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=errors, text=True, encoding="utf-8")
                lines = queue.Queue()
                def read_lines():
                    for line in process.stdout:
                        lines.put(line)
                    lines.put(None)
                reader = threading.Thread(target=read_lines, daemon=True)
                reader.start()
                responses = {}
                events = []
                abort_sent = False
                try:
                    deadline = time.monotonic() + 45
                    for value in commands:
                        process.stdin.write(json.dumps(value) + "\n")
                        process.stdin.flush()
                        finished = value["id"] != "fixture-run"
                        while value["id"] not in responses or not finished or (abort_sent and "running-abort" not in responses):
                            line = next_line(lines, deadline)
                            if line is None:
                                raise RuntimeError("候选 RPC 在响应前结束")
                            if line.startswith("{"):
                                record = json.loads(line)
                                if record.get("type") == "response":
                                    responses[record.get("id")] = record
                                else:
                                    events.append(record.get("type"))
                                    delta = record.get("assistantMessageEvent", {})
                                    if (args.cancel_running and not abort_sent and record.get("type") == "message_update"
                                            and delta.get("type") == "text_delta" and delta.get("delta") == "等待取消"):
                                        # 收到模型流片段后再取消，避免把空闲取消误当执行中取消。
                                        process.stdin.write(json.dumps({"id": "running-abort", "type": "abort"}) + "\n")
                                        process.stdin.flush()
                                        abort_sent = True
                                        report["checks"]["text_received_before_abort"] = True
                                    if record.get("type") == "agent_end":
                                        finished = True
                    process.stdin.close()
                    returncode = process.wait(timeout=10)
                finally:
                    if process.poll() is None:
                        remove_container(name)
                        process.kill()
                        process.wait(timeout=5)
                    reader.join(timeout=2)
                errors.seek(0)
                stderr = errors.read()
            report["checks"]["rpc_responses"] = all(responses.get(c["id"], {}).get("success") is True for c in commands)
            artifact = work / "tools.json"
            tools = json.loads(artifact.read_text(encoding="utf-8")) if artifact.exists() else []
            report["tools"] = tools
            report["checks"]["document_extension"] = {"inspect_source", "read_evidence", "freeze_coverage"}.issubset(tools)
            report["checks"]["capability_extension"] = "capability_fixture" in tools
            report["checks"]["process_exit"] = returncode == 0
            report["stderr"] = stderr[-4000:]
            report["responses"] = responses
            report["events"] = events
            if args.scripted_model:
                requests = json.loads((work / "model-requests.json").read_text(encoding="utf-8"))
                report["scripted_model_calls"] = len(requests)
                if args.cancel_running:
                    report["checks"]["running_abort"] = abort_sent and responses.get("running-abort", {}).get("success") is True
                    report["checks"]["no_retry_after_abort"] = len(requests) == 1
                    report["checks"]["aborted_message"] = any(m.get("role") == "assistant" and m.get("stopReason") == "aborted"
                        for m in responses["3"]["data"]["messages"])
                if args.unknown_outcome:
                    report["checks"]["unknown_request_not_repeated"] = len(requests) == 1
                    report["checks"]["unknown_stopped_as_error"] = any(m.get("role") == "assistant" and m.get("stopReason") == "error"
                        for m in responses["3"]["data"]["messages"])
                messages = requests[-1]["messages"]
                rendered = json.dumps(messages, ensure_ascii=False)
                results = {m.get("toolCallId"): m for m in responses["3"]["data"]["messages"]
                           if m.get("role") == "toolResult"}
                document = results.get("fixture-2", {})
                capability = results.get("fixture-3", {})
                blocked = results.get("fixture-4", {})
                preserved = work / "tool-results/fixture-1-0-0.txt"
                tool_checks = {
                    "scripted_tool_loop": len(requests) == 5,
                    "context_gate": "不可信工具数据开始" in rendered and "完整输出" in rendered,
                    "runtime_secret_read_blocked": blocked.get("isError") is True and blocked.get("toolName") == "bash"
                        and "运行时凭证配置不可由任务 Shell 读取" in json.dumps(blocked.get("content"), ensure_ascii=False),
                    "capability_result": capability.get("isError") is False and capability.get("toolName") == "capability_fixture"
                        and capability.get("details") == {"stdout": "capability-ok"},
                    "document_result": document.get("isError") is False and document.get("toolName") == "inspect_source"
                        and document.get("details") == {"source_id": "fixture", "pages": 1},
                    "full_tool_output_preserved": results.get("fixture-1", {}).get("isError") is False and preserved.exists()
                        and preserved.read_text(encoding="utf-8") == (work / "source.txt").read_text(encoding="utf-8"),
                    "final_answer": any(m.get("role") == "assistant" and m.get("content") == [{"type": "text", "text": "fixture-complete"}]
                        for m in responses["3"]["data"]["messages"]),
                    "settled_event": "agent_settled" in events,
                }
                if not args.cancel_running and not args.unknown_outcome:
                    report["checks"].update(tool_checks)
                session_path = session_directory / "qualification.jsonl"
                report["session_file_exists"] = session_path.exists()
                if session_path.exists():
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(session_path, args.output.with_suffix(".session.jsonl"))
                    report["session_record_types"] = [json.loads(line).get("type") for line in session_path.read_text(encoding="utf-8").splitlines()]
                report["command"] = command
                cold = subprocess.run(command, input=json.dumps({"id": "cold", "type": "get_messages"}) + "\n" + json.dumps({"id": "cold-state", "type": "get_state"}) + "\n",
                                      capture_output=True, text=True, encoding="utf-8", timeout=30)
                cold_records = [json.loads(line) for line in cold.stdout.splitlines() if line.startswith("{")]
                restored = next((r for r in cold_records if r.get("id") == "cold"), {})
                report["cold_response"] = restored
                report["cold_state"] = next((r for r in cold_records if r.get("id") == "cold-state"), {})
                report["cold_stderr"] = cold.stderr[-2000:]
                report["checks"]["cold_session_messages"] = (cold.returncode == 0 and restored.get("success") is True
                    and restored.get("data") == responses["3"].get("data"))
                report["checks"]["cold_read_no_model_call"] = json.loads((work / "model-requests.json").read_text(encoding="utf-8")) == []
        except (subprocess.TimeoutExpired, queue.Empty):
            report["checks"]["bounded_completion"] = False
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            report["checks"]["execution"] = False
            report["error_type"] = type(error).__name__
        finally:
            report["checks"]["container_removed"] = remove_container(name)
    report["passed"] = all(report["checks"].values())
    report["limitations"] = ["不证明真实Provider质量或工具执行中断后的重放安全", "取消仅验证模型流中断" if args.cancel_running else "RPC abort为空闲取消", "中继仅为合成响应，不验证宿主权限"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
