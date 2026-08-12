"""数据准备 LangGraph 新图（plan.md 第 5/10 节）。

主链路：intent -> prep_planner -> acquire -> parse -> profile_before -> clean -> validate -> output

控制面与数据面分离（plan 5.2）：
- state 只保存引用（artifact 元数据、JSONL 路径）、计数和摘要
- 数据面用任务目录中的批次文件传递，不把大数据集塞进 state
- 节点幂等：同输入 + Recipe 产生一致输出（plan 15.2 黄金回放）

Phase 1 互联网链路：WebCollectorAdapter 获取，WebParser 解析，网页 Recipe 清洗。
意图理解复用 conductor.intent_node（LLM 产 understanding，plan 10.2 不重写），
prep_planner 把 understanding 转为 DataPrepTaskSpec。
CLI 仍可预构建 spec 直接跑（run_data_prep）；chat API 走完整图（astream_data_prep）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from operator import add
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from src.cleaning.engine import RecipeEngine
from src.cleaning.models import RuleStats
from src.cleaning.profiler import ProfileAccumulator
from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.batches import BatchReference
from src.data_prep.checkpoints import Checkpoint
from src.data_prep.models import (
    DataPrepTaskSpec,
    OutputFormat,
    QualityReport,
    RawArtifact,
    RecordEnvelope,
    SelectionSpec,
    SourceLimits,
    SourceSpec,
    SourceType,
)
from src.parsers.registry import get_parser_registry
from src.quality.report import to_human_summary
from src.quality.validators import QualityAccumulator, validate as quality_validate

# 复用现有意图理解（plan 10.2：不重写，适配输出新契约）
from src.conductor.nodes.intent import intent_node

from .output import export_dataset

logger = logging.getLogger(__name__)


class DataPrepState(TypedDict, total=False):
    # ---- 输入 ----
    task_id: str
    spec: DataPrepTaskSpec
    user_input: str
    messages: List[Dict[str, str]]
    provider: Optional[str]
    model: Optional[str]
    session_id: str

    # ---- 意图 / 规划 ----
    understanding: Optional[Dict[str, Any]]       # intent_node 产出的松散理解
    needs_clarification: bool
    clarification_question: Optional[str]

    # ---- 获取（state 只存元数据，不存数据字节）----
    artifacts: List[RawArtifact]          # 原始制品元数据（含 storage_path 引用）
    acquire_warnings: List[str]

    # ---- 解析（批次引用 + 计数）----
    parsed_batches: List[BatchReference]
    parsed_count: int
    rejects_parse_count: int

    # ---- 剖析 ----
    profile: Optional[Dict[str, Any]]

    # ---- 清洗（批次引用 + 计数）----
    clean_batches: List[BatchReference]
    clean_count: int
    rejects_clean_count: int
    merged_count: int
    rule_stats: List[Dict[str, Any]]

    # ---- 质量 ----
    quality: Optional[QualityReport]
    record_counts: Dict[str, int]

    # ---- 产出 ----
    outputs: List[Any]                    # ManifestOutputEntry 列表
    manifest_path: Optional[str]
    status: str                           # SUCCEEDED | SUCCEEDED_WITH_WARNINGS | FAILED

    # ---- 可观测性 ----
    trace: Annotated[List[Dict[str, Any]], add]

    # ---- 断点续跑 ----
    checkpoint: Optional[Any]   # Checkpoint 实例（断点续跑用，plan 5.2/7.1）

    # ---- 旁路 ----
    error: Optional[str]
    needs_clarification: bool
    clarification_question: Optional[str]


def _traced(name: str):
    """节点计时包装：记录耗时与摘要到 state.trace（与旧 graph.py 一致的可观测性）。"""
    def decorator(fn):
        async def wrapper(state: DataPrepState) -> Dict[str, Any]:
            t0 = time.perf_counter()
            result = await fn(state) or {}
            ms = round((time.perf_counter() - t0) * 1000)
            result.setdefault("trace", []).append({"node": name, "ms": ms})
            return result
        return wrapper
    return decorator


# ===========================================================================
# 节点
# ===========================================================================

@_traced("prep_planner")
async def prep_planner_node(state: DataPrepState) -> Dict[str, Any]:
    """把 understanding 转为 DataPrepTaskSpec（plan 5.3 第 5 步：生成结构化计划）。

    复用 conductor.intent_node 产出的 understanding（urls/keywords/time_range/max_items），
    映射为 v2 SourceSpec + SelectionSpec。无 URL 且无关键词 -> 追问。
    """
    u = state.get("understanding") or {}
    urls = [str(x).strip() for x in (u.get("urls") or []) if str(x).strip()]
    keywords = [str(x).strip() for x in (u.get("keywords") or []) if str(x).strip()]
    time_range = u.get("time_range")
    intent = (u.get("intent") or state.get("user_input") or "数据准备").strip()
    try:
        max_items = int(u.get("max_items") or 30)
    except Exception:
        max_items = 30
    max_items = max(1, min(max_items, 2000))

    if not urls and not keywords:
        return {
            "needs_clarification": True,
            "clarification_question": (
                "想从哪个网址采集，或搜索什么关键词？"
                "例如：https://example.com 或「小米SU7 最近7天 评论」"
            ),
        }

    sources: List[SourceSpec] = []
    if urls:
        for i, url in enumerate(urls):
            sources.append(SourceSpec(
                source_id=f"web-{i + 1}", source_type=SourceType.WEB, locator=url,
                limits=SourceLimits(max_records=max_items),
                options={"max_items": max_items, "intent": intent},
            ))
    else:
        sources.append(SourceSpec(
            source_id="web-search-1", source_type=SourceType.WEB, locator="",
            limits=SourceLimits(max_records=max_items),
            options={"max_items": max_items, "keywords": keywords, "intent": intent},
        ))

    spec = DataPrepTaskSpec(
        intent=intent, sources=sources,
        selection=SelectionSpec(keywords=keywords, time_range=time_range),
    )
    return {"spec": spec, "needs_clarification": False, "clarification_question": None}


@_traced("acquire")
async def acquire_node(state: DataPrepState) -> Dict[str, Any]:
    """获取层：按 source_type 选 Connector，落盘不可变 RawArtifact。

    支持 web（WebCollectorAdapter）与 upload_file（FileConnector）。
    """
    spec = state["spec"]
    task_id = state["task_id"]
    if not spec.sources:
        return {"error": "未指定数据源", "status": "FAILED"}

    artifacts: List[RawArtifact] = []
    warnings: List[str] = []
    for src in spec.sources:
        src.options.setdefault("task_id", task_id)
        if src.source_type.value == "web":
            from src.connectors.web_adapter import WebCollectorAdapter
            adapter = WebCollectorAdapter()
            src.options.setdefault("intent", spec.intent)
            if spec.selection:
                src.options.setdefault("keywords", spec.selection.keywords)
                src.options.setdefault("time_range", spec.selection.time_range)
            async for batch in adapter.read(src):
                artifacts.extend(batch.artifacts)
                warnings.extend(batch.warnings)
                if batch.fatal_error:
                    return {"error": batch.fatal_error, "status": "FAILED",
                            "acquire_warnings": warnings, "artifacts": artifacts}
            await adapter.close()
        elif src.source_type.value == "upload_file":
            from src.connectors.file_connector import FileConnector
            from src.services.upload_store import UploadStore
            upload_store = UploadStore(
                root=settings.data_prep_upload_root,
                max_bytes=settings.data_prep_max_upload_bytes,
            )
            adapter = FileConnector(upload_store)
            async for batch in adapter.read(src):
                artifacts.extend(batch.artifacts)
                warnings.extend(batch.warnings)
                if batch.fatal_error:
                    return {"error": batch.fatal_error, "status": "FAILED",
                            "acquire_warnings": warnings, "artifacts": artifacts}
            await adapter.close()
        elif src.source_type.value == "http_api":
            from src.connectors.http_api_connector import HttpApiConnector
            adapter = HttpApiConnector()
            async for batch in adapter.read(src):
                artifacts.extend(batch.artifacts)
                warnings.extend(batch.warnings)
                if batch.fatal_error:
                    return {"error": batch.fatal_error, "status": "FAILED",
                            "acquire_warnings": warnings, "artifacts": artifacts}
            await adapter.close()
        elif src.source_type.value == "database":
            from src.connectors.database_connector import DatabaseConnector
            from src.services.db_connections import resolve_credential

            user_id = src.options.get("user_id", "")
            src.options["task_id"] = task_id
            adapter = DatabaseConnector(
                artifact_store=ArtifactStore(),
                credential_resolver=resolve_credential if src.credential_ref and user_id else None,
            )
            acquired_ckpt = None
            async for batch in adapter.read(src, checkpoint=state.get("checkpoint")):
                artifacts.extend(batch.artifacts)
                warnings.extend(batch.warnings)
                if batch.checkpoint.cursor:
                    acquired_ckpt = batch.checkpoint
                if batch.fatal_error:
                    return {"error": batch.fatal_error, "status": "FAILED",
                            "acquire_warnings": warnings, "artifacts": artifacts,
                            "checkpoint": acquired_ckpt}
            await adapter.close()
            if acquired_ckpt:
                return {"artifacts": artifacts, "acquire_warnings": warnings,
                        "record_counts": {"raw": len(artifacts)}, "checkpoint": acquired_ckpt}
        else:
            warnings.append(f"源 {src.source_id} 类型 {src.source_type} 不支持，跳过")

    if not artifacts:
        return {"error": "未获取到任何数据", "status": "FAILED",
                "acquire_warnings": warnings, "artifacts": []}
    return {
        "artifacts": artifacts,
        "acquire_warnings": warnings,
        "record_counts": {"raw": len(artifacts)},
    }


@_traced("parse")
async def parse_node(state: DataPrepState) -> Dict[str, Any]:
    """解析层：按 source_type/media_type 选 Parser，分批写 parsed JSONL。"""
    store = ArtifactStore()
    task_id = state["task_id"]
    artifacts = state.get("artifacts") or []
    registry = get_parser_registry()

    # 首版单源：web 用 WebParser，其他按 media_type/extension 选
    spec = state.get("spec")
    src_type = spec.sources[0].source_type.value if spec and spec.sources else "web"
    parser_hint = "web" if src_type == "web" else None

    batch_size = max(1, settings.data_prep_batch_records)
    checkpoint = state.get("checkpoint")
    if checkpoint is None:
        checkpoint = Checkpoint()
    parsed_batches: List[BatchReference] = []
    parse_rejects: List[Dict] = []
    total_parsed = 0
    part_no = 0
    batch: List[Dict[str, Any]] = []

    def _flush() -> None:
        nonlocal batch, part_no
        if not batch:
            return
        ref = store.append_jsonl_batch(task_id, "parsed", batch, part_no)
        parsed_batches.append(ref)
        part_no += 1
        batch.clear()

    for art in artifacts:
        if art.artifact_id in checkpoint.processed_artifact_ids:
            continue
        raw_bytes = store.read_raw_bytes(task_id, art.storage_path)
        if parser_hint:
            parser = registry.get(parser_hint)
        else:
            ext = Path(art.uri or art.storage_path).suffix.lstrip(".").lower()
            parser = registry.select(media_type=art.media_type, extension=ext)
        if parser is None:
            ext = Path(art.uri or art.storage_path).suffix.lstrip(".").lower()
            parse_rejects.append({
                "artifact_id": art.artifact_id,
                "reason": f"无匹配解析器: {art.media_type}/{ext}",
            })
            checkpoint.processed_artifact_ids.add(art.artifact_id)
            continue
        # PDF OCR 等解析可能包含较长的同步 HTTP/CPU 工作，移出事件循环，
        # 避免一个扫描件阻塞同进程的 SSE、健康检查和其他任务。
        recs, rejects = await asyncio.to_thread(parser.parse, art, raw_bytes)
        for rec in recs:
            batch.append(rec.model_dump(mode="json"))
            if len(batch) >= batch_size:
                _flush()
        parse_rejects.extend(rejects)
        total_parsed += len(recs)
        checkpoint.processed_artifact_ids.add(art.artifact_id)
    _flush()

    if parse_rejects:
        store.write_rejects(task_id, "parse", parse_rejects)

    counts = dict(state.get("record_counts") or {})
    counts["parsed"] = total_parsed
    counts["rejects_parse"] = len(parse_rejects)
    return {"parsed_batches": parsed_batches, "parsed_count": total_parsed,
            "rejects_parse_count": len(parse_rejects), "record_counts": counts,
            "checkpoint": checkpoint}


@_traced("profile_before")
async def profile_node(state: DataPrepState) -> Dict[str, Any]:
    """清洗前剖析（plan 5.3 第 11 步）：作为清洗影响基线。

    Phase 2 Task 2.5：逐批累计，不持有完整记录集（ProfileAccumulator）。
    """
    store = ArtifactStore()
    acc = ProfileAccumulator()
    acc.add_records(iter_records(store, state.get("parsed_batches")))
    return {"profile": acc.finalize().to_dict()}


@_traced("clean")
async def clean_node(state: DataPrepState) -> Dict[str, Any]:
    """清洗层：RecipeEngine 逐批执行，分批写 clean + rejects + lineage JSONL。

    Phase 2 Task 2.5：逐批 execute_batch + 逐批写 clean_batches，不一次性
    持有全部 parsed 记录。跨批去重用 record_id 集合（不存完整记录，plan 2.5）。
    rule_stats 跨批累加；跨批去重计入 merged。
    """
    store = ArtifactStore()
    task_id = state["task_id"]
    spec = state["spec"]
    engine = RecipeEngine()
    rule_params: Dict[str, Dict] = {}
    limits = spec.sources[0].limits if spec.sources else None
    if limits and limits.max_records:
        rule_params["web_cap_max_items"] = {"max_items": limits.max_records}
    # web 链路用默认网页规则；upload_file/http_api 等结构化数据不用，避免误隔离
    src_type = spec.sources[0].source_type.value if spec.sources else "web"
    use_web_defaults = src_type == "web"

    batch_size = max(1, settings.data_prep_batch_records)
    clean_batches: List[BatchReference] = []
    all_rejects = []
    rule_stats_acc: Dict[str, RuleStats] = {}
    seen_ids: set = set()           # 跨批去重（record_id）
    cross_merged = 0
    total_clean = 0
    part_no = 0

    for rec_batch in _iter_record_batches(
        store, state.get("parsed_batches"), batch_size
    ):
        result = engine.execute_batch(
            rec_batch, spec.cleaning_recipe, rule_params=rule_params,
            use_web_defaults=use_web_defaults,
        )
        # 跨批去重（批内已由 dedup 规则处理，此处补跨批同记录）
        deduped: List[RecordEnvelope] = []
        for r in result.clean:
            if r.record_id in seen_ids:
                cross_merged += 1
                continue
            seen_ids.add(r.record_id)
            deduped.append(r)
        clean_rows = [{**r.data, "_record_id": r.record_id} for r in deduped]
        if clean_rows:
            ref = store.append_jsonl_batch(
                task_id, "clean_batches", clean_rows, part_no
            )
            clean_batches.append(ref)
            part_no += 1
        total_clean += len(deduped)
        all_rejects.extend(result.rejects)
        # 逐批写 lineage（不全量驻留内存，plan Phase 2 Task 12）
        batch_lineage = [{
            "record_id": r.record_id,
            "artifact_id": (r.meta or {}).get("artifact_id"),
            "source_id": (r.meta or {}).get("source_id"),
            "content_hash": (r.meta or {}).get("content_hash"),
            "parser": (r.meta or {}).get("parser"),
        } for r in deduped]
        if batch_lineage:
            store.append_jsonl(task_id, "lineage/records.jsonl", batch_lineage)
        for s in result.rule_stats:
            acc = rule_stats_acc.setdefault(
                s.rule_id, RuleStats(rule_id=s.rule_id, stage=s.stage)
            )
            acc.input_count += s.input_count
            acc.output_count += s.output_count
            acc.isolated_count += s.isolated_count
            acc.merged_count += s.merged_count

    reject_rows = [
        {"record_id": rj.record.record_id, "reason": rj.reason, "rule_id": rj.rule_id,
         "stage": rj.stage.value, "artifact_id": (rj.record.meta or {}).get("artifact_id")}
        for rj in all_rejects
    ]
    if reject_rows:
        store.write_rejects(task_id, "clean", reject_rows)
    # lineage 已在 clean_node 循环内逐批 append（不全量驻留）

    rule_stats_list = list(rule_stats_acc.values())
    merged_total = sum(s.merged_count for s in rule_stats_list) + cross_merged
    counts = dict(state.get("record_counts") or {})
    counts["clean"] = total_clean
    counts["rejects_clean"] = len(all_rejects)
    counts["merged"] = merged_total
    return {
        "clean_batches": clean_batches,
        "clean_count": total_clean,
        "rejects_clean_count": len(all_rejects),
        "merged_count": merged_total,
        "rule_stats": [s.to_dict() for s in rule_stats_list],
        "record_counts": counts,
    }


@_traced("validate")
async def validate_node(state: DataPrepState) -> Dict[str, Any]:
    """质量门：确定性维度校验（plan 9，ADR-0003）。

    Phase 2 Task 2.5 阶段2：逐批累计字段非空/主键去重，不持有完整记录集。
    血缘覆盖率从 lineage 文件 + clean_count 计算（不依赖 clean_records list）。
    """
    store = ArtifactStore()
    task_id = state["task_id"]
    spec = state["spec"]

    # 逐批累计质量统计（不持有完整记录集）
    target_schema = spec.target_schema
    required = (
        [f.name for f in target_schema.fields if f.required]
        if target_schema and target_schema.fields else []
    )
    primary_key = target_schema.primary_key if target_schema else []
    acc = QualityAccumulator(required_fields=required, primary_key=primary_key)
    batch_size = max(1, settings.data_prep_batch_records)
    for rec_batch in _iter_record_batches(store, state.get("clean_batches"), batch_size):
        acc.add_batch(rec_batch)

    # 血缘覆盖率：逐行读 lineage 文件算（有 artifact_id 的行数 / clean 记录数）
    # 逐行读避免全量物化 lineage（plan Phase 2 Task 12）
    clean_count = (state.get("record_counts") or {}).get("clean", 0)
    lineage_with_artifact = 0
    lineage_rel = f"{task_id}/lineage/records.jsonl"
    try:
        for r in store.iter_jsonl(lineage_rel):
            if r.get("artifact_id"):
                lineage_with_artifact += 1
    except FileNotFoundError:
        pass
    lineage_coverage = (lineage_with_artifact / clean_count) if clean_count else 1.0

    report = quality_validate(
        record_counts=state.get("record_counts") or {},
        artifacts=state.get("artifacts") or [],
        policy=spec.quality_policy,
        target_schema=spec.target_schema,
        lineage_coverage=lineage_coverage,
        accumulated=acc,
    )
    report.task_id = task_id
    store.write_quality(task_id, report)
    status = "SUCCEEDED" if report.overall.value == "pass" else "SUCCEEDED_WITH_WARNINGS"
    if report.overall.value == "fail":
        status = "FAILED"
    return {"quality": report, "status": status}


@_traced("output")
async def output_node(state: DataPrepState) -> Dict[str, Any]:
    """产物交付：导出数据集 + Manifest + Schema + Trace（plan 5.3 第 14 步）。

    Phase 2 Task 2.5 阶段2：逐批导出，不持有完整记录集。schema 优先 target_schema，
    否则用 export_dataset 逐批推断的 schema。
    """
    store = ArtifactStore()
    task_id = state["task_id"]
    spec = state["spec"]

    # 质量 fail 时只交付诊断产物，不把任何数据标记为干净输出。
    quality = state.get("quality")
    quality_failed = quality is not None and quality.overall.value == "fail"
    if quality_failed:
        output_entries = []
        inferred_schema = {
            "fields": [],
            "inferred": True,
            "record_count": (state.get("record_counts") or {}).get("clean", 0),
        }
    else:
        formats = spec.outputs or [OutputFormat.JSONL, OutputFormat.PARQUET]
        output_entries, inferred_schema = export_dataset(
            store, task_id, state.get("clean_batches"), formats
        )

    # Schema（目标优先，否则用逐批推断）
    schema = spec.target_schema.model_dump(mode="json") if spec.target_schema else inferred_schema
    schema_path = store.write_schema(task_id, schema)

    # Recipe
    recipe_path = store.write_recipe(task_id, spec.cleaning_recipe)

    # Manifest
    manifest_path = store.write_manifest(
        task_id,
        artifacts=state.get("artifacts") or [],
        outputs=output_entries,
        record_counts=state.get("record_counts") or {},
        recipe_version=spec.cleaning_recipe.version,
        schema_ref=str(schema_path),
        quality_ref="quality_report.json",
        lineage_ref="lineage/records.jsonl",
    )

    # Trace
    store.write_trace(task_id, state.get("trace") or [])

    summary = to_human_summary(state["quality"]) if state.get("quality") else ""
    logger.info("任务 %s 产出完成: %s\n%s", task_id, manifest_path, summary)

    return {
        "outputs": [e.model_dump(mode="json") for e in output_entries],
        "manifest_path": str(manifest_path),
    }


# ===========================================================================
# 工具
# ===========================================================================

def iter_records(store: ArtifactStore, batches: Optional[List[BatchReference]]) -> Iterator[RecordEnvelope]:
    """从批次引用逐条读回记录信封（生成器，不全量物化，plan Phase 2 Task 2.5）。

    parsed_batches 行含 data+meta；clean_batches 行为 {_record_id, ...业务字段}。
    """
    for ref in batches or []:
        for row in store.iter_jsonl(ref.path):
            if "data" in row and "meta" in row:
                yield RecordEnvelope.model_validate(row)
            else:
                rid = row.pop("_record_id", "") or ""
                yield RecordEnvelope(record_id=rid, data=row, meta={})


def _iter_record_batches(
    store: ArtifactStore,
    batches: Optional[List[BatchReference]],
    size: int,
) -> Iterator[List[RecordEnvelope]]:
    """按 size 分批产出记录列表（逐批消费，批间释放，plan Phase 2 Task 2.5）。"""
    batch: List[RecordEnvelope] = []
    for rec in iter_records(store, batches):
        batch.append(rec)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# ===========================================================================
# 图构建
# ===========================================================================

def _route_after_start(state: DataPrepState) -> str:
    """有预构建 spec（CLI）-> 直接 acquire；否则 -> intent 理解（chat）。"""
    if state.get("spec"):
        return "acquire"
    return "intent"


def _route_after_intent(state: DataPrepState) -> str:
    if state.get("error") or state.get("needs_clarification"):
        return "end"
    return "prep_planner"


def _route_after_prep_planner(state: DataPrepState) -> str:
    if state.get("needs_clarification"):
        return "end"
    return "acquire"


def _route_after_acquire(state: DataPrepState) -> str:
    if state.get("error"):
        return "end"
    return "parse"


def _route_after_parse(state: DataPrepState) -> str:
    if state.get("parsed_count", 0) == 0 and state.get("rejects_parse_count", 0) == 0:
        return "end"
    return "profile_before"


def build_graph(checkpointer=None):
    """构建并编译数据准备图。

    START -> [有 spec? -> acquire : intent] -> prep_planner -> acquire -> parse
    -> profile_before -> clean -> validate -> output -> END
    """
    g = StateGraph(DataPrepState)
    g.add_node("intent", _traced("intent")(intent_node))
    g.add_node("prep_planner", prep_planner_node)
    g.add_node("acquire", acquire_node)
    g.add_node("parse", parse_node)
    g.add_node("profile_before", profile_node)
    g.add_node("clean", clean_node)
    g.add_node("validate", validate_node)
    g.add_node("output", output_node)

    g.add_conditional_edges(START, _route_after_start, {"intent": "intent", "acquire": "acquire"})
    g.add_conditional_edges("intent", _route_after_intent, {"prep_planner": "prep_planner", "end": END})
    g.add_conditional_edges("prep_planner", _route_after_prep_planner, {"acquire": "acquire", "end": END})
    g.add_conditional_edges("acquire", _route_after_acquire, {"parse": "parse", "end": END})
    g.add_conditional_edges("parse", _route_after_parse, {"profile_before": "profile_before", "end": END})
    g.add_edge("profile_before", "clean")
    g.add_edge("clean", "validate")
    g.add_edge("validate", "output")
    g.add_edge("output", END)
    return g.compile(checkpointer=checkpointer)


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_data_prep(
    spec: DataPrepTaskSpec,
    task_id: str,
    checkpoint: Optional[Checkpoint] = None,
) -> Dict[str, Any]:
    """运行数据准备图。spec 预构建（CLI 或上层构造），task_id 隔离工作目录。

    有 spec -> 跳过 intent/prep_planner 直接 acquire（_route_after_start）。
    """
    spec.task_id = task_id
    init: DataPrepState = {"task_id": task_id, "spec": spec}
    if checkpoint is not None:
        init["checkpoint"] = checkpoint
    return dict(await get_graph().ainvoke(init))


# ===========================================================================
# 流式运行（chat API 用）
# ===========================================================================

def _build_data_prep_view(node: str, delta: Dict[str, Any], values: Dict[str, Any]) -> str:
    """data_prep 节点的中文白盒摘要（供前端打字机展示，风格同 conductor.node_views）。"""
    delta = delta or {}
    values = values or {}

    if node == "intent":
        if delta.get("needs_clarification"):
            return f"🤔 需要您补充信息：{delta.get('clarification_question') or '请补充更多细节'}"
        u = delta.get("understanding") or {}
        intent = u.get("intent", "") or ""
        return f"已理解需求：{intent}" if intent else "已理解需求，准备规划。"

    if node == "prep_planner":
        if delta.get("needs_clarification"):
            return f"🤔 {delta.get('clarification_question') or '请补充数据源'}"
        spec = delta.get("spec")
        if spec is None:
            return "正在规划数据准备任务…"
        sources = getattr(spec, "sources", []) or []
        urls = [s.locator for s in sources if getattr(s, "locator", "")]
        limits = getattr(sources[0], "limits", None) if sources else None
        max_items = getattr(limits, "max_records", None) if limits else None
        parts = [f"{len(sources)} 个数据源"]
        if urls:
            parts.append(f"URL: {urls[0]}{'…' if len(urls) > 1 else ''}")
        if max_items:
            parts.append(f"最多 {max_items} 条")
        return f"📋 任务规划：{', '.join(parts)}"

    if node == "acquire":
        artifacts = delta.get("artifacts") or values.get("artifacts") or []
        warnings = delta.get("acquire_warnings") or []
        if delta.get("error"):
            return f"❌ 采集失败：{delta['error']}"
        text = f"📥 采集到 {len(artifacts)} 条原始数据"
        if warnings:
            text += "\n" + "\n".join(str(w) for w in warnings[:2])
        return text

    if node == "parse":
        n = delta.get("parsed_count", 0)
        rj = delta.get("rejects_parse_count", 0)
        text = f"🔍 解析完成：{n} 条记录"
        if rj:
            text += f"（{rj} 条解析隔离）"
        return text

    if node == "profile_before":
        prof = delta.get("profile") or {}
        n = prof.get("record_count", 0)
        fields = prof.get("fields", []) or []
        dup = prof.get("dup_count", 0)
        text = f"📊 数据剖析：{n} 条，{len(fields)} 个字段"
        if dup:
            text += f"（疑似重复 {dup} 条）"
        return text

    if node == "clean":
        counts = values.get("record_counts") or {}
        clean_n = counts.get("clean", delta.get("clean_count", 0))
        rejects = counts.get("rejects_clean", delta.get("rejects_clean_count", 0))
        rule_stats = delta.get("rule_stats") or []
        text = f"🧹 清洗完成：{clean_n} 条干净数据"
        if rejects:
            text += f"（隔离 {rejects} 条）"
        isolated_rules = [s for s in rule_stats if s.get("isolated", 0) > 0]
        if isolated_rules:
            reasons = "、".join(f"{s['rule_id']} {s['isolated']}条" for s in isolated_rules[:3])
            text += f"\n隔离原因：{reasons}"
        return text

    if node == "validate":
        q = delta.get("quality")
        if not q:
            return "质量校验已跳过"
        overall = getattr(q, "overall", None)
        overall_val = overall.value if hasattr(overall, "value") else str(overall)
        label = {"pass": "✅ 通过", "warn": "⚠️ 告警", "fail": "❌ 失败"}.get(overall_val, overall_val)
        dims = getattr(q, "dimensions", []) or []
        return f"✅ 质量校验：{label}（{len(dims)} 个维度）"

    if node == "output":
        outputs = delta.get("outputs") or []
        manifest = delta.get("manifest_path")
        if not outputs:
            return "📦 产出已生成"
        fmts = ", ".join(str(o.get("format", "?")) for o in outputs)
        text = f"📦 已产出 {len(outputs)} 个文件（{fmts}）"
        if manifest:
            text += "\nManifest 已生成"
        return text

    return "处理完成"


async def astream_data_prep(
    user_input: str,
    messages: Optional[List[Dict[str, str]]] = None,
    provider: Optional[str] = None,
    *,
    model: Optional[str] = None,
    session_id: str = "default",
    task_id: Optional[str] = None,
) -> AsyncIterator[Tuple[str, Any]]:
    """流式运行数据准备图（chat API 用）。

    依次产出 ("node", {"node": name, "view": 摘要}) 表示某节点完成，
    最后产出 ("final", 最终状态字典)。
    """
    tid = task_id or (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    init: DataPrepState = {
        "task_id": tid,
        "user_input": user_input,
        "messages": messages or [{"role": "user", "content": user_input}],
        "provider": provider,
        "model": model,
        "session_id": session_id,
    }
    stream = get_graph().astream(init, stream_mode=["updates", "values"])
    final_state: Dict[str, Any] = {}
    async for mode, chunk in stream:
        if mode == "values":
            final_state = chunk if isinstance(chunk, dict) else final_state
        elif mode == "updates" and isinstance(chunk, dict):
            for node_name, delta in chunk.items():
                view = _build_data_prep_view(node_name, delta or {}, final_state)
                yield ("node", {"node": node_name, "view": view})
    yield ("final", dict(final_state))
