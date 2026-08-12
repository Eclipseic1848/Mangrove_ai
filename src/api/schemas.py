"""网关请求/响应的 pydantic 模型。"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


# ---------- 鉴权 ----------
class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    display_name: str
    role: str = "user"


# ---------- 管理员：用户管理 ----------
class AdminUserCreateIn(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    role: str = "user"  # admin | user


class AdminUserUpdateIn(BaseModel):
    role: Optional[str] = None       # admin | user
    disabled: Optional[bool] = None
    pending: Optional[bool] = None   # False=审批通过（激活），True=置为待审批
    password: Optional[str] = None   # 重置密码（明文，后端再哈希）
    display_name: Optional[str] = None  # 修改昵称（分级规则同其它字段：仅能改低于自己的账号）


class RegistrationIn(BaseModel):
    enabled: bool


# ---------- 会话 ----------
class ConversationOut(BaseModel):
    conv_id: str
    title: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: str
    task_id: Optional[str] = None
    meta: Optional[dict] = None


class RenameIn(BaseModel):
    title: str


# ---------- 聊天 ----------
class ChatIn(BaseModel):
    conv_id: Optional[str] = None  # 不传则新建会话
    content: str
    provider: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None  # data_prep | legacy_analysis；None 时按 settings.data_prep_mode_enabled


# ---------- HITL 确认 ----------
class ConfirmIn(BaseModel):
    task_id: str


# ---------- 定时任务 ----------
class ScheduleIn(BaseModel):
    task_id: str  # 对应聊天产生的 schedule 暂存


# ---------- 任务中心：手动创建/编辑自动化任务 ----------
class TriggerIn(BaseModel):
    type: str  # "cron" | "interval" | "once"
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    run_at: Optional[str] = None  # once 的执行时刻（ISO），如 "2026-07-14T15:00"


class ManualTaskIn(BaseModel):
    name: str
    prompt: str  # 交给 Conductor 的 user_input
    provider: Optional[str] = None
    model: Optional[str] = None
    trigger: TriggerIn
    start_date: Optional[str] = None  # 生效区间起（ISO 日期），留空始终生效
    end_date: Optional[str] = None    # 生效区间止（ISO 日期）
    template_id: Optional[str] = None  # 由模板创建时带上，落库 source="template"


class TaskPatchIn(BaseModel):
    status: Optional[str] = None  # "active" | "paused"：仅传它表示纯粹的暂停/恢复
    name: Optional[str] = None
    prompt: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    trigger: Optional[TriggerIn] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


# ---------- 模板沉淀 ----------
class TemplateConfirmIn(BaseModel):
    task_id: str
