# -*- coding: utf-8 -*-
"""Deep Agents 0.6.12 可抛弃 Adapter。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import httpx
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from langchain_openai import ChatOpenAI


PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROTOTYPE_ROOT))

from common import (  # noqa: E402
    emit,
    invoke_tool,
    load_bakeoff_case,
    model_config,
    system_prompt,
    user_prompt,
)

EXCLUDED_BUILTIN_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "task",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    case = load_bakeoff_case(args.case_id)
    config = model_config()
    args.run_dir.mkdir(parents=True, exist_ok=True)

    def bridge(tool_name: str, arguments: dict[str, object]) -> str:
        emit("tool.started", f"调用 {tool_name}", tool_name=tool_name)
        try:
            result = invoke_tool(
                python_executable=sys.executable,
                case_id=args.case_id,
                run_dir=args.run_dir,
                tool_name=tool_name,
                arguments=arguments,
            )
        except Exception as exc:
            emit(
                "tool.failed",
                f"{tool_name} 失败",
                tool_name=tool_name,
                error=str(exc),
            )
            # Deep Agents 的普通 callable 异常会终止整张图，必须转为可重规划的 Observation。
            return json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        emit("tool.completed", f"{tool_name} 已完成", tool_name=tool_name)
        if tool_name == "submit_candidate":
            emit("candidate.created", "候选产物已生成", **result)
        elif tool_name == "request_clarification":
            emit("approval.required", "需要用户确认目标", **result)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    def observe_sources() -> str:
        """观察 GoalContract 允许的来源、可读位置和结构摘要。"""
        return bridge("observe_sources", {})

    def read_source(source_id: str, locator: str) -> str:
        """定向读取一个已观察到的来源位置，并返回 EvidenceRef。"""
        return bridge(
            "read_source",
            {"source_id": source_id, "locator": locator},
        )

    def submit_candidate(output_format: str, filename: str, content: str) -> str:
        """提交一个候选文件；这不会发布正式交付。"""
        return bridge(
            "submit_candidate",
            {
                "output_format": output_format,
                "filename": filename,
                "content": content,
            },
        )

    def request_clarification(question: str, options: list[str]) -> str:
        """提出一个最小必要问题，并给出可执行的真实操作。"""
        return bridge(
            "request_clarification",
            {"question": question, "options": options},
        )

    client = httpx.Client(trust_env=False, timeout=120)
    model = ChatOpenAI(
        model=config["model"],
        base_url=config["base_url"],
        api_key=config["api_key"],
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
        max_retries=0,
        timeout=120,
        http_client=client,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": config["enable_thinking"],
            }
        },
    )
    register_harness_profile(
        "openai",
        HarnessProfile(
            excluded_tools=EXCLUDED_BUILTIN_TOOLS,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    agent = create_deep_agent(
        model=model,
        tools=[
            observe_sources,
            read_source,
            request_clarification,
            submit_candidate,
        ],
        backend=StateBackend(),
        subagents=[],
        system_prompt=system_prompt(case),
    )
    emit(
        "run.started",
        "Deep Agents 开始执行",
        candidate="deepagents",
        framework_version="0.6.12",
    )
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_prompt(case)}]},
            {"recursion_limit": 24},
        )
        messages = []
        for message in result["messages"]:
            messages.append(
                {
                    "type": type(message).__name__,
                    "content": getattr(message, "content", None),
                    "tool_calls": getattr(message, "tool_calls", None),
                }
            )
        (args.run_dir / "deepagents-native-messages.json").write_text(
            json.dumps(messages, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        emit("adapter.finished", "Deep Agents Agent Loop 已结束")
        return 0
    except Exception as exc:
        emit(
            "run.failed",
            "Deep Agents 执行异常",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
