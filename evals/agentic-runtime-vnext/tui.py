# -*- coding: utf-8 -*-
"""阶段 1 赛马的轻量交互式终端外壳。"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from common import (
    CASE_FILE,
    PROTOTYPE_ROOT,
    REPO_ROOT,
    adapter_environment,
    load_bakeoff_case,
    system_prompt,
    user_prompt,
)
from state_model import GoalContract, KernelEvent, RunState, reduce_event


CANDIDATES = ("deepagents", "opencode", "pi")


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """只终止本次原型启动的 Adapter 进程树。"""

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    else:
        process.terminate()


def initial_state(candidate: str, case_id: str) -> RunState:
    case = load_bakeoff_case(case_id)
    goal = case["goal"]
    return RunState(
        candidate=candidate,
        case_id=case_id,
        goal=GoalContract(
            original_request=goal["original_request"],
            source_scope=tuple(goal["source_scope"]),
            output_format=goal["output_format"],
            output_file_count=goal["output_file_count"],
            must_include=tuple(goal.get("must_include", [])),
            must_not_include=tuple(goal.get("must_not_include", [])),
        ),
    )


def command_for(candidate: str, case_id: str, run_dir: Path) -> tuple[list[str], dict[str, str]]:
    python = str(REPO_ROOT / ".venv-agentic-bakeoff" / "Scripts" / "python.exe")
    environment = adapter_environment(case_id, run_dir)
    if candidate == "deepagents":
        return [
            python,
            str(PROTOTYPE_ROOT / "adapters" / "deepagents_adapter.py"),
            "--case-id",
            case_id,
            "--run-dir",
            str(run_dir),
        ], environment
    if candidate == "pi":
        case = load_bakeoff_case(case_id)
        prompt_file = run_dir / "prompt.json"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(
            json.dumps(
                {
                    "system_prompt": system_prompt(case),
                    "user_prompt": user_prompt(case),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return [
            "node",
            str(PROTOTYPE_ROOT / "adapters" / "pi_adapter.mjs"),
            "--case-id",
            case_id,
            "--run-dir",
            str(run_dir),
            "--prompt-file",
            str(prompt_file),
        ], environment
    return [
        python,
        str(PROTOTYPE_ROOT / "adapters" / "opencode_adapter.py"),
        "--case-id",
        case_id,
        "--run-dir",
        str(run_dir),
    ], environment


def execute(state: RunState) -> tuple[RunState, Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = (
        PROTOTYPE_ROOT
        / "runs"
        / f"{stamp}-{state.candidate}-{state.case_id}-{uuid.uuid4().hex[:6]}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    command, environment = command_for(state.candidate, state.case_id, run_dir)
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    paused = False
    cancelled = False
    timed_out = False
    deadline = time.monotonic() + 360
    control_file = run_dir / "control.json"
    cancel_file = run_dir / "cancel.request"
    while process.poll() is None:
        if cancel_file.is_file():
            cancelled = True
            _terminate_process_tree(process)
            break
        if control_file.is_file():
            control = json.loads(control_file.read_text(encoding="utf-8"))
            if control.get("action") == "pause_for_clarification":
                paused = True
                # Windows 下必须结束我们启动的完整进程树，避免 Node/OpenCode 子进程继续提交。
                _terminate_process_tree(process)
                break
        if time.monotonic() >= deadline:
            timed_out = True
            process.kill()
            break
        time.sleep(0.05)
    stdout, stderr = process.communicate()
    (run_dir / "adapter-stdout.jsonl").write_text(stdout, encoding="utf-8")
    (run_dir / "adapter-stderr.log").write_text(stderr, encoding="utf-8")
    current = state
    deferred_events: list[KernelEvent] = []
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "event_type" not in payload:
            continue
        event = KernelEvent(
            event_type=payload["event_type"],
            summary=payload.get("summary", payload["event_type"]),
            payload=payload.get("payload", {}),
        )
        if event.event_type in {"run.started", "run.resumed"}:
            current = reduce_event(current, event)
        elif event.event_type not in {"tool.started", "tool.completed", "tool.failed"}:
            deferred_events.append(event)
    # Tool Bridge 账本是三候选统一事实源，避免各框架对工具失败的事件含义不一致。
    attempt_log = run_dir / "tool_attempts.jsonl"
    tool_attempts = (
        [
            json.loads(line)
            for line in attempt_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if attempt_log.is_file()
        else []
    )
    for item in tool_attempts:
        tool_name = item["tool_name"]
        current = reduce_event(
            current,
            KernelEvent(
                "tool.started",
                f"调用 {tool_name}",
                {"tool_name": tool_name},
            ),
        )
        event_type = "tool.failed" if item["status"] == "failed" else "tool.completed"
        payload = {"tool_name": tool_name}
        if event_type == "tool.failed":
            payload.update(
                {
                    "error_type": item.get("error_type"),
                    "error": item.get("error"),
                }
            )
        current = reduce_event(
            current,
            KernelEvent(
                event_type,
                f"{tool_name} 失败" if event_type == "tool.failed" else f"{tool_name} 已完成",
                payload,
            ),
        )
    for event in deferred_events:
        current = reduce_event(current, event)
    if cancelled:
        current = reduce_event(
            current,
            KernelEvent("run.cancelled", "用户已取消任务"),
        )
    if paused:
        if not current.clarification_required:
            clarification = json.loads(
                (run_dir / "clarification.json").read_text(encoding="utf-8")
            )
            current = reduce_event(
                current,
                KernelEvent(
                    "approval.required",
                    "需要用户确认目标",
                    clarification,
                ),
            )
    if timed_out and not current.adapter_failed:
        current = reduce_event(
            current,
            KernelEvent("run.failed", "AgentKernel 执行超时"),
        )
    if (
        process.returncode != 0
        and not paused
        and not cancelled
        and not timed_out
        and not current.adapter_failed
    ):
        # Adapter 未能自行产出失败事件时，由 Supervisor 按退出码兜底失败关闭。
        current = reduce_event(
            current,
            KernelEvent(
                "run.failed",
                "AgentKernel 进程异常退出",
                {"exit_code": process.returncode},
            ),
        )
    current = reduce_event(
        current,
        KernelEvent("verification.started", "独立 Verifier 开始检查"),
    )
    verifier = subprocess.run(
        [
            sys.executable,
            str(PROTOTYPE_ROOT / "tool_host.py"),
            "--case-file",
            str(CASE_FILE),
            "--case-id",
            state.case_id,
            "--run-dir",
            str(run_dir),
            "verify",
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )
    verification = (
        json.loads(verifier.stdout)
        if verifier.returncode == 0
        else {"passed": False, "errors": [verifier.stderr.strip()]}
    )
    current = reduce_event(
        current,
        KernelEvent(
            "verification.completed",
            "Verifier 通过" if verification["passed"] else "Verifier 未通过",
            verification,
        ),
    )
    current = reduce_event(
        current,
        KernelEvent("run.completed", "本次原型运行结束"),
    )
    (run_dir / "unified-state.json").write_text(
        json.dumps(asdict(current), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return current, run_dir


def render(state: RunState, run_dir: Path | None = None) -> None:
    print("\033[2J\033[H", end="")
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"
    print(f"{bold}Mangrove Agentic Runtime vNext 阶段 1 原型{reset}")
    print(f"{bold}候选{reset}: {state.candidate}")
    print(f"{bold}用例{reset}: {state.case_id}")
    print(f"{bold}状态{reset}: {state.status}")
    print(f"{bold}活动工具{reset}: {', '.join(state.active_tools) or '-'}")
    print(f"{bold}工具调用数{reset}: {state.tool_calls}")
    print(f"{bold}候选已生成{reset}: {state.candidate_created}")
    print(f"{bold}需要用户确认{reset}: {state.clarification_required}")
    print(f"{bold}Verifier{reset}: {state.verification_passed}")
    print(f"{bold}最近摘要{reset}: {state.last_summary}")
    print(f"{bold}目标{reset}: {state.goal.original_request}")
    if run_dir:
        print(f"{dim}运行目录: {run_dir}{reset}")
    print()
    print(f"{bold}最近事件{reset}")
    for event in state.events[-10:]:
        print(f"- {event.event_type}: {event.summary}")
    print()
    print(
        f"{bold}[1]{reset} Deep Agents  "
        f"{bold}[2]{reset} OpenCode  "
        f"{bold}[3]{reset} Pi  "
        f"{bold}[c]{reset} 切换用例  "
        f"{bold}[r]{reset} 运行  "
        f"{bold}[q]{reset} 退出"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=CANDIDATES)
    parser.add_argument("--case-id")
    args = parser.parse_args()
    payload = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    case_ids = [item["case_id"] for item in payload["cases"]]
    candidate = args.candidate or CANDIDATES[0]
    case_id = args.case_id or case_ids[0]
    state = initial_state(candidate, case_id)
    run_dir = None
    if args.candidate or args.case_id:
        state, run_dir = execute(state)
        render(state, run_dir)
        return 0 if state.verification_passed or state.status.value == "cancelled" else 1
    while True:
        render(state, run_dir)
        choice = input("> ").strip().lower()
        if choice == "q":
            return 0
        if choice in {"1", "2", "3"}:
            candidate = CANDIDATES[int(choice) - 1]
            state = initial_state(candidate, case_id)
            run_dir = None
        elif choice == "c":
            index = (case_ids.index(case_id) + 1) % len(case_ids)
            case_id = case_ids[index]
            state = initial_state(candidate, case_id)
            run_dir = None
        elif choice == "r":
            state, run_dir = execute(initial_state(candidate, case_id))


if __name__ == "__main__":
    raise SystemExit(main())
