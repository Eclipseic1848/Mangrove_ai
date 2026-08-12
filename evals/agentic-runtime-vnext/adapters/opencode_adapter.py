# -*- coding: utf-8 -*-
"""OpenCode 1.18.9 headless 可抛弃 Adapter。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROTOTYPE_ROOT))

from common import (  # noqa: E402
    emit,
    load_bakeoff_case,
    model_config,
    system_prompt,
    user_prompt,
)


DISABLED_TOOLS = {
    name: False
    for name in (
        "bash",
        "edit",
        "write",
        "read",
        "grep",
        "glob",
        "list",
        "task",
        "webfetch",
        "websearch",
        "skill",
        "todowrite",
        "lsp",
        "question",
        "patch",
    )
}


def _find_executable() -> Path:
    node_modules = PROTOTYPE_ROOT / "node_modules"
    candidates = (
        node_modules / "opencode-windows-x64" / "bin" / "opencode.exe",
        node_modules / "opencode-windows-x64-baseline" / "bin" / "opencode.exe",
    )
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 1_000_000:
            return candidate
    raise FileNotFoundError("OpenCode 原生可执行文件尚未安装完成")


def _prepare_project(
    project_dir: Path,
    *,
    case: dict[str, object],
    config: dict[str, object],
) -> None:
    tools_dir = project_dir / ".opencode" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PROTOTYPE_ROOT / "adapters" / "opencode_tool_bridge.ts",
        project_dir / ".opencode" / "tool_bridge.ts",
    )
    for source in (PROTOTYPE_ROOT / "adapters" / "opencode_tools").glob("*.ts"):
        shutil.copy2(source, tools_dir / source.name)
    agent_tools = dict(DISABLED_TOOLS)
    agent_tools.update(
        {
            "observe_sources": True,
            "read_source": True,
            "request_clarification": True,
            "submit_candidate": True,
        }
    )
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "model": f"mangrove-local/{config['model']}",
        "small_model": f"mangrove-local/{config['model']}",
        "provider": {
            "mangrove-local": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Mangrove Local Qwen",
                "options": {
                    "baseURL": config["base_url"],
                    "apiKey": config["api_key"],
                    "timeout": 120_000,
                },
                "models": {
                    config["model"]: {
                        "name": config["model"],
                        "limit": {"context": 32_768, "output": 4_096},
                    }
                },
            }
        },
        "tools": agent_tools,
        "permission": {
            "*": "deny",
            "observe_sources": "allow",
            "read_source": "allow",
            "request_clarification": "allow",
            "submit_candidate": "allow",
        },
        "agent": {
            "bakeoff": {
                "description": "Mangrove AgentKernel 公平赛马候选",
                "mode": "primary",
                "model": f"mangrove-local/{config['model']}",
                "prompt": system_prompt(case),
                "tools": agent_tools,
            }
        },
        "default_agent": "bakeoff",
    }
    (project_dir / "opencode.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "--quiet", str(project_dir)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    case = load_bakeoff_case(args.case_id)
    config = model_config()
    project_dir = args.run_dir / "opencode-project"
    _prepare_project(project_dir, case=case, config=config)
    executable = _find_executable()
    environment = dict(os.environ)
    host = str(config["base_url"]).split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    no_proxy = ",".join(filter(None, (environment.get("NO_PROXY", ""), host)))
    environment["NO_PROXY"] = no_proxy
    environment["no_proxy"] = no_proxy
    emit(
        "run.started",
        "OpenCode headless 开始执行",
        candidate="opencode",
        framework_version="1.18.9",
    )
    command = [
        str(executable),
        "run",
        "--format",
        "json",
        # 显式标题可避免 OpenCode 在业务循环前额外调用一次本地模型生成标题。
        "--title",
        f"Mangrove Bakeoff {args.case_id}",
        "--model",
        f"mangrove-local/{config['model']}",
        "--agent",
        "bakeoff",
        "--dir",
        str(project_dir),
        user_prompt(case),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=project_dir,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # 超时必须转换成统一失败事件，不能让外层误把残留候选当成成功。
        emit(
            "run.failed",
            "OpenCode headless 执行超时",
            timeout_seconds=300,
            error=str(exc),
        )
        return 1
    (args.run_dir / "opencode-native-events.jsonl").write_text(
        completed.stdout,
        encoding="utf-8",
    )
    (args.run_dir / "opencode-native-stderr.log").write_text(
        completed.stderr,
        encoding="utf-8",
    )
    tool_log = args.run_dir / "tool_calls.jsonl"
    if tool_log.is_file():
        for line in tool_log.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            tool_name = payload["tool_name"]
            emit("tool.started", f"调用 {tool_name}", tool_name=tool_name)
            emit("tool.completed", f"{tool_name} 已完成", tool_name=tool_name)
            if tool_name == "submit_candidate":
                emit("candidate.created", "候选产物已生成")
            elif tool_name == "request_clarification":
                emit("approval.required", "需要用户确认目标")
    if completed.returncode != 0:
        emit(
            "run.failed",
            "OpenCode headless 执行异常",
            exit_code=completed.returncode,
            error=completed.stderr[-2000:],
        )
        return 1
    emit("adapter.finished", "OpenCode headless Agent Loop 已结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
