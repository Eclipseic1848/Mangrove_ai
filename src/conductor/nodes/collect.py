"""采集节点：依候选顺序逐个尝试，命中数据即停止（能力升级式降级）。

补采增强（P0-1）：关键词发现型任务（无显式 URL）若首个命中的采集器数据量
不足 max_items 的一定比例，继续用后续采集器补齐（跨采集器按 URL 去重），
根治"只采到 1 条就交差"。URL 任务不补采——各引擎抓的是同一批 URL，
补采只会重复抓取浪费时间。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Dict, List

from src.collectors import get_registry, select_collectors
from src.collectors._domain_health import record as domain_health_record
from src.collectors._metrics import record as metrics_record
from src.config.settings import settings

from ..state import ConductorState

logger = logging.getLogger(__name__)

# 数据量达到 max_items 的此比例即视为"足够"，不再降级补采
_ENOUGH_RATIO = 0.6


def _item_key(item: Dict[str, Any]) -> str:
    """跨采集器去重键：优先 URL；无 URL 时用标题+正文前 200 字哈希。"""
    url = (item.get("url") or "").strip()
    if url:
        return url
    raw = (item.get("title") or "") + (item.get("content") or "")[:200]
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def collect_node(state: ConductorState) -> Dict[str, Any]:
    spec = state["task_spec"]
    names = state.get("collector_candidates")
    if not names:
        names = [c.name for c in select_collectors(spec)]
    if not names:
        return {"raw_dataset": [], "collector_used": "", "error": "没有可用的采集器"}

    registry = get_registry()
    last_msg = ""
    notes: list[str] = []  # 给用户的过程说明（登录过期/全网兜底/补采等），随结果一并透出
    attempts: list[dict[str, Any]] = []

    # 补采只对关键词发现型任务开启：URL 任务的数量≈URL 数，各引擎抓同一批 URL 无补采意义
    topup_enabled = not spec.urls
    target = max(1, spec.max_items)
    enough = max(1, int(target * _ENOUGH_RATIO))

    merged: List[Dict[str, Any]] = []   # 跨采集器累积的结果（已去重）
    seen_keys: set[str] = set()
    used_names: list[str] = []          # 实际贡献了数据的采集器

    for idx, name in enumerate(names):
        collector = registry.get(name)
        if collector is None:
            continue
        t0 = time.time()
        # mediacrawler 走真实浏览器，耗时远高于 HTTP 采集，单独用更长超时
        timeout = (
            settings.collect_timeout_mediacrawler_seconds
            if name == "mediacrawler"
            else settings.collect_timeout_seconds
        )
        try:
            # 超时保护：单个采集器卡死不冻住整条流水线，超时即降级到下一个
            result = await asyncio.wait_for(collector.collect(spec), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("采集器 %s 超时（>%ss），降级", name, timeout)
            last_msg = f"{name} 超时（>{timeout}s）"
            attempts.append({"collector": name, "success": False, "count": 0, "message": last_msg})
            continue
        except Exception as e:
            logger.warning("采集器 %s 异常: %s", name, e)
            last_msg = f"{name} 异常: {e}"
            attempts.append({"collector": name, "success": False, "count": 0, "message": last_msg})
            continue

        elapsed_ms = (time.time() - t0) * 1000
        if result.has_data:
            attempts.append({
                "collector": name,
                "success": True,
                "count": len(result.items),
                "message": result.message or "",
            })
            metrics_record(name, True, elapsed_ms)
            domain_health_record(name, spec.urls, True)
            # 跨采集器去重合并，总量不超过用户要求的上限
            added = 0
            for it in result.items:
                d = it.to_dict()
                key = _item_key(d)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(d)
                added += 1
                if len(merged) >= target:
                    break
            if added:
                used_names.append(name)
            logger.info("采集成功：%s，新增 %d 条（累计 %d/%d）", name, added, len(merged), target)
            if "兜底" in (result.message or ""):
                notes.append(f"ℹ️ {result.message}")
            # 足够了（或不启用补采）就停；否则继续用下一个采集器补齐
            if not topup_enabled or len(merged) >= enough:
                break
            # 诚实提示：只有后面真有候选引擎才说"继续补采"，否则明说已无引擎可补
            remaining = [n for n in names[idx + 1:] if registry.get(n) is not None]
            if remaining:
                notes.append(f"ℹ️ {name} 采到 {len(merged)} 条（目标 {target}），继续用下一引擎（{remaining[0]}）补采")
            else:
                notes.append(f"⚠️ {name} 采到 {len(merged)} 条（目标 {target}），已无更多可补采引擎，以现有数据继续")
            continue

        last_msg = f"{name}: {result.message}"
        attempts.append({
            "collector": name,
            "success": False,
            "count": 0,
            "message": result.message or "",
        })
        metrics_record(name, False, elapsed_ms)
        domain_health_record(name, spec.urls, False)
        # 社媒采集器失败且原因可操作（登录过期/风控/频次）时，记为面向用户的提示
        if name == "mediacrawler" and result.message:
            notes.append(f"⚠️ {result.message}")
        logger.info("采集器 %s 未取得数据，尝试降级。%s", name, result.message)

    if merged:
        return {
            "raw_dataset": merged,
            "collector_used": "+".join(used_names),
            "collector_notes": notes,
            "collector_attempts": attempts,
        }
    return {
        "raw_dataset": [],
        "collector_used": "",
        "collector_notes": notes,
        "collector_attempts": attempts,
        "error": f"所有采集器均未取得数据。最后信息：{last_msg}",
    }
