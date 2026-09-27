"""
Conductor 编排图（LangGraph）。

流程：
  intent → [需澄清/出错? → END] → planner → [需澄清(如未定数量)? → END]
         → router → collect → [无数据? → END] → clean → analyze → checker → output → END

对话的多轮澄清由前端管理会话历史，每轮把完整 messages 传入；
当 intent 或 planner 判定需澄清（如用户未明确采集数量）时图提前结束，
前端把问题抛给用户，下一轮带历史再进入。
"""
from __future__ import annotations

import logging
import asyncio
import time
import uuid
from datetime import datetime
from src.timezone import now as beijing_now
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

from langgraph.graph import END, START, StateGraph

from .nodes import (
    analyze_node,
    checker_node,
    clean_node,
    collect_node,
    intent_node,
    output_node,
    planner_node,
    router_node,
    schedule_node,
    target_resolve_node,
    video_enrich_node,
)
from .node_views import build_node_view
from .progress import LABELS, emit_progress, completed_summary
from .state import ConductorState
from .targets import is_direct_video_manifest

logger = logging.getLogger(__name__)


def _route_after_intent(state: ConductorState) -> str:
    if state.get("error"):
        return "end"
    if state.get("needs_clarification"):
        return "end"
    return "planner"


def _route_after_planner(state: ConductorState) -> str:
    """规划后的路由。

    需追问（如未明确采集数量）→ 提前结束，前端把问题抛给用户，下一轮再进入；
    带 schedule 且非调度器自身触发 → 短路到 schedule 节点（只回执，不采集）。
    """
    if state.get("needs_clarification"):
        return "end"
    spec = state.get("task_spec")
    if spec and getattr(spec, "schedule", None) and not state.get("ignore_schedule"):
        return "schedule"
    return "target_resolve"


def _route_after_collect(state: ConductorState) -> str:
    if state.get("evidence_collection") or state.get("bank_collection"):
        return "output"
    if is_direct_video_manifest(state.get("target_manifest") or []):
        return "video_enrich"
    return "clean" if state.get("raw_dataset") else "end"


def _route_after_video_enrich(state: ConductorState) -> str:
    """视频证据不足时直接交给质检和产出，禁止进入普通分析。"""
    if not state.get("evidence_ready"):
        return "checker"
    return "clean"


def _route_after_checker(state: ConductorState) -> str:
    """质检闭环（P1-2）：不达标且 checker 刚下发了问题清单 → 打回 analyze 重做。

    防死循环：checker 仅在 analyze_reruns 为 0 时下发 checker_feedback（限 1 次），
    analyze 消费后即清空该标记，故第二轮质检后必然走 output。
    """
    q = state.get("quality") or {}
    if state.get("checker_feedback") and not q.get("passed", True):
        return "analyze"
    return "output"


def _node_summary(name: str, result: Dict[str, Any], state: ConductorState) -> str:
    """从节点返回值提取一句话摘要，用于执行轨迹（可观测性）。"""
    r = result or {}
    if name == "intent":
        return "需澄清" if r.get("needs_clarification") else ("出错" if r.get("error") else "意图已解析")
    if name == "router":
        cands = r.get("collector_candidates") or state.get("collector_candidates") or []
        return f"候选:{','.join(cands)}" if cands else "无候选"
    if name == "collect":
        used = r.get("collector_used") or state.get("collector_used") or "—"
        attempts = r.get("collector_attempts") or []
        if attempts:
            chain = "→".join(
                f"{item.get('collector', '—')}({'成功' if item.get('success') else '失败'})"
                for item in attempts
            )
            return f"{chain}；采用{used}/{len(r.get('raw_dataset') or [])}条"
        return f"{used}/{len(r.get('raw_dataset') or [])}条"
    if name == "video_enrich":
        coverage = r.get("target_coverage") or {}
        return f"证据就绪 {coverage.get('evidence_ready', 0)}/{coverage.get('total', 0)} 个目标"
    if name == "clean":
        return f"{len(r.get('cleaned_dataset') or [])}条"
    if name == "analyze":
        return f"模板={r.get('analysis_source') or '—'}"
    if name == "checker":
        q = r.get("quality")
        if not q:
            return "跳过"
        s = f"{q.get('score')}分/{'过' if q.get('passed') else '未过'}"
        return s + "→重跑分析" if r.get("checker_feedback") else s
    if name == "output":
        return "已保留诊断，任务未完成" if r.get("error") else "已产出"
    if name == "schedule":
        return "已识别定时任务"
    return ""


def _traced(name: str, fn: Callable[[ConductorState], Awaitable[Dict[str, Any]]]):
    """包装节点：记录耗时与摘要，追加到 state.trace（reducer 累加）。"""
    async def wrapper(state: ConductorState) -> Dict[str, Any]:
        from src.api.execution import execution_checkpoint
        execution_checkpoint()
        if state.get("budget_exhausted"):
            return {}
        t0 = time.perf_counter()
        emit_progress(name, "started", f"正在{LABELS.get(name, '处理任务')}…")
        spec = state.get("task_spec")
        budget = getattr(spec, "time_budget_seconds", None)
        started = state.get("execution_started_at") or time.time()
        remaining = budget - (time.time() - started) if budget is not None else None
        result = None
        timeout = asyncio.timeout(max(0, remaining)) if remaining is not None else None
        if remaining is not None and remaining <= 0:
            result = {"budget_exhausted": True}
        elif timeout is not None:
            partial_result = {}
            try:
                async with timeout:
                    try:
                        result = await fn(state) or {}
                    except asyncio.CancelledError as exc:
                        from .nodes.collect import CollectionInterrupted
                        if isinstance(exc, CollectionInterrupted):
                            partial_result = exc.partial_result
                        raise
            except TimeoutError:
                if not timeout.expired():
                    raise
                result = {**partial_result, "budget_exhausted": True}
        else:
            result = await fn(state) or {}
        # 规划前尚不知道自然语言预算；识别后立即计入入口以来的全部已耗时间。
        planned_budget = getattr(result.get("task_spec"), "time_budget_seconds", budget)
        if result.get("budget_exhausted") or (planned_budget is not None and time.time() - started >= planned_budget):
            message = "已达到本次任务的总耗时上限，停止后续步骤；已完成数据保留，在途外部请求可能结果未知，不自动重发。"
            result.update(budget_exhausted=True, error=message, reply=message, checker_feedback=None)
        from src.llm.provider import verify_bound_model
        verify_bound_model()
        execution_checkpoint()
        emit_progress(name, "failed" if result.get("error") else "waiting" if result.get("needs_clarification") else "completed", completed_summary(name, result))
        ms = round((time.perf_counter() - t0) * 1000)
        entry = {"node": name, "ms": ms, "summary": _node_summary(name, result, state)}
        return {**result, "execution_started_at": started, "trace": [entry]}
    return wrapper


def build_graph(checkpointer=None):
    """构建并编译 Conductor 图。

    checkpointer 不为空时挂检查点（断点续跑）；此时调用方必须在 config 里传 thread_id。
    """
    g = StateGraph(ConductorState)
    # 每个节点用 _traced 包装：记录耗时与摘要到 state.trace（可观测性）
    g.add_node("intent", _traced("intent", intent_node))
    g.add_node("planner", _traced("planner", planner_node))
    g.add_node("target_resolve", _traced("target_resolve", target_resolve_node))
    g.add_node("router", _traced("router", router_node))
    g.add_node("collect", _traced("collect", collect_node))
    g.add_node("video_enrich", _traced("video_enrich", video_enrich_node))
    g.add_node("clean", _traced("clean", clean_node))
    g.add_node("analyze", _traced("analyze", analyze_node))
    g.add_node("checker", _traced("checker", checker_node))
    g.add_node("output", _traced("output", output_node))
    g.add_node("schedule", _traced("schedule", schedule_node))

    g.add_edge(START, "intent")
    g.add_conditional_edges("intent", _route_after_intent, {"planner": "planner", "end": END})
    g.add_conditional_edges(
        "planner", _route_after_planner, {"target_resolve": "target_resolve", "schedule": "schedule", "end": END}
    )
    g.add_edge("target_resolve", "router")
    g.add_edge("schedule", END)
    g.add_edge("router", "collect")
    g.add_conditional_edges("collect", _route_after_collect, {"video_enrich": "video_enrich", "clean": "clean", "output": "output", "end": END})
    g.add_conditional_edges("video_enrich", _route_after_video_enrich, {"clean": "clean", "checker": "checker"})
    g.add_edge("clean", "analyze")
    g.add_edge("analyze", "checker")
    # 质检闭环（P1-2）：不达标带问题清单打回 analyze 重做 1 次，其余进 output
    g.add_conditional_edges("checker", _route_after_checker, {"analyze": "analyze", "output": "output"})
    g.add_edge("output", END)
    return g.compile(checkpointer=checkpointer)


# 编译一次，重复使用
_GRAPH = None
# 带持久检查点的图与其 saver（懒加载，进程内复用同一连接）
_CKPT_GRAPH = None
_CKPT_SAVER = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def probe_checkpoint_storage() -> str:
    """探测断点续跑数据库所在目录是否可写；只写一个探测文件并立即删除，不碰真实检查点数据。

    没有远程服务可连，"自检"实际测的是本地存储是否可用；供设置页/配置中心两处自检共用，避免各写一份。
    同步阻塞 I/O，调用方需自行 asyncio.to_thread。
    """
    from pathlib import Path

    from src.config.settings import settings as _settings

    db_path = Path(_settings.checkpoint_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    probe = db_path.parent / ".checkpoint_write_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return str(db_path)


async def _get_checkpoint_graph():
    """懒加载带持久 AsyncSqliteSaver 的图（断点续跑用）。连接在当前事件循环内创建并复用。"""
    global _CKPT_GRAPH, _CKPT_SAVER
    if _CKPT_GRAPH is None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        from pathlib import Path as _Path
        from src.config.settings import settings as _settings

        db_path = _settings.checkpoint_db_path
        _Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(db_path)
        _CKPT_SAVER = AsyncSqliteSaver(conn)
        await _CKPT_SAVER.setup()
        _CKPT_GRAPH = build_graph(checkpointer=_CKPT_SAVER)
        logger.info("已启用断点续跑检查点：%s", db_path)
    return _CKPT_GRAPH


def _build_init(
    user_input: str,
    messages: Optional[List[Dict[str, str]]],
    provider: Optional[str],
    model: Optional[str],
    session_id: str,
    approved_db_write: bool,
    ignore_schedule: bool,
    task_id: Optional[str] = None,
) -> ConductorState:
    # task_id 传入则复用（断点续跑同一任务）；否则新建并加 6 位随机后缀，
    # 同一秒并发的多个任务不会共用 downloads/<task_id>/ 目录（并行不踩踏）。
    tid = task_id or (beijing_now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    import hashlib
    import json
    from src.memory._library_scope import execution_owner
    from src.llm.provider import _bound_chat_identity
    binding = hashlib.sha256(json.dumps([execution_owner(), _bound_chat_identity.get()], ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        "user_input": user_input,
        "messages": messages or [{"role": "user", "content": user_input}],
        "provider": provider,
        "model": model,
        "session_id": session_id,
        "task_id": tid,
        "execution_binding": binding,
        "execution_started_at": time.time(),
        "approved_db_write": approved_db_write,
        "ignore_schedule": ignore_schedule,
    }


async def _collection_checkpoint_input(graph, init, config):
    saved = (await graph.aget_state(config)).values
    spec = saved.get("task_spec") if saved else None
    if not spec or not (getattr(spec, "bank_benefits", None) or getattr(spec, "evidence_collection", None)):
        return init
    # 同 task_id 的采集恢复不能重新规划，否则冻结日期和范围改变，长节点进度无法复用。
    keys = ("execution_binding", "user_input", "provider", "model", "session_id", "approved_db_write", "ignore_schedule")
    if any(saved.get(key) != init.get(key) for key in keys):
        raise ValueError("采集检查点与当前身份、模型或任务输入不一致，拒绝恢复")
    return None


async def _ainvoke(init: ConductorState) -> Dict[str, Any]:
    """按是否启用检查点选择图并执行；启用时以 task_id 作为 thread_id 落盘各节点状态。"""
    from src.config.settings import settings as _settings

    if _settings.checkpoint_enabled:
        graph = await _get_checkpoint_graph()
        config = {"configurable": {"thread_id": init["task_id"]}}
        value = await _collection_checkpoint_input(graph, init, config)
        return dict(await graph.ainvoke(value, config=config))
    return dict(await get_graph().ainvoke(init))


async def run_conductor(
    user_input: str,
    messages: Optional[List[Dict[str, str]]] = None,
    provider: Optional[str] = None,
    *,
    model: Optional[str] = None,
    session_id: str = "default",
    approved_db_write: bool = False,
    ignore_schedule: bool = False,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """运行一轮 Conductor，返回最终状态字典。

    ignore_schedule=True 时忽略 task_spec.schedule 直接执行采集流程（供调度器到点触发用）。
    task_id 传入则复用该 id；启用检查点时，用同一 task_id 重跑即从断点恢复。
    """
    init = _build_init(
        user_input, messages, provider, model, session_id, approved_db_write,
        ignore_schedule, task_id,
    )
    return await _ainvoke(init)


async def astream_conductor(
    user_input: str,
    messages: Optional[List[Dict[str, str]]] = None,
    provider: Optional[str] = None,
    *,
    model: Optional[str] = None,
    session_id: str = "default",
    approved_db_write: bool = False,
    ignore_schedule: bool = False,
    task_id: Optional[str] = None,
) -> AsyncIterator[Tuple[str, Any]]:
    """流式运行 Conductor：

    依次产出 ("node", 节点名) 表示某节点执行完毕（用于前端进度展示），
    最后产出一次 ("final", 最终状态字典)。
    task_id 传入则复用；启用检查点时以 task_id 作为 thread_id 落盘、支持断点续跑。
    """
    init = _build_init(
        user_input, messages, provider, model, session_id, approved_db_write,
        ignore_schedule, task_id,
    )
    from src.config.settings import settings as _settings

    if _settings.checkpoint_enabled:
        graph = await _get_checkpoint_graph()
        config = {"configurable": {"thread_id": init["task_id"]}}
        value = await _collection_checkpoint_input(graph, init, config)
        stream = graph.astream(value, config=config, stream_mode=["updates", "values", "custom"])
    else:
        stream = get_graph().astream(init, stream_mode=["updates", "values", "custom"])
    async for event in _stream_events(stream):
        yield event


async def astream_collection_recovery(state):
    # 仅重进原采集与快照节点；恢复不得重新理解或规划用户的冻结范围。
    graph = StateGraph(ConductorState)
    graph.add_node("collect", _traced("collect", collect_node))
    graph.add_node("output", _traced("output", output_node))
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "output")
    graph.add_edge("output", END)
    stream = graph.compile().astream(state, stream_mode=["updates", "values", "custom"])
    async for event in _stream_events(stream):
        yield event


async def _stream_events(stream):
    final_state: Dict[str, Any] = {}
    async for mode, chunk in stream:
        if mode == "custom" and isinstance(chunk, dict):
            yield ("progress", chunk)
        if mode == "values":
            final_state = chunk if isinstance(chunk, dict) else final_state
        elif mode == "updates" and isinstance(chunk, dict):
            for node_name, delta in chunk.items():
                view = build_node_view(node_name, delta or {}, final_state)
                yield ("node", {"node": node_name, "view": view})
    yield ("final", dict(final_state))
