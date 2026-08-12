# -*- coding: utf-8 -*-
"""不同能力类型共享的无 Shell 运行契约。"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SHELL_PROGRAMS = {
    "bash",
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "sh",
    "zsh",
}
_SECRET_MARKERS = ("api_key", "apikey", "password", "secret", "token")
_ALLOWED_RUNTIME_PERMISSIONS = {
    "mcp:stdio",
    "network:none",
    "process:child",
    "skill:read",
    "work:write",
    "workspace:read",
}
_ALLOWED_ENVIRONMENT_KEYS = {"LANG", "LC_ALL", "NODE_PATH", "PYTHONPATH"}


def _validate_relative_path(value: str, *, label: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label}路径越界")
    if not value.strip() or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{label}格式无效")
    return value


class RuntimeCommand(BaseModel):
    """只能直接 exec argv；禁止把能力声明退化成 Shell 脚本。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    program: str = Field(min_length=1, max_length=300)
    arguments: tuple[str, ...] = ()
    working_directory: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: int = Field(default=120, ge=1, le=3600)

    @field_validator("program")
    @classmethod
    def validate_program(cls, value: str) -> str:
        value = _validate_relative_path(value, label="程序")
        if PurePosixPath(value).name.casefold() in _SHELL_PROGRAMS:
            raise ValueError("Shell 解释器不能成为能力入口")
        return value

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        return _validate_relative_path(value, label="工作目录")

    @model_validator(mode="after")
    def validate_environment(self) -> "RuntimeCommand":
        for key, value in self.environment:
            normalized = key.casefold()
            if any(marker in normalized for marker in _SECRET_MARKERS):
                # Secret 只能由任务级 Grant 注入，不能冻结进能力包或事件。
                raise ValueError("Secret 环境变量不得写入运行清单")
            if key not in _ALLOWED_ENVIRONMENT_KEYS:
                raise ValueError(f"运行清单不允许环境变量：{key}")
            if not key or "=" in key or "\x00" in key or "\x00" in value:
                raise ValueError("环境变量格式无效")
        for argument in self.arguments:
            if "\x00" in argument or "\n" in argument or "\r" in argument:
                raise ValueError("argv 参数格式无效")
        return self


class PreparationCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    argv: tuple[str, ...] = Field(min_length=1)


class PreparationPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    root: Path
    runtime_identity: str
    commands: tuple[PreparationCommand, ...]


class PreparedCli(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    root: Path
    entrypoint: Path
    digest: str
    asset_digest: str | None = None
    platform: str
    architecture: str
    source_ref: str


class PreparedSkill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    root: Path
    name: str
    skill_file: Path


class CapabilityRuntimeManifest(BaseModel):
    """冻结包内唯一允许进入业务 Runtime 的执行说明。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,119}$")
    version: str = Field(min_length=1, max_length=80)
    kind: Literal["python", "node", "cli", "skill", "mcp_local", "mcp_remote"]
    purpose: str = Field(min_length=1, max_length=300)
    entrypoint: RuntimeCommand | None = None
    healthcheck: RuntimeCommand | None = None
    skill_path: str | None = None
    connection_ref: str | None = Field(default=None, max_length=200)
    secret_ref: str | None = Field(
        default=None,
        pattern=r"^secretref:[A-Za-z0-9._:-]{1,180}$",
    )
    permissions: tuple[str, ...] = ()

    @field_validator("skill_path")
    @classmethod
    def validate_skill_path(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_relative_path(value, label="Skill")
        return value

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        unsupported = sorted(set(values) - _ALLOWED_RUNTIME_PERMISSIONS)
        if unsupported:
            raise ValueError("能力请求了未授权运行权限：" + "、".join(unsupported))
        return values

    @model_validator(mode="after")
    def validate_kind_contract(self) -> "CapabilityRuntimeManifest":
        if self.kind in {"python", "node", "cli", "mcp_local"}:
            if self.entrypoint is None:
                raise ValueError("可执行能力必须声明 entrypoint")
        if self.kind == "skill":
            if self.skill_path is None:
                raise ValueError("Skill 能力必须声明 skill_path")
            if self.entrypoint is not None:
                raise ValueError("无脚本 Skill 不得声明 entrypoint")
        if self.kind == "mcp_remote":
            if not self.connection_ref or not self.secret_ref:
                raise ValueError("远程 MCP 必须只声明 ConnectionRef 与 SecretRef")
            if self.entrypoint is not None:
                raise ValueError("远程 MCP 不得声明本地入口")
        elif self.connection_ref is not None or self.secret_ref is not None:
            raise ValueError("本地能力不得携带远程连接引用")
        return self
