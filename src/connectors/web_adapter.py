"""WebCollectorAdapter -- 适配现有网页采集器输出新制品契约（plan.md 第 7.2/10.2 节）。

设计：现有 src/collectors 已内置 fetch+extract（输出 url/title/content/metadata），
本适配器不重写其内部实现，而是：
1. 把 v2 SourceSpec 转成 v1 TaskSpec 喂给现有 collector
2. 调 collector.collect()，把每条 CollectedItem 存为不可变 RawArtifact（JSON）
3. 解析阶段由 WebParser 读取 RawArtifact 产出 RecordEnvelope（fetch/parse 分离）

plan 10.2 适配：第一阶段不重写 collector 内部；Phase 2+ 文件/API/DB 严格分离。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from src.collectors import get_registry, select_collectors
from src.collectors._domain_health import record as domain_health_record
from src.collectors._metrics import record as metrics_record
from src.config.settings import settings
from src.conductor.task_spec import TaskSpec as V1TaskSpec
from src.conductor.task_spec import DataType, LoginStrategy

from src.data_prep.checkpoints import Checkpoint
from src.data_prep.models import SourceSpec, SourceType

from .base import ProbeResult, RecordBatch, SourceConnector

logger = logging.getLogger(__name__)


def _v2_to_v1_spec(source: SourceSpec, selection_options: Dict[str, Any]) -> V1TaskSpec:
    """把 v2 SourceSpec + 选取参数转成 v1 TaskSpec（喂现有 collector）。"""
    locator = source.locator or ""
    urls = [locator] if locator.startswith("http") else []
    keywords = selection_options.get("keywords") or []
    platforms = selection_options.get("platforms") or []
    site_domains = selection_options.get("site_domains") or []
    time_range = selection_options.get("time_range")
    max_items = (source.limits.max_records if source.limits else None) or selection_options.get("max_items") or 30

    return V1TaskSpec(
        intent=selection_options.get("intent") or "数据准备采集",
        platforms=platforms,
        urls=urls,
        site_domains=site_domains,
        keywords=keywords,
        data_type=DataType.GENERIC,
        time_range=time_range,
        max_items=min(int(max_items), 2000),
        login_strategy=LoginStrategy.NONE,
        outputs=[],
    )


class WebCollectorAdapter(SourceConnector):
    """网页采集器适配器。"""

    name = "web"
    source_type = SourceType.WEB.value

    async def probe(self, spec: SourceSpec) -> ProbeResult:
        """轻量探测：检查是否有可用采集器。不拉全量。"""
        v1 = _v2_to_v1_spec(spec, spec.options or {})
        cands = select_collectors(v1)
        if not cands:
            return ProbeResult(reachable=False, message="没有可用采集器", warnings=[])
        names = [c.name for c in cands]
        return ProbeResult(
            reachable=True,
            message=f"可用采集器: {', '.join(names)}",
            sample={"candidates": names, "url": spec.locator},
        )

    async def read(
        self, spec: SourceSpec, checkpoint: Optional[Checkpoint] = None
    ) -> AsyncIterator[RecordBatch]:
        """采集并写入 RawArtifact。复用 select_collectors + collector.collect()。

        保留 collect.py 的核心思想：能力升级式降级、跨采集器去重补采、超时保护。
        """
        from src.data_prep.artifact_store import ArtifactStore

        v1 = _v2_to_v1_spec(spec, spec.options or {})
        cands = select_collectors(v1)
        if not cands:
            yield RecordBatch(
                checkpoint=Checkpoint(is_final=True),
                fatal_error="没有可用采集器",
            )
            return

        store = ArtifactStore()
        task_id = spec.options.get("task_id", "")
        source_id = spec.source_id

        # 跨采集器去重（与 collect.py._item_key 一致）
        seen_keys: set[str] = set()
        artifacts_meta: List = []  # RawArtifact 列表
        used_names: List[str] = []
        target = v1.max_items
        enough = max(1, int(target * 0.6))
        topup_enabled = not v1.urls
        warnings: List[str] = []

        for idx, collector in enumerate(cands):
            if len(artifacts_meta) >= target:
                break
            name = collector.name
            timeout = (
                settings.collect_timeout_mediacrawler_seconds
                if name == "mediacrawler"
                else settings.collect_timeout_seconds
            )
            t0 = time.time()
            try:
                result = await asyncio.wait_for(collector.collect(v1), timeout=timeout)
            except asyncio.TimeoutError:
                metrics_record(name, False, (time.time() - t0) * 1000)
                domain_health_record(name, v1.urls, False)
                warnings.append(f"{name} 超时（>{timeout}s），降级")
                continue
            except Exception as e:  # noqa: BLE001
                metrics_record(name, False, (time.time() - t0) * 1000)
                domain_health_record(name, v1.urls, False)
                warnings.append(f"{name} 异常: {e}")
                continue

            elapsed_ms = (time.time() - t0) * 1000
            if not result.has_data:
                metrics_record(name, False, elapsed_ms)
                domain_health_record(name, v1.urls, False)
                warnings.append(f"{name}: {result.message}")
                continue
            # 采集成功：记录健康度与域名反爬学习（与 collect.py 对齐，data_prep 模式下仪表盘也更新）
            metrics_record(name, True, elapsed_ms)
            domain_health_record(name, v1.urls, True)

            added = 0
            for item in result.items:
                d = item.to_dict()
                key = (d.get("url") or "") + (d.get("content") or "")[:200]
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                # 存为不可变 RawArtifact（JSON：collector 输出）
                payload = json.dumps(d, ensure_ascii=False).encode("utf-8")
                artifact = store.write_raw(
                    task_id=task_id,
                    source_id=source_id,
                    data=payload,
                    uri=d.get("url") or "",
                    media_type="application/json",
                    request_snapshot={"collector": name, "url": d.get("url")},
                    response_metadata={"title": d.get("title", "")[:200]},
                    ext="json",
                )
                artifacts_meta.append(artifact)
                added += 1
                if len(artifacts_meta) >= target:
                    break

            if added:
                used_names.append(name)
            if not topup_enabled or len(artifacts_meta) >= enough:
                break
            remaining = [c.name for c in cands[idx + 1:]]
            if remaining:
                warnings.append(f"{name} 采到 {len(artifacts_meta)} 条，继续补采（{remaining[0]}）")

        yield RecordBatch(
            artifacts=artifacts_meta,
            checkpoint=Checkpoint(is_final=True),
            byte_count=sum(a.size_bytes for a in artifacts_meta),
            warnings=warnings,
        )

    def capabilities(self):
        from src.data_prep.models import ConnectorCapability
        return {ConnectorCapability.STREAMING}

    async def close(self) -> None:
        return None
