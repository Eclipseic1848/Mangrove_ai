"""数据准备内核：任务契约、原始制品、记录信封、清洗 Recipe、质量报告的统一模型。

设计依据：plan.md 第 6 节《核心数据契约》。
- 所有模型用 Pydantic v2，与现有 src/conductor/task_spec.py 风格一致。
- 旧 TaskSpec(v1) 保留不动；本模块定义 spec_version=2 的新契约，旧图只作兼容。
- 凭证只引用 credential_ref，明文绝不进任务/日志/Manifest。
- JSON Schema 可由 scripts/export_schemas.py 导出，供前后端与测试共用。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ===========================================================================
# 枚举
# ===========================================================================

class TaskMode(str, Enum):
    """任务模式：默认数据准备，旧分析仅显式选择时兼容。"""
    DATA_PREP = "data_prep"
    LEGACY_ANALYSIS = "legacy_analysis"


class SourceType(str, Enum):
    """数据源类型。首版覆盖 web/upload_file/http_api/database/media。"""
    WEB = "web"                      # 互联网网页/搜索/社媒（复用现有 collectors）
    UPLOAD_FILE = "upload_file"      # 本地上传文件
    HTTP_API = "http_api"            # 通用 REST/HTTP 接口
    DATABASE = "database"            # SQLite/MySQL/PostgreSQL 只读取数
    MEDIA = "media"                  # 音视频/图片
    OBJECT_STORAGE = "object_storage"  # S3/MinIO（后续增量，首版不实现）


class OutputFormat(str, Enum):
    """数据产物格式。7C 决策：首版全开 JSONL/Parquet/CSV/TSV/JSON/XLSX/SQLite。"""
    JSONL = "jsonl"      # 默认：流式、嵌套友好
    PARQUET = "parquet"  # 默认：类型保真、大数据首选
    CSV = "csv"          # 可选：最大兼容，附 Schema
    TSV = "tsv"          # 可选
    JSON = "json"        # 小规模/联调；超阈值自动建议 JSONL
    XLSX = "xlsx"        # 人工查看副本，不作为权威格式
    SQLITE = "sqlite"    # 单文件可查询交付，仅写新产物库


class QualityResult(str, Enum):
    """质量门结论。fail 时只交付原始/隔离/失败报告，不标"干净数据"。"""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class RecipeStage(str, Enum):
    """清洗规则阶段（plan 8.2）。引擎按阶段顺序执行。"""
    INPUT_VALIDATION = "input_validation"        # Schema/编码/文件完整性/必需字段
    BASIC_NORMALIZE = "basic_normalize"          # Unicode/换行/空白/HTML 实体/全半角
    FIELD_NORMALIZE = "field_normalize"           # 重命名/嵌套展开/列选择/类型转换
    VALUE_NORMALIZE = "value_normalize"           # 日期时区/数值千分位/布尔/单位/枚举映射
    CONTENT_CLEAN = "content_clean"               # 网页样板/验证码页/控制字符/OCR 噪声/重复段落
    DEDUP = "dedup"                               # 主键/内容哈希/字段组合；模糊去重可选
    QUALITY_CONSTRAINT = "quality_constraint"     # 必填/范围/正则/枚举/唯一/跨字段
    SENSITIVE_INFO = "sensitive_info"             # 检测/标记/按策略脱敏
    ANOMALY_ISOLATION = "anomaly_isolation"       # 无法安全修复的记录进 rejects


class ConnectorCapability(str, Enum):
    """连接器能力声明，供 Planner 选择与降级参考。"""
    READ_ONLY = "read_only"            # 只读连接器（ADR-0004，默认）
    SUPPORTS_CHECKPOINT = "supports_checkpoint"  # 支持断点续跑
    STREAMING = "streaming"            # 支持流式分批读取
    INCREMENTAL = "incremental"        # 支持游标/水位线增量
    SCHEMA_PROBE = "schema_probe"      # 可探测 Schema
    RANDOM_ACCESS = "random_access"    # 可随机访问（如数据库分页）
    MEDIA_EXTRACTION = "media_extraction"  # 可提取音视频/OCR


# ===========================================================================
# 数据源规格（plan 6.1 SourceSpec）
# ===========================================================================

class IncrementalSpec(BaseModel):
    """增量策略：只记录游标语义，具体由连接器解释。"""
    strategy: str = Field(..., description="cursor | watermark | etag | last_modified | none")
    cursor_field: Optional[str] = Field(default=None, description="水位线字段名（strategy=watermark 时）")
    last_value: Optional[str] = Field(default=None, description="上次游标值，用于断点续跑")


class SourceLimits(BaseModel):
    """来源抽取上限，防止单任务失控。"""
    max_bytes: Optional[int] = Field(default=None, ge=1, description="最大字节数")
    max_records: Optional[int] = Field(default=None, ge=1, description="最大记录数")
    max_pages: Optional[int] = Field(default=None, ge=1, description="最大页数")
    max_seconds: Optional[int] = Field(default=None, ge=1, description="最大执行秒数")


class SourceSpec(BaseModel):
    """单个数据源规格。凭证只引用 credential_ref，不写明文。"""
    source_id: str = Field(..., description="数据源标识，任务内唯一")
    source_type: SourceType
    locator: str = Field(
        ...,
        description="URL / 上传文件 ID / 数据库连接 ID / API endpoint 等定位符",
    )
    credential_ref: Optional[str] = Field(
        default=None,
        description="服务端凭证引用（不写明文）；None 表示无需凭证",
    )
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description="来源专属参数：编码/分页/表名/Sheet/OCR 等",
    )
    incremental: Optional[IncrementalSpec] = Field(default=None, description="增量策略")
    limits: Optional[SourceLimits] = Field(default=None, description="抽取上限")

    def to_public_dict(self) -> Dict[str, Any]:
        """对外展示用：剔除 credential_ref 明文（Manifest/日志/trace 复用）。"""
        d = self.model_dump()
        if d.get("credential_ref"):
            d["credential_ref"] = "····"
        return d


# ===========================================================================
# 选取范围与目标 Schema（plan 6.1 selection / target_schema）
# ===========================================================================

class SelectionSpec(BaseModel):
    """从数据源中选取哪些数据：表/字段/路径/关键词/时间/数量范围。"""
    tables: List[str] = Field(default_factory=list, description="数据库表名")
    fields: List[str] = Field(default_factory=list, description="字段/列名")
    paths: List[str] = Field(default_factory=list, description="文件内路径/Sheet/页码范围")
    keywords: List[str] = Field(default_factory=list, description="检索关键词")
    time_range: Optional[str] = Field(default=None, description="时间范围，自然语言或 ISO 区间")
    row_range: Optional[str] = Field(default=None, description="行范围，如 0-1000")
    page_range: Optional[str] = Field(default=None, description="页范围，如 1-10")


class TargetSchemaField(BaseModel):
    """目标 Schema 的单个字段定义。"""
    name: str
    dtype: str = Field(..., description="逻辑类型：string|integer|number|boolean|datetime|date|enum|json|bytes")
    required: bool = Field(default=False)
    enum_values: List[str] = Field(default_factory=list, description="枚举合法值（dtype=enum 时）")
    unique: bool = Field(default=False, description="是否业务唯一键")
    description: Optional[str] = None


class TargetSchema(BaseModel):
    """可选的目标 Schema：清洗后字段应满足的类型与约束。"""
    fields: List[TargetSchemaField] = Field(default_factory=list)
    primary_key: List[str] = Field(default_factory=list, description="主键字段名列表")
    unique_keys: List[List[str]] = Field(default_factory=list, description="业务唯一键组合")


# ===========================================================================
# 清洗 Recipe（plan 8.2）
# ===========================================================================

class RecipeRule(BaseModel):
    """单条清洗规则。每条有版本、参数、影响数量与可逆性说明。"""
    rule_id: str = Field(..., description="规则唯一标识，如 web_dedup_url_prefix")
    stage: RecipeStage
    name: str = Field(..., description="人可读规则名")
    params: Dict[str, Any] = Field(default_factory=dict, description="规则参数")
    version: str = Field(default="1", description="规则版本，便于复现")
    reversible: bool = Field(default=False, description="是否可逆（去重/删除类应为 False）")
    high_impact: bool = Field(
        default=False,
        description="高影响规则（字段删除/模糊去重/脱敏/强制类型）需用户显式确认",
    )
    description: Optional[str] = None


class Recipe(BaseModel):
    """清洗规则集合。可内联或引用已保存 Recipe。"""
    recipe_id: Optional[str] = Field(default=None, description="已保存 Recipe 引用；内联时留空")
    version: str = Field(default="1")
    rules: List[RecipeRule] = Field(default_factory=list)


# ===========================================================================
# 质量策略与报告（plan 6.1 quality_policy / 第 9 节）
# ===========================================================================

class QualityPolicy(BaseModel):
    """质量门阈值策略。确定性规则计算，LLM 不参与通过判定。"""
    max_reject_rate: float = Field(default=0.1, ge=0, le=1, description="最大异常隔离率，超过 fail")
    min_completeness: float = Field(default=0.95, ge=0, le=1, description="必填字段最低非空率")
    min_uniqueness: float = Field(default=1.0, ge=0, le=1, description="主键/业务键最低唯一率")
    require_lineage: bool = Field(default=True, description="是否要求 100% 血缘覆盖")
    warn_reject_rate: float = Field(default=0.03, ge=0, le=1, description="告警级异常率阈值")


class QualityDimensionResult(BaseModel):
    """单个质量维度结果（plan 9：正确/完整/有效/唯一/一致/新鲜/可追溯）。"""
    name: str = Field(..., description="维度名，如 采集完整性/字段有效性")
    value: float = Field(..., description="实际值，通常 0-1")
    threshold: Optional[float] = Field(default=None, description="阈值")
    passed: bool
    details: Dict[str, Any] = Field(default_factory=dict, description="明细：覆盖率/违规数/样本等")


class QualityReport(BaseModel):
    """质量报告。机器读取，强制生成。"""
    task_id: str
    overall: QualityResult
    dimensions: List[QualityDimensionResult] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list, description="问题清单，人可读")
    counts: Dict[str, int] = Field(
        default_factory=dict,
        description="记录账本：raw/parsed/clean/rejects_parse/rejects_clean/merged",
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ===========================================================================
# 原始制品与记录信封（plan 6.2 RawArtifact / 6.3 RecordEnvelope）
# ===========================================================================

class RawArtifact(BaseModel):
    """不可变原始制品登记。每次获取都落一条，支撑复查与复跑。"""
    artifact_id: str
    source_id: str
    task_id: str
    uri: str = Field(..., description="原始定位符（URL/文件路径等，已脱敏）")
    media_type: str = Field(default="application/octet-stream")
    size_bytes: int = Field(default=0, ge=0)
    sha256: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    fetched_at: Optional[datetime] = None
    request_snapshot: Dict[str, Any] = Field(
        default_factory=dict,
        description="已脱敏的请求摘要（不含 Cookie/Token/密码/完整授权头）",
    )
    response_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="状态码/响应头白名单/数据库快照信息等",
    )
    parent_artifact_id: Optional[str] = Field(
        default=None, description="派生关系：解压/下载/转码的父制品"
    )
    storage_path: str = Field(..., description="不可变存储路径（任务目录内相对路径）")


class RecordPosition(BaseModel):
    """记录在原始制品中的位置。字段按来源类型选用。"""
    page: Optional[int] = None
    row: Optional[int] = None
    sheet: Optional[str] = None
    line: Optional[int] = None
    time_start: Optional[str] = None   # 音视频片段开始时间码
    time_end: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class RecordEnvelope(BaseModel):
    """内部统一记录信封：业务数据 + 系统元数据分离（plan 6.3）。

    导出时业务数据与血缘侧车文件分离，避免系统字段污染业务字段。
    """
    record_id: str = Field(..., description="稳定内容标识（内容哈希或主键派生）")
    data: Dict[str, Any] = Field(default_factory=dict, description="业务字段")
    meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="系统元数据：source_id/artifact_id/position/observed_at/parser/content_hash",
    )


# ===========================================================================
# 产物 Manifest（plan 6.4）
# ===========================================================================

class ManifestArtifactEntry(BaseModel):
    """Manifest 中单个制品条目。"""
    artifact_id: str
    kind: str = Field(..., description="raw|parsed|clean|rejects|lineage|schema|quality|recipe|trace")
    path: str
    sha256: str
    size_bytes: int = Field(default=0, ge=0)


class ManifestOutputEntry(BaseModel):
    """Manifest 中单个输出文件条目。"""
    format: OutputFormat
    path: str
    sha256: str
    records: int = Field(default=0, ge=0)


class DatasetManifest(BaseModel):
    """任务产物总清单，唯一入口（plan 6.4）。

    严禁包含 Cookie/Token/密码/完整授权头。
    """
    task_id: str
    spec_version: str = Field(default="2", description="契约版本")
    mode: TaskMode = TaskMode.DATA_PREP
    recipe_version: Optional[str] = None
    artifacts: List[ManifestArtifactEntry] = Field(default_factory=list)
    outputs: List[ManifestOutputEntry] = Field(default_factory=list)
    record_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="记录账本：raw/parsed/clean/rejects_parse/rejects_clean/merged",
    )
    schema_ref: Optional[str] = Field(default=None, description="schema.json 路径")
    quality_ref: Optional[str] = Field(default=None, description="quality_report.json 路径")
    lineage_ref: Optional[str] = Field(default=None, description="lineage/records.jsonl 路径")
    environment: Dict[str, Any] = Field(
        default_factory=dict,
        description="执行环境：python/engine_version/created_at",
    )


# ===========================================================================
# DataPrepTaskSpec v2（plan 6.1）
# ===========================================================================

class RetentionPolicy(BaseModel):
    """数据保留策略（plan 3：默认 30 天可配置）。"""
    raw_days: int = Field(default=30, ge=0, description="原始制品保留天数；4B 决策默认 30")
    intermediate_days: int = Field(default=7, ge=0, description="中间产物保留天数")
    output_days: int = Field(default=90, ge=0, description="输出数据保留天数")


class DataPrepTaskSpec(BaseModel):
    """数据准备任务规格 v2（plan 6.1）。

    与旧 TaskSpec(v1) 并存：v1 仍是网页文本模型，v2 是通用数据准备契约。
    旧图按 mode=legacy_analysis 兼容，新图按 mode=data_prep 默认进入。
    """
    spec_version: str = Field(default="2")
    mode: TaskMode = TaskMode.DATA_PREP
    intent: str = Field(..., description="对用户意图的一句话归纳")

    sources: List[SourceSpec] = Field(default_factory=list, description="数据源列表")
    selection: Optional[SelectionSpec] = Field(default=None, description="选取范围")
    target_schema: Optional[TargetSchema] = Field(default=None, description="目标 Schema")

    cleaning_recipe: Recipe = Field(default_factory=Recipe, description="清洗规则集合")
    quality_policy: QualityPolicy = Field(default_factory=QualityPolicy)

    outputs: List[OutputFormat] = Field(
        default_factory=lambda: [OutputFormat.JSONL, OutputFormat.PARQUET],
        description="输出格式；7C 决策默认 JSONL+Parquet",
    )
    schedule: Optional[str] = Field(default=None, description="once@<时间> 或 cron@<5段>")
    retention_policy: RetentionPolicy = Field(default_factory=RetentionPolicy)

    # ---- 运行期才填充，不进 LLM 草稿 ----
    task_id: Optional[str] = None
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
