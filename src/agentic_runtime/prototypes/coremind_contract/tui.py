"""可抛弃单屏终端：手动推动 CoreMind AgentKernel 合同状态。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from contract_model import (
    Action,
    ActionType,
    KernelManifest,
    PrototypeState,
    REQUIRED_CAPABILITIES,
    initial_state,
    reduce_state,
)
from local_source_probe import probe_local_coremind


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def full_manifest() -> KernelManifest:
    return KernelManifest(
        family="mangrove-contract-simulator",
        version="prototype-1",
        protocol="in-memory",
        evidence_level="完整合同模拟",
        capabilities=REQUIRED_CAPABILITIES | frozenset({"replay", "child_run"}),
    )


PROFILES = {
    "1": full_manifest,
    "2": probe_local_coremind,
}


ACTIONS = {
    "s": Action(ActionType.START),
    "t": Action(ActionType.TICK),
    "o": Action(ActionType.TOOL_SUCCEEDED),
    "f": Action(ActionType.TOOL_FAILED),
    "h": Action(ActionType.FAILURE_RECOVERED),
    "p": Action(ActionType.PAUSE),
    "r": Action(ActionType.RESUME),
    "g": Action(ActionType.STEER),
    "k": Action(ActionType.USAGE_KNOWN, value=1_200),
    "u": Action(ActionType.USAGE_UNKNOWN),
    "e": Action(ActionType.UNKNOWN_EVENT),
    "x": Action(ActionType.FINISH),
    "c": Action(ActionType.CANCEL),
}


def render(state: PrototypeState) -> None:
    os.system("cls" if os.name == "nt" else "clear")
    manifest = state.manifest
    print(f"{BOLD}PROTOTYPE — CoreMind AgentKernel 合同{RESET}")
    print(f"{DIM}只验证状态形状，不接真实 Provider、数据库或 Delivery。{RESET}\n")
    print(f"{BOLD}RuntimeBinding{RESET}")
    print(f"  family: {manifest.family}")
    print(f"  version: {manifest.version}")
    print(f"  protocol: {manifest.protocol}")
    print(f"  evidence: {manifest.evidence_level}")
    print(f"  capabilities: {', '.join(sorted(manifest.capabilities)) or '-'}")
    print(f"  missing_required: {', '.join(manifest.missing_required) or '-'}\n")

    state_view = {
        "lifecycle": state.lifecycle,
        "run_id": state.run_id,
        "sequence": state.sequence,
        "active_ticks": state.active_ticks,
        "waiting_ticks": state.waiting_ticks,
        "tool_calls": state.tool_calls,
        "unresolved_failures": state.unresolved_failures,
        "recovered_failures": state.recovered_failures,
        "usage": state.usage_summary,
        "candidate_ready": state.candidate_ready,
        "delivery_created": state.delivery_created,
        "last_error": state.last_error,
        "verdict": state.verdict,
    }
    print(f"{BOLD}Current state{RESET}")
    print(json.dumps(state_view, ensure_ascii=False, indent=2, default=str))
    print(f"\n{BOLD}Recent events{RESET}")
    for event in state.events[-5:]:
        print(f"  {event.sequence:02d}  {event.event_type:<27} {event.summary}")
    if not state.events:
        print("  -")

    print(f"\n{BOLD}Keys{RESET}")
    print("  [1] 完整模拟  [2] 本机 CoreMind 源码边界  [0] 重置")
    print("  [s] 启动  [t] 时间  [o] 工具成功  [f] 工具失败  [h] 恢复失败")
    print("  [p] 暂停  [r] 恢复  [g] 引导  [k] 已知 Usage  [u] 未知 Usage")
    print("  [e] 未知事件  [x] Candidate  [c] 取消  [q] 退出")


def main() -> None:
    state = initial_state(full_manifest())
    while True:
        render(state)
        choice = input("\n> ").strip().lower()
        if choice == "q":
            return
        if choice in PROFILES:
            state = initial_state(PROFILES[choice]())
            continue
        if choice == "0":
            state = initial_state(state.manifest)
            continue
        action = ACTIONS.get(choice)
        if action is not None:
            state = reduce_state(state, action)


if __name__ == "__main__":
    main()
