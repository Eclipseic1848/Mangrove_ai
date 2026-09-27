"""
TaskSpec —— 全局结构化任务契约

它是意图理解的产物，也是路由、采集、清洗、分析、产出各环节的统一依据。
用 pydantic 做确定性校验（Harness 验证机制）。
"""
from __future__ import annotations

from enum import Enum
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from src.timezone import now as beijing_now


class DataType(str, Enum):
    """要采集的数据类型。"""
    COMMENT = "comment"        # 评论/槽点（社媒、电商）
    POST = "post"              # 帖子/笔记/视频信息
    BID = "bid"                # 招投标/政务标讯
    PRODUCT = "product"        # 商品信息
    ARTICLE = "article"        # 新闻/资讯/文章
    GENERIC = "generic"        # 通用网页内容


class LoginStrategy(str, Enum):
    """登录策略：偏好免登录，必要时用 cookie / 已登录会话。"""
    NONE = "none"
    COOKIE = "cookie"
    SESSION = "session"


class OutputFormat(str, Enum):
    """产出形式（可多选）。"""
    REPORT_MD = "report_md"    # Markdown 分析报告
    JSON = "json"              # 结构化 JSON 数据
    DB = "db"                  # 入数据库（敏感动作，走 HITL 确认）
    EMAIL = "email"            # 邮件报告；宿主核对用户原文授权后发送
    SLACK = "slack"            # Slack 报告；宿主核对用户原文授权后发送


class AnalysisType(str, Enum):
    """分析方式。"""
    VOC = "voc"                # 用户声音/槽点分析（复用 voc_processor）
    SUMMARY = "summary"        # 通用摘要提炼
    CUSTOM = "custom"          # 自定义指令分析
    NONE = "none"              # 不分析，仅给原始/清洗数据


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    keyword: str = Field(min_length=1, max_length=200)


BankQuery = SearchQuery


class CollectionScope(BaseModel):
    """主题发现与证据抽取的有界契约；领域规则另行声明。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_evidence: str = Field(min_length=1, max_length=100)
    raw_only: bool = False
    include_images: bool = True
    count_unit: Literal["note"] = "note"
    publication_from: Optional[date] = None
    publication_to: Optional[date] = None
    frozen_at: datetime = Field(default_factory=beijing_now)
    target_count: int = Field(default=5, ge=1, le=100)
    initial_candidates: int = Field(default=20, ge=1, le=20)
    candidate_limit: int = Field(default=100, ge=1, le=100)
    ocr_limit: int = Field(default=20, ge=0, le=20)
    strictness: Literal["strict", "best_effort"] = "best_effort"
    time_budget_seconds: int = Field(default=1800, ge=30, le=14400)

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.publication_from and self.publication_to and self.publication_from > self.publication_to:
            raise ValueError("发布时间起点不能晚于终点")
        if self.target_count > self.candidate_limit or self.frozen_at.tzinfo is None:
            raise ValueError("目标不能超过候选预算，冻结时间必须带时区")
        return self


class EvidenceCollectionScope(CollectionScope):
    queries: list[SearchQuery] = Field(min_length=1, max_length=10)
    fields: list[str] = Field(default_factory=list, max_length=40)
    extraction_instruction: str = Field(default="按用户要求提取来源明确记载的字段", max_length=2000)

    @model_validator(mode="after")
    def valid_queries(self):
        import re
        if len({q.name for q in self.queries}) != len(self.queries) or any("," in q.keyword for q in self.queries):
            raise ValueError("分组不可重复，每组只允许一个搜索词")
        if len(set(self.fields)) != len(self.fields) or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", f) for f in self.fields):
            raise ValueError("字段名称必须唯一且使用小写字母、数字或下划线")
        if not self.raw_only and not self.fields:
            raise ValueError("结构化抽取必须冻结结果字段")
        if set(self.fields) & {"evidence", "missing_reasons", "evidence_status", "review_required",
                               "note_id", "benefit_index", "source_status", "exclusion_reason"}:
            raise ValueError("业务字段不能覆盖证据与来源状态字段")
        return self


class BankBenefitsScope(CollectionScope):
    """每家银行独立执行同一冻结预算，缺省范围不能冒充用户要求。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_evidence: str = Field(min_length=1, max_length=100)
    banks: list[BankQuery] = Field(min_length=1)
    audience: Optional[str] = None
    include_credit_card: bool = False
    raw_only: bool = False
    require_current_rules: bool = True
    publication_from: date = Field(default_factory=lambda: beijing_now().date().replace(month=1, day=1))
    publication_to: date = Field(default_factory=lambda: beijing_now().date())
    frozen_at: datetime = Field(default_factory=beijing_now)
    target_count: int = Field(default=5, ge=1, le=20)
    initial_candidates: Literal[20] = 20
    candidate_limit: Literal[100] = 100
    ocr_limit: Literal[20] = 20

    @model_validator(mode="after")
    def valid_scope(self):
        if self.publication_from > self.publication_to:
            raise ValueError("发布时间起点不能晚于终点")
        if len({bank.name for bank in self.banks}) != len(self.banks):
            raise ValueError("银行列表不能重复")
        if any("," in bank.keyword for bank in self.banks):
            raise ValueError("每家银行须冻结单个检索词，不能用逗号隐式扩成多次搜索")
        return self


class TaskSpec(BaseModel):
    """一次数据采集分析任务的完整规格。"""
    _collection_task_id: Optional[str] = PrivateAttr(default=None)

    intent: str = Field(..., description="对用户意图的一句话归纳")
    platforms: List[str] = Field(
        default_factory=list,
        description="目标平台/站点，如 抖音、小红书、某招投标网；通用网页可留空",
    )
    urls: List[str] = Field(default_factory=list, description="用户直接给出的 URL（如有）")
    site_domains: List[str] = Field(
        default_factory=list,
        description="站点限定的主域名，如 autohome.com.cn；用户指定站点而内置词表未收录时由规划器补全",
    )
    keywords: List[str] = Field(default_factory=list, description="检索关键词，如 小米SU7")
    data_type: DataType = Field(default=DataType.GENERIC, description="数据类型")
    include_comments: bool = Field(default=False, description="保留帖子，并附带其评论及回复")
    comment_limit: int = Field(default=20, ge=1, le=200, description="每帖一级评论获取上限")
    xhs_search_page: int = Field(default=1, ge=1, le=100, description="小红书检索起始页")
    xhs_sort: Optional[Literal["general", "time_descending"]] = None
    xhs_note_type: Optional[Literal["image", "video", "all"]] = None
    bank_benefits: Optional[BankBenefitsScope] = None
    evidence_collection: Optional[EvidenceCollectionScope] = None

    @model_validator(mode="after")
    def single_collection_profile(self):
        if self.bank_benefits and self.evidence_collection:
            raise ValueError("一次任务只能冻结一套领域抽取规则")
        return self

    time_range: Optional[str] = Field(
        default=None, description="时间范围，如 最近7天 / 2026-01 至今（自然语言即可）"
    )
    max_items: int = Field(default=50, ge=1, le=2000, description="最多采集条数")
    # 未设置时省略新字段，保持历史采集进度的冻结摘要兼容。
    time_budget_seconds: int | None = Field(default=None, ge=1, le=14400, strict=True, exclude_if=lambda value: value is None,
        description="用户明确要求的全流程总耗时上限（秒），包含理解、规划、采集、分析及质检；未指定则为空")
    login_strategy: LoginStrategy = Field(
        default=LoginStrategy.NONE, description="登录策略"
    )

    analysis_type: AnalysisType = Field(default=AnalysisType.SUMMARY, description="分析方式")
    analysis_instruction: Optional[str] = Field(
        default=None, description="自定义分析指令（analysis_type=custom 时使用）"
    )

    outputs: List[OutputFormat] = Field(
        default_factory=lambda: [OutputFormat.REPORT_MD],
        description="产出形式，可多选",
    )
    db_target: Optional[str] = Field(
        default=None, description="入库目标（表名/连接别名），outputs 含 db 时使用"
    )
    email_to: Optional[str] = Field(
        default=None, description="邮件收件人（多个用逗号分隔），outputs 含 email 时使用"
    )

    schedule: Optional[str] = Field(
        default=None, description="定时表达式：once@<时间> 或 cron@<5段>（MVP 仅记录，期2 执行）"
    )

    def needs_db_write(self) -> bool:
        return OutputFormat.DB in self.outputs

    def needs_email(self) -> bool:
        return OutputFormat.EMAIL in self.outputs

    def needs_slack(self) -> bool:
        return OutputFormat.SLACK in self.outputs

    def wants_report(self) -> bool:
        return OutputFormat.REPORT_MD in self.outputs

    @classmethod
    def from_draft(cls, draft: dict, fallback_text: str = "") -> "TaskSpec":
        """兼容缺省草稿，但不把非法的明确约束替换成另一种任务。"""
        if draft is None:
            draft = {}
        if not isinstance(draft, dict):
            raise ValueError("任务草稿必须为对象")
        if set(draft) - set(cls.model_fields) - {"reasoning"}:
            raise ValueError("任务草稿包含未支持字段")
        for key in ("intent", "time_range", "analysis_instruction", "db_target", "email_to", "schedule"):
            if draft.get(key) is not None and not isinstance(draft[key], str):
                raise ValueError("任务文本参数必须为文本")
        for scope_key in ("bank_benefits", "evidence_collection"):
            if not isinstance(draft.get(scope_key), dict):
                continue
            draft = {**draft, scope_key: dict(draft[scope_key])}
            # 只纠正模型已知字段的层级，不吞未知字段，也不替用户裁决冲突。
            for key in ("xhs_sort", "xhs_note_type", "include_comments", "comment_limit",
                        "data_type", "analysis_type", "outputs"):
                if key not in draft[scope_key]:
                    continue
                value = draft[scope_key].pop(key)
                if key in draft and draft[key] is not None and draft[key] != value:
                    raise ValueError("银行范围与顶层采集参数冲突" if scope_key == "bank_benefits" else "采集范围与顶层采集参数冲突")
                draft[key] = value
            if scope_key == "bank_benefits":
                draft.setdefault("include_comments", True)

        def _enum(enum_cls, value, default):
            if value is None:
                return default
            return enum_cls(value.strip().lower() if isinstance(value, str) else value)

        def _list(value):
            if value is None:
                return []
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                if not all(isinstance(v, str) and v.strip() for v in value):
                    raise ValueError("列表项必须为非空文本")
                return [v.strip() for v in value]
            raise ValueError("列表参数必须为文本或文本列表")

        outputs_raw = (["report_md"] if draft.get("outputs") is None else _list(draft["outputs"]))
        if not outputs_raw:
            raise ValueError("输出格式不能为空")
        outputs: List[OutputFormat] = []
        for o in outputs_raw:
            fmt = _enum(OutputFormat, o, None)
            if fmt and fmt not in outputs:
                outputs.append(fmt)

        max_items = draft.get("max_items") if draft.get("max_items") is not None else 50
        for key in ("max_items", "comment_limit", "xhs_search_page"):
            value = draft.get(key)
            if isinstance(value, bool) or isinstance(value, float):
                raise ValueError("数量和页码必须为整数")
        if draft.get("include_comments") is not None and not isinstance(draft["include_comments"], bool):
            raise ValueError("是否采集评论必须为布尔值")

        keywords = _list(draft.get("keywords"))
        intent = draft.get("intent") or fallback_text or "采集并分析数据"
        if not isinstance(intent, str):
            raise ValueError("任务意图必须为文本")
        intent = intent.strip()
        if not keywords and fallback_text:
            keywords = [fallback_text.strip()[:50]]

        return cls(
            intent=intent,
            platforms=_list(draft.get("platforms")),
            urls=_list(draft.get("urls")),
            site_domains=_list(draft.get("site_domains")),
            keywords=keywords,
            data_type=_enum(DataType, draft.get("data_type"), DataType.GENERIC),
            include_comments=draft.get("include_comments") is True,
            comment_limit=(draft.get("comment_limit") if draft.get("comment_limit") is not None else 20),
            xhs_sort=draft.get("xhs_sort"),
            xhs_search_page=draft.get("xhs_search_page", 1),
            xhs_note_type=draft.get("xhs_note_type"),
            bank_benefits=({**draft["bank_benefits"], "frozen_at": beijing_now()}
                           if isinstance(draft.get("bank_benefits"), dict) else draft.get("bank_benefits")),
            evidence_collection=({**draft["evidence_collection"], "frozen_at": beijing_now()}
                                 if isinstance(draft.get("evidence_collection"), dict) else draft.get("evidence_collection")),
            time_range=(draft.get("time_range") or None),
            max_items=max_items,
            time_budget_seconds=draft.get("time_budget_seconds"),
            login_strategy=_enum(LoginStrategy, draft.get("login_strategy"), LoginStrategy.NONE),
            analysis_type=_enum(AnalysisType, draft.get("analysis_type"), AnalysisType.SUMMARY),
            analysis_instruction=(draft.get("analysis_instruction") or None),
            outputs=outputs,
            db_target=(draft.get("db_target") or None),
            email_to=(draft.get("email_to") or None),
            schedule=(draft.get("schedule") or None),
        )
