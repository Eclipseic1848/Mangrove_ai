"""把明确 URL 解析为目标清单，并补齐确定性的平台信息。"""
from __future__ import annotations

from typing import Any, Dict

from ..state import ConductorState
from ..targets import build_target_manifest


async def target_resolve_node(state: ConductorState) -> Dict[str, Any]:
    spec = state.get("task_spec")
    if spec is None or not spec.urls:
        return {"target_manifest": []}
    manifest = build_target_manifest(spec.urls)
    existing = set(spec.platforms or [])
    for target in manifest:
        platform = target.get("platform")
        if platform and platform != "direct" and platform not in existing:
            spec.platforms.append(platform)
            existing.add(platform)
    return {"target_manifest": manifest, "task_spec": spec}
