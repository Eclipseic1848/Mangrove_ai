"""显式视频的证据补全节点。"""
from __future__ import annotations

from typing import Any, Dict, List

from src.config.settings import settings
from src.services.video_evidence import extract_video_evidence

from ..state import ConductorState
from ..targets import is_direct_video_manifest, target_matches


def _evidence_text(sources: List[Dict[str, str]]) -> str:
    """把不同来源的证据显式标记后交给后续分析节点。"""
    return "\n\n".join(f"【{source['type']}】\n{source['text']}" for source in sources)


async def video_enrich_node(state: ConductorState) -> Dict[str, Any]:
    """仅对显式视频链接提取证据；其他任务保持原始数据不变。"""
    targets = state.get("target_manifest") or []
    if not is_direct_video_manifest(targets):
        return {}
    if not settings.video_evidence_enabled:
        return {
            "evidence_ready": False,
            "target_coverage": {"total": len(targets), "verified": 0, "evidence_ready": 0},
            "collector_notes": ["显式视频证据链已关闭，未生成视频内容结论。"],
        }

    raw = state.get("raw_dataset") or []
    updated: List[Dict[str, Any]] = []
    bundles: List[Dict[str, Any]] = []
    notes: List[str] = list(state.get('collector_notes') or [])
    verified = 0
    ready = 0
    for target in targets:
        matched = next((item for item in raw if target_matches(item, target)), None)
        if not matched:
            notes.append(f"未采集到目标视频：{target.get('requested_url')}")
            continue
        metadata = matched.get("metadata") or {}
        if not metadata.get("identity_verified"):
            notes.append(f"目标身份未校验，已拒绝分析：{target.get('requested_url')}")
            continue
        verified += 1
        result = await extract_video_evidence(matched)
        bundle = {
            "target": target,
            "ready": result.get("ready", False),
            "reason": result.get("reason", ""),
            "sources": result.get("sources", []),
        }
        bundles.append(bundle)
        if not result.get("ready"):
            notes.append(f"视频证据不足：{result.get('reason') or target.get('requested_url')}")
            continue
        ready += 1
        enriched = dict(matched)
        enriched_meta = dict(metadata)
        enriched_meta["evidence_sources"] = [source["type"] for source in result["sources"]]
        enriched["metadata"] = enriched_meta
        enriched["content"] = _evidence_text(result["sources"])
        updated.append(enriched)

    coverage = {"total": len(targets), "verified": verified, "evidence_ready": ready}
    evidence_ready = bool(targets) and ready == len(targets)
    result: Dict[str, Any] = {
        "raw_dataset": updated,
        "evidence_bundles": bundles,
        "evidence_ready": evidence_ready,
        "target_coverage": coverage,
        "collector_notes": notes,
    }
    if not evidence_ready:
        reasons = "；".join(notes) or "未获得可验证的视频证据"
        result["analysis"] = (
            "## 视频内容证据不足\n\n"
            "本次未取得足以支撑内容总结的字幕、语音转写或画面文字证据，"
            "因此不会根据视频标题、简介或搜索结果推断视频内容。\n\n"
            f"原因：{reasons}"
        )
        result["analysis_source"] = "video_evidence_blocked"
    return result
