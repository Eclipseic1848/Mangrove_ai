# -*- coding: utf-8 -*-
"""Agentic Runtime vNext 的稳定领域契约。"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class RuntimeVersion(str, Enum):
    """工作台任务采用的执行 Runtime。"""

    LEGACY = "legacy"
    PI = "pi"


class PermissionProfile(str, Enum):
    """任务级权限档位。"""

    STANDARD = "standard"
    EXTENDED = "extended"
    HOST_DEV = "host_dev"


class RuntimeStatus(str, Enum):
    """Pi Run 的持久状态，不复用旧 Harness 的内部状态。"""

    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    CANDIDATE_READY = "candidate_ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VerificationStatus(str, Enum):
    """独立候选验证的结论。"""

    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class RuntimeTaskConfig(BaseModel):
    """创建任务时冻结的 Runtime、权限和模型连接选择。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    runtime_version: RuntimeVersion = RuntimeVersion.LEGACY
    permission_profile: PermissionProfile = PermissionProfile.STANDARD
    model_connection_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    model_connection_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    model_connection_model: str | None = Field(default=None, min_length=1)
    external_api_confirmed: bool = False

    @model_validator(mode="after")
    def validate_connection_binding(self) -> "RuntimeTaskConfig":
        if self.model_connection_id is None:
            if self.model_connection_version is not None or self.model_connection_model is not None:
                raise ValueError("连接版本必须绑定 model_connection_id")
            return self
        if self.model_connection_version is None:
            raise ValueError("外部连接任务必须冻结连接版本")
        if self.model_connection_model is None:
            raise ValueError("外部连接任务必须冻结连接模型")
        if not self.external_api_confirmed:
            raise ValueError("外部连接任务必须记录本修订的数据外发确认")
        return self


class SourceInput(BaseModel):
    """传给 Runtime 的单个只读来源。"""

    model_config = ConfigDict(extra="forbid")

    upload_id: str = Field(min_length=1)
    original_name: str = Field(min_length=1)
    host_path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = "application/octet-stream"


class PiRuntimeRequest(BaseModel):
    """Pi Runtime 启动一次 revision 所需的完整不可变请求。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    objective_text: str = Field(min_length=1, max_length=20_000)
    requested_output_formats: tuple[str, ...] = Field(min_length=1)
    sources: tuple[SourceInput, ...] = Field(min_length=1)
    permission_profile: PermissionProfile = PermissionProfile.STANDARD
    model_connection_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    model_connection_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    model_connection_model: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    api_key: str | None = Field(default=None, min_length=1)

    @field_validator("requested_output_formats")
    @classmethod
    def normalize_formats(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("requested_output_formats 不得重复")
        return normalized

    @model_validator(mode="after")
    def validate_model_route(self) -> "PiRuntimeRequest":
        """外部模式只携带连接引用；本地模式保留无秘密的直接配置。"""

        local_values = (self.model, self.base_url, self.api_key)
        if self.model_connection_id is not None:
            if self.model_connection_version is None:
                raise ValueError("外部连接模式必须冻结连接版本")
            if self.model_connection_model is None:
                raise ValueError("外部连接模式必须冻结连接模型")
            if any(value is not None for value in local_values):
                raise ValueError(
                    "外部连接模式不得同时携带 model、base_url 或 api_key"
                )
            return self
        if (
            self.model_connection_version is not None
            or self.model_connection_model is not None
        ):
            raise ValueError("连接版本必须绑定 model_connection_id")
        if any(value is None for value in local_values):
            raise ValueError(
                "本地模式必须同时提供 model、base_url 和 api_key"
            )
        return self


class PiRuntimeCheckpoint(BaseModel):
    """服务重启后恢复同一 Pi Run 所需的最小持久信息。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workspace_root: Path
    container_name: str | None = None
    session_file: str | None = None


class RuntimeEvent(BaseModel):
    """可安全展示和持久化的精简 Agent 事件。"""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateArtifact(BaseModel):
    """Pi 生成、但尚未成为正式 Delivery 的候选文件。"""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    format: str = Field(min_length=1)
    host_path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    openable: bool
    qa_checks: tuple[str, ...] = ()

    def public_dict(
        self,
        *,
        task_id: str,
        revision: int,
    ) -> dict[str, Any]:
        """对前端隐藏宿主机路径，只暴露经过 owner 校验的下载入口。"""
        payload = self.model_dump(mode="json", exclude={"host_path"})
        payload["download_url"] = (
            f"/api/semantic-workspace/tasks/{task_id}/candidates/"
            f"{self.artifact_id}?revision={revision}"
        )
        return payload


class VerificationCheck(BaseModel):
    """一项可审计、可向普通用户解释的验证检查。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    passed: bool
    summary: str = Field(min_length=1, max_length=500)


class SemanticDecision(BaseModel):
    """独立语义模型只负责判断，不取得发布权限。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    contains_unrequested_content: bool
    reason: str = Field(min_length=1, max_length=1000)
    # list 而非 tuple：instructor v2 JSON 模式 strict 解析下，LLM 输出
    # JSON 数组无法转 tuple，会造成「候选语义验证未形成结论」的既有缺陷
    # （#15 纵切面真实暴露）。
    missing_requirements: list[str] = []

    @field_validator("missing_requirements", mode="before")
    @classmethod
    def normalize_single_missing_requirement(cls, value: Any) -> Any:
        # 部分 OpenAI-compatible Provider 会把单条数组误写成字符串；
        # 规范化为单元素数组可保留失败语义，不能把它吞掉或误判为通过。
        if isinstance(value, str):
            normalized = value.strip()
            return [normalized] if normalized else []
        return value


class VerificationReport(BaseModel):
    """候选验证报告；通过也不会自动变成正式交付。"""

    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus
    summary: str = Field(min_length=1, max_length=1000)
    checks: tuple[VerificationCheck, ...]
    evidence_count: int = Field(ge=0)
    formal_delivery_eligible: bool = False


class PiRuntimeResult(BaseModel):
    """一次 Pi Run 的终态结果。"""

    model_config = ConfigDict(extra="forbid")

    status: RuntimeStatus
    run_id: str = Field(min_length=1)
    workspace_root: Path
    container_name: str | None = None
    session_file: str | None = None
    summary: str = ""
    candidates: tuple[CandidateArtifact, ...] = ()
    verification: VerificationReport | None = None
    failure: dict[str, Any] | None = None
    clarification: dict[str, str] | None = None
