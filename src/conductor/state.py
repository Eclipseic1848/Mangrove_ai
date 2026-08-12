"""
Conductor 状态定义（LangGraph State）

每个节点读取状态、返回部分更新。对话历史 messages 由前端逐轮传入，
任务规格 task_spec 在 Planner 节点产生后贯穿后续环节。
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from .task_spec import TaskSpec


class ConductorState(TypedDict, total=False):
    # ---- 输入 ----
    messages: List[Dict[str, str]]      # 完整对话历史：[{"role","content"}]
    user_input: str                     # 本轮用户输入
    provider: Optional[str]             # 模型供应商覆盖（None=用默认）
    model: Optional[str]                # 具体模型覆盖（None=用该供应商默认模型）
    session_id: str                     # 会话/线程标识
    task_id: str                        # 本次任务标识（用于隔离工作目录）

    # ---- 意图阶段 ----
    needs_clarification: bool           # 是否需要向用户追问
    clarification_question: Optional[str]
    understanding: Optional[Dict[str, Any]]  # 意图节点产出的松散理解（供 Planner 规划）
    spec_draft: Optional[Dict[str, Any]]     # 兼容旧引用（新链路不再产出）
    plan_reasoning: Optional[str]            # Planner 的规划理由（白盒回显/日志）
    task_spec: Optional[TaskSpec]       # 结构化任务规格

    # ---- 采集/清洗/分析 ----
    collector_candidates: List[str]     # 路由挑选出的候选采集器名（按优先级）
    collector_used: str                 # 实际使用的采集器名
    collector_notes: List[str]          # 采集过程给用户的说明（如登录过期/全网兜底），供 output 透出
    collector_attempts: List[Dict[str, Any]]  # 各候选采集器的执行结果，供 trace/排障展示
    raw_dataset: List[Dict[str, Any]]   # 原始数据
    cleaned_dataset: List[Dict[str, Any]]  # 清洗后数据
    clean_stats: Optional[Dict[str, int]]  # 清洗剔除统计：{原因: 条数}（质量门透明化，白盒卡片透出）
    analysis: Optional[str]             # 分析结果（Markdown 正文）
    analysis_source: str                # 分析模板来源：voc|builtin|learned|fallback（fallback 时可提议沉淀模板）
    analysis_route: Optional[str]       # 模板路由依据（词表信号/LLM分类，白盒透出，P0-4）

    # ---- 质量评估 / 自学习（Phase 3）----
    quality: Optional[Dict[str, Any]]   # Checker 评估：{score, passed, issues, summary}
    analyze_reruns: int                 # 质检闭环已触发的重跑次数（P1-2，限 1 次防死循环）
    checker_feedback: Optional[List[str]]  # 质检问题清单（触发重跑时传给 analyze，消费后清空）
    template_saved: Optional[Dict[str, str]]  # 自动沉淀的模板：{slug, title}（自学习阶段2）
    template_slug: Optional[str]        # 本次命中复用的已学模板 slug（source=learned 时），供回写使用统计
    template_status: Optional[Dict[str, str]]  # 模板状态变更：{slug, status}（草稿转正 active / 淘汰 retired）

    # ---- 可观测性（Phase 3）----
    # trace 由节点计时包装器逐节点追加（reducer=add 累加成完整执行轨迹）
    trace: Annotated[List[Dict[str, Any]], add]  # [{node, ms, summary}]
    grade: Optional[Dict[str, Any]]     # 任务质量分级：{level, score, items, collector}

    # ---- 人类接管（HITL）----
    approved_db_write: bool             # 用户是否已确认入库

    # ---- 定时任务（期2）----
    ignore_schedule: bool               # True 时忽略 schedule 直接跑流程（调度器自身触发用）
    schedule_request: Optional[str]     # 检测到的定时请求（原始 schedule 串），供前端注册

    # ---- 产出 ----
    outputs: Dict[str, Any]             # 产出物：{"report_md": path, "json": path, "db": ...}
    reply: str                          # 给用户的最终回复

    # ---- 旁路 ----
    error: Optional[str]                # 错误信息（任一环节失败）
    target_manifest: List[Dict[str, Any]]  # 显式链接目标清单
    evidence_bundles: List[Dict[str, Any]]  # 含来源的视频证据
    evidence_ready: bool                # 显式视频证据是否充足
    supplemental_dataset: List[Dict[str, Any]]  # 补充资料，不混入主数据
    target_coverage: Optional[Dict[str, int]]  # 总数、已校验数、证据就绪数
