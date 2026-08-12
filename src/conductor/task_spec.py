"""
TaskSpec —— 全局结构化任务契约

它是意图理解的产物，也是路由、采集、清洗、分析、产出各环节的统一依据。
用 pydantic 做确定性校验（Harness 验证机制）。
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


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
    EMAIL = "email"            # 邮件发送报告（敏感动作，走 HITL 确认）
    SLACK = "slack"            # 推送报告到 Slack 频道（敏感动作，走 HITL 确认）


class AnalysisType(str, Enum):
    """分析方式。"""
    VOC = "voc"                # 用户声音/槽点分析（复用 voc_processor）
    SUMMARY = "summary"        # 通用摘要提炼
    CUSTOM = "custom"          # 自定义指令分析
    NONE = "none"              # 不分析，仅给原始/清洗数据


class TaskSpec(BaseModel):
    """一次数据采集分析任务的完整规格。"""

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

    time_range: Optional[str] = Field(
        default=None, description="时间范围，如 最近7天 / 2026-01 至今（自然语言即可）"
    )
    max_items: int = Field(default=50, ge=1, le=2000, description="最多采集条数")
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
        """从 LLM 产出的松散草稿安全构造 TaskSpec：逐字段容错，失败则用兜底默认。"""
        draft = draft or {}

        def _enum(enum_cls, value, default):
            try:
                return enum_cls(str(value).strip().lower())
            except Exception:
                return default

        def _list(value):
            if isinstance(value, list):
                return [str(v).strip() for v in value if str(v).strip()]
            if value:
                return [str(value).strip()]
            return []

        outputs_raw = _list(draft.get("outputs")) or ["report_md"]
        outputs: List[OutputFormat] = []
        for o in outputs_raw:
            fmt = _enum(OutputFormat, o, None)
            if fmt and fmt not in outputs:
                outputs.append(fmt)
        if not outputs:
            outputs = [OutputFormat.REPORT_MD]

        max_items = draft.get("max_items", 50)
        try:
            max_items = max(1, min(int(max_items), 2000))
        except Exception:
            max_items = 50

        keywords = _list(draft.get("keywords"))
        intent = (draft.get("intent") or fallback_text or "采集并分析数据").strip()
        if not keywords and fallback_text:
            keywords = [fallback_text.strip()[:50]]

        return cls(
            intent=intent,
            platforms=_list(draft.get("platforms")),
            urls=_list(draft.get("urls")),
            site_domains=_list(draft.get("site_domains")),
            keywords=keywords,
            data_type=_enum(DataType, draft.get("data_type"), DataType.GENERIC),
            time_range=(draft.get("time_range") or None),
            max_items=max_items,
            login_strategy=_enum(LoginStrategy, draft.get("login_strategy"), LoginStrategy.NONE),
            analysis_type=_enum(AnalysisType, draft.get("analysis_type"), AnalysisType.SUMMARY),
            analysis_instruction=(draft.get("analysis_instruction") or None),
            outputs=outputs,
            db_target=(draft.get("db_target") or None),
            email_to=(draft.get("email_to") or None),
            schedule=(draft.get("schedule") or None),
        )
