"""#125 隔离身份契约：只记录交接事实，不调用模型、工具或生产授权服务。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Identity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ActivePhase = Literal["submitted", "clarifying", "preparing", "frozen"]
Purpose = Literal["prerun", "execution"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ModelSelection(Contract):
    connection_id: Identity
    version: Identity
    model: Identity


class RunHandoff(Contract):
    authorized_snapshot_ids: tuple[Identity, ...] = Field(min_length=1)
    task_id: Identity
    revision: int = Field(ge=1)
    run_id: Identity

    @model_validator(mode="after")
    def unique_sources(self) -> "RunHandoff":
        if len(set(self.authorized_snapshot_ids)) != len(self.authorized_snapshot_ids):
            raise ValueError("封存来源不得重复")
        return self


class UsageReceipt(Contract):
    total_tokens: int | None = Field(default=None, ge=0)


class Call(Contract):
    call_id: Identity
    model: ModelSelection
    purpose: Purpose
    receipt: UsageReceipt | None = None
    queried_outcome: Literal["completed", "failed"] | None = None


class TaskSession(Contract):
    schema_version: Literal[1] = 1
    session_id: Identity
    owner_id: Identity
    query: Identity
    model: ModelSelection
    phase: Literal["submitted", "clarifying", "preparing", "frozen", "paused", "unknown", "cancelled"] = "submitted"
    resume_phase: ActivePhase | None = None
    unknown_call_id: Identity | None = None
    handoff: RunHandoff | None = None
    calls: tuple[Call, ...] = ()

    @model_validator(mode="after")
    def consistent_state(self) -> "TaskSession":
        if (self.phase in {"paused", "unknown"}) != (self.resume_phase is not None):
            raise ValueError("等待状态缺少原阶段或活动状态带有恢复阶段")
        if self.phase != "cancelled" and (
                (self.resume_phase or self.phase) == "frozen") != (self.handoff is not None):
            raise ValueError("执行阶段与冻结交接不一致")
        if self.phase == "unknown" and self.unknown_call_id is None:
            raise ValueError("未知结果缺少调用身份")
        if self.unknown_call_id is not None and (
                self.phase not in {"unknown", "cancelled"}
                or self.unknown_call_id not in {
                    item.call_id for item in self.calls if item.queried_outcome is None}):
            raise ValueError("未知结果不属于已开始调用")
        if len({item.call_id for item in self.calls}) != len(self.calls):
            raise ValueError("调用身份重复")
        if any(item.model != self.model or (item.purpose == "execution" and self.handoff is None)
               for item in self.calls):
            raise ValueError("调用模型或执行交接不一致")
        return self

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, value: str, *, expected_owner: str, expected_model: ModelSelection,
                  expected_session_id: str) -> "TaskSession":
        session = cls.model_validate_json(value)
        # 嵌套回执也不能用构造默认值补齐，避免恢复时丢失去重和查询事实。
        pending: list[BaseModel] = [session]
        while pending:
            item = pending.pop()
            if item.model_fields_set != set(type(item).model_fields):
                raise ValueError("快照缺少字段，不能用默认值补回调用或状态事实")
            for name in type(item).model_fields:
                value = getattr(item, name)
                if isinstance(value, BaseModel):
                    pending.append(value)
                elif isinstance(value, tuple):
                    pending.extend(child for child in value if isinstance(child, BaseModel))
        if (session.owner_id != expected_owner or session.model != expected_model
                or session.session_id != expected_session_id):
            raise ValueError("快照不属于预期 Owner、模型或会话")
        return session

    def _owner(self, owner_id: str) -> None:
        if owner_id != self.owner_id:
            raise ValueError("会话不属于当前 Owner")

    def clarify(self, owner_id: str) -> "TaskSession":
        self._owner(owner_id)
        if self.phase not in {"submitted", "clarifying"}:
            raise ValueError("当前阶段不能澄清")
        return self.model_copy(update={"phase": "clarifying"})

    def prepare(self, owner_id: str) -> "TaskSession":
        self._owner(owner_id)
        if self.phase not in {"submitted", "clarifying"}:
            raise ValueError("当前阶段不能准备来源")
        return self.model_copy(update={"phase": "preparing"})

    def freeze(self, *, owner_id: str, account_active: bool,
               authorized_snapshot_ids: tuple[str, ...], task_id: str,
               revision: int, run_id: str) -> "TaskSession":
        self._owner(owner_id)
        if self.phase != "preparing" or account_active is not True:
            raise ValueError("只有活跃 Owner 的来源准备阶段可以冻结")
        # 这些是生产授权和来源封存完成后传入的事实，原型不授予来源权限。
        handoff = RunHandoff(authorized_snapshot_ids=authorized_snapshot_ids,
                             task_id=task_id, revision=revision, run_id=run_id)
        return self.model_copy(update={"phase": "frozen", "handoff": handoff})

    def pause(self, owner_id: str) -> "TaskSession":
        self._owner(owner_id)
        if self.phase in {"paused", "unknown", "cancelled"}:
            raise ValueError("当前阶段不能暂停")
        return self.model_copy(update={"phase": "paused", "resume_phase": self.phase})

    def resume(self, owner_id: str) -> "TaskSession":
        self._owner(owner_id)
        if self.phase != "paused":
            raise ValueError("当前阶段不能恢复")
        return self.model_copy(update={"phase": self.resume_phase, "resume_phase": None})

    def cancel(self, owner_id: str) -> "TaskSession":
        self._owner(owner_id)
        return self.model_copy(update={"phase": "cancelled", "resume_phase": None})

    def begin_call(self, owner_id: str, call_id: str, model: ModelSelection,
                   purpose: Purpose) -> "TaskSession":
        self._owner(owner_id)
        if self.phase in {"paused", "unknown", "cancelled"} or model != self.model:
            raise ValueError("当前阶段或模型不允许开始调用")
        if (purpose == "execution") != (self.phase == "frozen"):
            raise ValueError("调用用途不属于当前阶段")
        call = Call(call_id=call_id, model=model, purpose=purpose)
        if any(item.call_id == call.call_id for item in self.calls):
            raise ValueError("调用已经开始，禁止重放")
        return self.model_copy(update={"calls": (*self.calls, call)})

    def mark_unknown(self, owner_id: str, call_id: str) -> "TaskSession":
        self._owner(owner_id)
        if self.phase in {"paused", "unknown", "cancelled"} or not any(
                item.call_id == call_id and item.queried_outcome is None for item in self.calls):
            raise ValueError("未知结果必须属于当前已开始调用")
        return self.model_copy(update={"phase": "unknown", "resume_phase": self.phase,
                                      "unknown_call_id": call_id})

    def resolve_unknown(self, owner_id: str, call_id: str,
                        queried_outcome: Literal["completed", "failed"]) -> "TaskSession":
        self._owner(owner_id)
        if (self.phase not in {"unknown", "cancelled"} or self.unknown_call_id != call_id
                or queried_outcome not in {"completed", "failed"}):
            raise ValueError("只能用同一调用的明确查询结果解除未知")
        # 这里只接收查询事实，绝不执行或重放原调用；取消仍保持终态。
        return self.model_copy(update={
            "phase": "cancelled" if self.phase == "cancelled" else self.resume_phase,
            "resume_phase": None, "unknown_call_id": None,
            "calls": tuple(item.model_copy(update={"queried_outcome": queried_outcome})
                           if item.call_id == call_id else item for item in self.calls),
        })

    def record_usage(self, owner_id: str, call_id: str, model: ModelSelection,
                     purpose: Purpose, total_tokens: int | None) -> "TaskSession":
        self._owner(owner_id)
        call = next((item for item in self.calls if item.call_id == call_id), None)
        if call is None or model != call.model or purpose != call.purpose:
            raise ValueError("用量不属于已开始的同模型调用")
        receipt = UsageReceipt(total_tokens=total_tokens)
        if call.receipt is not None and call.receipt != receipt:
            raise ValueError("同一调用的用量回执冲突")
        # 取消只禁止新执行，已开始调用的迟到用量仍属于原会话。
        return self.model_copy(update={"calls": tuple(
            item.model_copy(update={"receipt": receipt}) if item.call_id == call_id else item
            for item in self.calls
        )})

    @property
    def usage_summary(self) -> dict[str, int]:
        return {
            "known_tokens": sum(item.receipt.total_tokens for item in self.calls
                                if item.receipt is not None and item.receipt.total_tokens is not None),
            "unknown_calls": sum(item.receipt is not None and item.receipt.total_tokens is None
                                 for item in self.calls),
            "pending_calls": sum(item.receipt is None for item in self.calls),
        }
