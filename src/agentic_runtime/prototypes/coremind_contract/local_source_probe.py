"""只读投影相邻本机 CoreMind 源码的 Protocol v2 能力形状。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from contract_model import KernelManifest


def probe_local_coremind() -> KernelManifest:
    root = Path(__file__).resolve().parents[5] / "CoreMind"
    if not root.is_dir():
        return KernelManifest(
            family="coremind-local-source",
            version="not-found",
            protocol="unknown",
            evidence_level="未找到相邻本机源码仓库",
            capabilities=frozenset(),
        )

    protocol = _read(root / "packages/coremind-protocol/src/v2.ts")
    client = _read(root / "python/src/coremind/client.py")
    server = _read(root / "packages/coremind-worker/src/server.ts")
    runtime = _read(root / "packages/coremind-runtime/src/runtime.ts")
    agent_factory = _read(root / "packages/coremind-runtime/src/agent-factory.ts")
    package = json.loads(_read(root / "package.json"))

    capabilities: set[str] = set()
    if 'method: Type.Literal("run")' in protocol and "def run(" in client:
        capabilities.add("start")
    if 'method: Type.Literal("resume")' in protocol and "def resume_run(" in client:
        capabilities.add("resume")
    if 'Type.Literal("steering")' in protocol and "this.agent.steer(message)" in agent_factory:
        capabilities.add("steer")
    if 'Type.Literal("cancel")' in protocol and "def cancel(" in client:
        capabilities.add("cancel")
    if "ProtocolV2EventsRequestSchema" in protocol and "def events(" in client:
        capabilities.add("events")
    if "ProtocolV2QueryRequestSchema" in protocol and "def query(" in client:
        capabilities.add("query")
    if all(
        token_field in protocol
        for token_field in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
    ):
        capabilities.add("usage")
    if 'type: Type.Literal("effect_receipt")' in protocol:
        capabilities.add("tool_effect_events")
    if 'type: Type.Literal("checkpoint_created")' in protocol:
        capabilities.add("checkpoint_events")
    if "inspectCheckpoint(record" in runtime and "restoreCheckpoint(record" in runtime:
        capabilities.add("runtime_checkpoint")
    if "ReplayKit" in _read(root / "packages/coremind-runtime/src/replay-kit.ts"):
        capabilities.add("runtime_replay")
    if "delegateChildRun(" in runtime:
        capabilities.add("experimental_child_run")
    if "sessionId" in protocol and "session_id" in client:
        capabilities.add("session")

    # 严格按现成 v2 Adapter 边界声明：内部 Runtime 有这些原语，不等于 v2 已开放操作合同。
    if 'self._require_v1_method("checkpoint_diff")' not in client:
        capabilities.add("checkpoint")
    if "Protocol v2 尚未开放 Python callable 注册" not in client:
        capabilities.add("tool_effect")

    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    source_version = str(package.get("version", "unknown"))
    return KernelManifest(
        family="coremind-local-source",
        version=f"source-{commit} (manifest {source_version})",
        protocol="2.0 本机源码边界",
        evidence_level="源码符号投影；尚未执行 Runtime",
        capabilities=frozenset(capabilities),
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")
