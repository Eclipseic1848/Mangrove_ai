# -*- coding: utf-8 -*-
"""Phase 4B 批次 5：可持久化、可暂停且有硬预算的 Harness Graph。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, TypedDict
import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_none,
)

from .document_models import DocumentPhysicalPlan
from .delivery import create_delivery
from .harness_adapters import get_harness_adapter
from .harness_models import (
    HarnessLoopPolicy,
    HarnessNode,
    HarnessQuestion,
    HarnessQuestionOption,
    HarnessResume,
    HarnessStatus,
    RepairAction,
    failure_fingerprint,
)
from .harness_policy import classify_exception, decide_repair, safe_error_message
from .inspection_models import SourceInspectionReport
from .models import (
    BoundPlan,
    ExecutionBoundary,
    FailureKind,
    SemanticTaskPlan,
    ToolResult,
    ToolStatus,
    VerificationReport,
    VerificationStatus,
)
from .physical_models import PhysicalPlan, RuntimeProfileName


class HarnessState(TypedDict, total=False):
    run_id: str
    user_id: str
    logical_plan: dict[str, Any]
    bound_plan: dict[str, Any]
    reports: list[dict[str, Any]]
    physical_plan: dict[str, Any]
    tool_result: dict[str, Any]
    verification: dict[str, Any]
    artifact_paths: dict[str, str]
    delivery: dict[str, Any]
    failure_kind: str
    failure_message: str
    failure_fingerprint: str
    question: dict[str, Any]
    resume_target: str
    external_confirmed: bool
    force_recompile: bool
    route: str


@dataclass(frozen=True)
class HarnessRuntime:
    store: Any
    upload_store: Any
    output_root: Path


def _hash_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _question(
    *,
    run_id: str,
    prompt: str,
    reason: str,
    affected_scope: str,
    options: tuple[HarnessQuestionOption, ...],
    external: bool = False,
    allow_free_text: bool = True,
) -> HarnessQuestion:
    question_id = f"question_{uuid.uuid4().hex[:16]}"
    return HarnessQuestion(
        question_id=question_id,
        run_id=run_id,
        checkpoint_id=f"checkpoint_{run_id}",
        prompt=prompt,
        reason=reason,
        affected_scope=affected_scope,
        options=options,
        allow_free_text=allow_free_text,
        answer_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["question_id", "resume_token", "answer"],
        },
        resume_token=uuid.uuid4().hex,
        external_service="用户选择的外部 OpenAPI" if external else None,
        outbound_data=("计划文本", "被选中的证据片段") if external else (),
        purpose="完成用户明确要求的语义处理" if external else None,
        risk="数据将离开本机或局域网" if external else None,
    )


def _build_graph(runtime: HarnessRuntime, checkpointer: Any):
    def load_run(state: HarnessState) -> dict[str, Any]:
        row = runtime.store.get_semantic_harness_run(
            state["user_id"], state["run_id"]
        )
        if row is None:
            raise PermissionError("Harness run 不存在或无权访问")
        return row

    def update_run(
        state: HarnessState,
        *,
        status: HarnessStatus,
        node: HarnessNode,
        question: HarnessQuestion | None = None,
        verification: VerificationReport | None = None,
        eligible: bool = False,
        repair_rounds: int | None = None,
        transient_retries: int | None = None,
        same_failure_count: int | None = None,
        last_failure_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        run = load_run(state)
        return runtime.store.update_semantic_harness_run(
            state["user_id"],
            state["run_id"],
            status=status.value,
            current_node=node.value,
            repair_rounds=(
                run["repair_rounds"]
                if repair_rounds is None
                else repair_rounds
            ),
            semantic_replans=run["semantic_replans"],
            transient_retries=(
                run["transient_retries"]
                if transient_retries is None
                else transient_retries
            ),
            same_failure_count=(
                run["same_failure_count"]
                if same_failure_count is None
                else same_failure_count
            ),
            last_failure_fingerprint=last_failure_fingerprint,
            question=question,
            final_verification=verification,
            eligible_for_delivery=eligible,
        )

    def event(
        state: HarnessState,
        *,
        key: str,
        node: HarnessNode,
        event_type: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        runtime.store.append_semantic_harness_event(
            state["user_id"],
            state["run_id"],
            event_key=f"{state['run_id']}:{key}",
            node=node.value,
            event_type=event_type,
            summary=summary,
            details=details,
        )

    def fail_state(
        state: HarnessState,
        *,
        failure_kind: FailureKind,
        message: str,
    ) -> dict[str, Any]:
        fingerprint = failure_fingerprint(failure_kind, message)
        run = load_run(state)
        same_count = (
            run["same_failure_count"] + 1
            if run["last_failure_fingerprint"] == fingerprint
            else 1
        )
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.REPAIR,
            same_failure_count=same_count,
            last_failure_fingerprint=fingerprint,
        )
        return {
            "failure_kind": failure_kind.value,
            "failure_message": message,
            "failure_fingerprint": fingerprint,
            "route": "repair",
        }

    async def interpret_node(state: HarnessState) -> dict[str, Any]:
        run = load_run(state)
        plan_row = runtime.store.get_semantic_plan_revision(
            state["user_id"],
            run["logical_plan_id"],
            run["logical_plan_revision"],
        )
        binding_row = runtime.store.get_semantic_binding_revision(
            state["user_id"],
            run["logical_plan_id"],
            run["binding_revision"],
        )
        if (
            plan_row is None
            or plan_row["plan"] is None
            or plan_row["plan_hash"] != run["logical_plan_hash"]
            or binding_row is None
            or binding_row["bound_plan"] is None
            or binding_row["bound_plan_hash"] != run["binding_hash"]
        ):
            return fail_state(
                state,
                failure_kind=FailureKind.POLICY_DENIED,
                message="冻结的计划或来源绑定已变化",
            )
        plan = SemanticTaskPlan.model_validate(plan_row["plan"])
        if not plan.is_executable:
            return fail_state(
                state,
                failure_kind=FailureKind.NEEDS_USER,
                message="逻辑计划仍有未解决歧义",
            )
        if (
            plan.risk_policy.execution_boundary
            == ExecutionBoundary.EXTERNAL_API
            and not state.get("external_confirmed", False)
        ):
            question = _question(
                run_id=state["run_id"],
                prompt="该任务标记为使用外部 OpenAPI，是否确认本次数据外发？",
                reason="本次确认只绑定当前 run，不能复用模糊历史同意",
                affected_scope="计划文本和执行所需的证据片段",
                options=(
                    HarnessQuestionOption(
                        value="confirm_external",
                        label="确认本次外发",
                        description="仅授权当前 run 按已声明目的调用外部服务。",
                    ),
                    HarnessQuestionOption(
                        value="stop",
                        label="停止任务",
                        description="保持数据在本地，不继续当前 run。",
                    ),
                ),
                external=True,
                allow_free_text=False,
            )
            return {
                "logical_plan": plan.model_dump(mode="json"),
                "bound_plan": binding_row["bound_plan"],
                "reports": binding_row["reports"],
                "question": question.model_dump(mode="json"),
                "resume_target": "inspect",
                "route": "needs_user",
            }
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.INTERPRET,
            last_failure_fingerprint=run["last_failure_fingerprint"],
        )
        event(
            state,
            key="interpret:ok",
            node=HarnessNode.INTERPRET,
            event_type="node_completed",
            summary="已校验服务端冻结的语义计划和策略边界",
        )
        return {
            "logical_plan": plan.model_dump(mode="json"),
            "bound_plan": binding_row["bound_plan"],
            "reports": binding_row["reports"],
            "route": "inspect",
        }

    async def inspect_node(state: HarnessState) -> dict[str, Any]:
        try:
            bound = BoundPlan.model_validate(state["bound_plan"])
            for artifact_id, expected_hash in bound.input_artifact_hashes.items():
                item = runtime.upload_store.resolve(
                    state["user_id"], artifact_id
                )
                if item.sha256 != expected_hash:
                    raise PermissionError("来源制品哈希已变化")
            reports = [
                SourceInspectionReport.model_validate(item)
                for item in state["reports"]
            ]
            actual_ids = {item.artifact_id for item in reports}
            if actual_ids != set(bound.input_artifact_hashes):
                raise ValueError("检查报告没有覆盖全部冻结来源")
        except Exception as exc:
            return fail_state(
                state,
                failure_kind=classify_exception(exc),
                message=safe_error_message(exc),
            )
        run = load_run(state)
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.INSPECT,
            last_failure_fingerprint=run["last_failure_fingerprint"],
        )
        event(
            state,
            key="inspect:ok",
            node=HarnessNode.INSPECT,
            event_type="node_completed",
            summary="来源哈希、归属和检查报告覆盖已通过",
            details={"artifact_count": len(reports)},
        )
        return {"route": "bind"}

    async def bind_node(state: HarnessState) -> dict[str, Any]:
        bound = BoundPlan.model_validate(state["bound_plan"])
        if not bound.is_executable:
            return fail_state(
                state,
                failure_kind=FailureKind.NEEDS_USER,
                message="来源绑定仍存在歧义或缺失",
            )
        run = load_run(state)
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.BIND,
            last_failure_fingerprint=run["last_failure_fingerprint"],
        )
        event(
            state,
            key="bind:ok",
            node=HarnessNode.BIND,
            event_type="node_completed",
            summary="不可变来源绑定已通过可执行性校验",
        )
        return {"route": "plan"}

    async def plan_node(state: HarnessState) -> dict[str, Any]:
        run = load_run(state)
        if "physical_plan" in state and not state.get("force_recompile"):
            return {"route": "execute"}
        try:
            plan = SemanticTaskPlan.model_validate(state["logical_plan"])
            bound = BoundPlan.model_validate(state["bound_plan"])
            reports = tuple(
                SourceInspectionReport.model_validate(item)
                for item in state["reports"]
            )
            adapter = get_harness_adapter(run["capability_id"])
            physical = await asyncio.to_thread(
                adapter.compile_plan,
                plan,
                bound,
                reports,
                profile=RuntimeProfileName(run["runtime_profile"]),
            )
            if getattr(physical.status, "value", physical.status) != "ready":
                raise ValueError("物理计划仍需用户确认")
        except Exception as exc:
            return fail_state(
                state,
                failure_kind=classify_exception(exc),
                message=safe_error_message(exc),
            )
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.PLAN,
            last_failure_fingerprint=run["last_failure_fingerprint"],
        )
        event(
            state,
            key=f"plan:{run['repair_rounds']}",
            node=HarnessNode.PLAN,
            event_type="node_completed",
            summary="已由冻结逻辑计划编译服务端物理计划",
            details={
                "capability_id": run["capability_id"],
                "repair_round": run["repair_rounds"],
            },
        )
        return {
            "physical_plan": physical.model_dump(mode="json"),
            "force_recompile": False,
            "route": "execute",
        }

    async def execute_node(state: HarnessState) -> dict[str, Any]:
        run = load_run(state)
        attempt_number = run["repair_rounds"] + 1
        physical_payload = state["physical_plan"]
        input_hash = _hash_payload(physical_payload)
        idempotency_key = (
            f"{state['run_id']}:execute:{attempt_number}:{input_hash}"
        )
        existing = runtime.store.get_semantic_harness_attempt_by_key(
            state["user_id"], idempotency_key
        )
        if (
            existing is not None
            and existing["tool_result"] is not None
            and existing["verification"] is not None
        ):
            return {
                "tool_result": existing["tool_result"],
                "verification": existing["verification"],
                "artifact_paths": existing["artifact_paths"],
                "route": "verify",
            }
        try:
            plan = SemanticTaskPlan.model_validate(state["logical_plan"])
            bound = BoundPlan.model_validate(state["bound_plan"])
            reports = tuple(
                SourceInspectionReport.model_validate(item)
                for item in state["reports"]
            )
            paths = {
                artifact_id: Path(
                    runtime.upload_store.resolve(
                        state["user_id"], artifact_id
                    ).storage_path
                )
                for artifact_id in bound.input_artifact_hashes
            }
            adapter = get_harness_adapter(run["capability_id"])
            if run["capability_id"] == "table.duckdb":
                physical: PhysicalPlan | DocumentPhysicalPlan = (
                    PhysicalPlan.model_validate(physical_payload)
                )
            else:
                physical = DocumentPhysicalPlan.model_validate(
                    physical_payload
                )
            safe_user = "".join(
                char
                for char in state["user_id"]
                if char.isalnum() or char in "-_"
            )
            output_dir = (
                runtime.output_root
                / safe_user
                / run["logical_plan_id"]
                / state["run_id"]
                / f"attempt-{attempt_number}"
            )
            outcome = None
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(1),
                wait=wait_none(),
                retry=retry_if_exception_type(
                    (TimeoutError, ConnectionError, MemoryError)
                ),
                reraise=True,
            ):
                with attempt:
                    outcome = await adapter.execute(
                        plan,
                        bound,
                        reports,
                        profile=RuntimeProfileName(run["runtime_profile"]),
                        artifact_paths=paths,
                        output_dir=output_dir,
                        physical_plan=physical,
                    )
            assert outcome is not None
        except Exception as exc:
            kind = classify_exception(exc)
            message = safe_error_message(exc)
            fingerprint = failure_fingerprint(kind, message)
            runtime.store.save_semantic_harness_attempt(
                state["user_id"],
                state["run_id"],
                attempt_id=f"attempt_{uuid.uuid4().hex[:16]}",
                node=HarnessNode.EXECUTE.value,
                attempt_number=attempt_number,
                idempotency_key=idempotency_key,
                input_hash=input_hash,
                status="failed",
                failure_kind=kind.value,
            )
            failed = fail_state(
                state,
                failure_kind=kind,
                message=message,
            )
            failed["failure_fingerprint"] = fingerprint
            return failed
        runtime.store.save_semantic_harness_attempt(
            state["user_id"],
            state["run_id"],
            attempt_id=f"attempt_{uuid.uuid4().hex[:16]}",
            node=HarnessNode.EXECUTE.value,
            attempt_number=attempt_number,
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            status=outcome.tool_result.status.value,
            failure_kind=(
                outcome.tool_result.failure_kind.value
                if outcome.tool_result.failure_kind
                else None
            ),
            tool_result=outcome.tool_result,
            verification=outcome.verification,
            artifact_paths={
                key: str(path.resolve())
                for key, path in outcome.artifact_paths.items()
            },
        )
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.EXECUTE,
            last_failure_fingerprint=run["last_failure_fingerprint"],
        )
        event(
            state,
            key=f"execute:{attempt_number}",
            node=HarnessNode.EXECUTE,
            event_type="node_completed",
            summary="能力工具已执行并保存幂等尝试记录",
            details={
                "attempt": attempt_number,
                "capability_id": run["capability_id"],
            },
        )
        return {
            "physical_plan": outcome.physical_plan.model_dump(mode="json"),
            "tool_result": outcome.tool_result.model_dump(mode="json"),
            "verification": outcome.verification.model_dump(mode="json"),
            "artifact_paths": {
                key: str(path.resolve())
                for key, path in outcome.artifact_paths.items()
            },
            "route": "verify",
        }

    async def verify_node(state: HarnessState) -> dict[str, Any]:
        tool_result = ToolResult.model_validate(state["tool_result"])
        verification = VerificationReport.model_validate(
            state["verification"]
        )
        run = load_run(state)
        if (
            tool_result.status == ToolStatus.SUCCEEDED
            and verification.status == VerificationStatus.PASS
            and verification.authoritative_output_allowed
        ):
            update_run(
                state,
                status=HarnessStatus.RUNNING,
                node=HarnessNode.VERIFY,
                same_failure_count=0,
                last_failure_fingerprint=None,
            )
            event(
                state,
                key=f"verify:{run['repair_rounds']}:pass",
                node=HarnessNode.VERIFY,
                event_type="verification_passed",
                summary="全部确定性后置条件已通过",
            )
            return {"route": "deliver"}
        if tool_result.failure_kind is not None:
            kind = tool_result.failure_kind
            message = tool_result.error_message or "能力工具未成功"
        else:
            failed_checks = [
                check for check in verification.checks if not check.passed
            ]
            kind = (
                FailureKind.INVALID_PLAN
                if any(check.repairable for check in failed_checks)
                else FailureKind.INSUFFICIENT_DATA
            )
            message = "；".join(check.code for check in failed_checks)[:300]
        fingerprint = (
            verification.failure_fingerprint
            or failure_fingerprint(kind, message)
        )
        same_count = (
            run["same_failure_count"] + 1
            if run["last_failure_fingerprint"] == fingerprint
            else 1
        )
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.REPAIR,
            same_failure_count=same_count,
            last_failure_fingerprint=fingerprint,
        )
        return {
            "failure_kind": kind.value,
            "failure_message": message,
            "failure_fingerprint": fingerprint,
            "route": "repair",
        }

    async def repair_node(state: HarnessState) -> dict[str, Any]:
        run = load_run(state)
        policy = HarnessLoopPolicy.model_validate(run["policy"])
        decision = decide_repair(
            run_id=state["run_id"],
            failure_kind=FailureKind(state["failure_kind"]),
            failure_fingerprint=state["failure_fingerprint"],
            reason=state["failure_message"],
            policy=policy,
            repair_rounds=run["repair_rounds"],
            transient_retries=run["transient_retries"],
            same_failure_count=run["same_failure_count"],
        )
        next_round = run["repair_rounds"] + 1
        runtime.store.save_semantic_harness_attempt(
            state["user_id"],
            state["run_id"],
            attempt_id=f"attempt_{uuid.uuid4().hex[:16]}",
            node=HarnessNode.REPAIR.value,
            attempt_number=next_round,
            idempotency_key=(
                f"{state['run_id']}:repair:{next_round}:"
                f"{state['failure_fingerprint']}"
            ),
            input_hash=state["failure_fingerprint"],
            status=(
                "approved"
                if decision.approved
                else "needs_user"
                if decision.requires_user
                else "stopped"
            ),
            failure_kind=state["failure_kind"],
            repair_decision=decision,
        )
        event(
            state,
            key=f"repair:{next_round}:{state['failure_fingerprint']}",
            node=HarnessNode.REPAIR,
            event_type="repair_decided",
            summary=decision.policy_reason,
            details={
                "action": decision.proposal.action.value,
                "changes_user_semantics": (
                    decision.proposal.changes_user_semantics
                ),
            },
        )
        if decision.approved:
            transient_retries = run["transient_retries"]
            if decision.proposal.action == RepairAction.RETRY_SAME_TOOL:
                transient_retries += 1
                await asyncio.sleep(min(0.05 * (2**transient_retries), 0.2))
                route = "execute"
                payload: dict[str, Any] = {"route": route}
            else:
                route = "plan"
                payload = {"route": route, "force_recompile": True}
            update_run(
                state,
                status=HarnessStatus.RUNNING,
                node=HarnessNode.REPAIR,
                repair_rounds=next_round,
                transient_retries=transient_retries,
                last_failure_fingerprint=state["failure_fingerprint"],
            )
            return payload
        if decision.requires_user:
            question = _question(
                run_id=state["run_id"],
                prompt="当前结果未通过验证。是否在不扩大来源范围的前提下重试？",
                reason=decision.policy_reason,
                affected_scope="当前冻结计划和来源",
                options=(
                    HarnessQuestionOption(
                        value="retry",
                        label="原范围重试",
                        description="保持计划和来源不变，再执行一次。",
                    ),
                    HarnessQuestionOption(
                        value="stop",
                        label="停止任务",
                        description="保留诊断记录，但不产生权威交付。",
                    ),
                ),
            )
            update_run(
                state,
                status=HarnessStatus.NEEDS_USER,
                node=HarnessNode.NEEDS_USER,
                question=question,
                repair_rounds=next_round,
                last_failure_fingerprint=state["failure_fingerprint"],
            )
            return {
                "question": question.model_dump(mode="json"),
                "resume_target": "execute",
                "route": "needs_user",
            }
        update_run(
            state,
            status=HarnessStatus.FAILED,
            node=HarnessNode.REPAIR,
            repair_rounds=min(
                next_round, policy.max_total_repair_rounds
            ),
            last_failure_fingerprint=state["failure_fingerprint"],
        )
        return {"route": "end"}

    async def needs_user_node(state: HarnessState) -> dict[str, Any]:
        question = HarnessQuestion.model_validate(state["question"])
        run = load_run(state)
        update_run(
            state,
            status=HarnessStatus.NEEDS_USER,
            node=HarnessNode.NEEDS_USER,
            question=question,
            last_failure_fingerprint=run["last_failure_fingerprint"],
        )
        event(
            state,
            key=f"question:{question.question_id}",
            node=HarnessNode.NEEDS_USER,
            event_type="interrupted",
            summary=question.reason,
            details={"question_id": question.question_id},
        )
        raw_answer = interrupt(question.model_dump(mode="json"))
        answer = HarnessResume.model_validate(raw_answer)
        if (
            answer.question_id != question.question_id
            or answer.resume_token != question.resume_token
        ):
            raise ValueError("恢复答案与当前问题不匹配")
        allowed = {item.value for item in question.options}
        if answer.answer not in allowed:
            if not question.allow_free_text:
                raise ValueError("回答不符合当前问题 Schema")
            update_run(
                state,
                status=HarnessStatus.FAILED,
                node=HarnessNode.NEEDS_USER,
                last_failure_fingerprint=run["last_failure_fingerprint"],
            )
            event(
                state,
                key=f"question:{question.question_id}:free-text",
                node=HarnessNode.NEEDS_USER,
                event_type="new_revision_required",
                summary="自由文本可能改变任务语义，当前 run 已停止，请创建新 revision",
            )
            return {"route": "end"}
        if answer.answer == "stop":
            update_run(
                state,
                status=HarnessStatus.FAILED,
                node=HarnessNode.NEEDS_USER,
                last_failure_fingerprint=run["last_failure_fingerprint"],
            )
            return {"route": "end"}
        update_run(
            state,
            status=HarnessStatus.RUNNING,
            node=HarnessNode.NEEDS_USER,
            last_failure_fingerprint=run["last_failure_fingerprint"],
        )
        event(
            state,
            key=f"question:{question.question_id}:answered",
            node=HarnessNode.NEEDS_USER,
            event_type="resumed",
            summary="已校验当前问题的结构化回答并恢复",
        )
        result: dict[str, Any] = {"route": state["resume_target"]}
        if answer.answer == "confirm_external":
            result["external_confirmed"] = True
        return result

    async def deliver_node(state: HarnessState) -> dict[str, Any]:
        verification = VerificationReport.model_validate(
            state["verification"]
        )
        plan = SemanticTaskPlan.model_validate(state["logical_plan"])
        bound = BoundPlan.model_validate(state["bound_plan"])
        try:
            manifest = await asyncio.to_thread(
                create_delivery,
                store=runtime.store,
                output_root=runtime.output_root,
                user_id=state["user_id"],
                run_id=state["run_id"],
                plan=plan,
                artifact_paths={
                    key: Path(value)
                    for key, value in state["artifact_paths"].items()
                },
                source_artifact_hashes=bound.input_artifact_hashes,
            )
        except Exception as exc:
            message = safe_error_message(exc)
            run = load_run(state)
            update_run(
                state,
                status=HarnessStatus.FAILED,
                node=HarnessNode.DELIVER,
                verification=verification,
                eligible=False,
                same_failure_count=run["same_failure_count"],
                last_failure_fingerprint=run["last_failure_fingerprint"],
            )
            event(
                state,
                key="deliver:failed",
                node=HarnessNode.DELIVER,
                event_type="delivery_failed",
                summary=f"正式交付生成或 QA 失败：{message}",
                details={"formal_download_created": False},
            )
            return {"route": "end"}
        run = load_run(state)
        update_run(
            state,
            status=HarnessStatus.SUCCEEDED,
            node=HarnessNode.DELIVER,
            verification=verification,
            eligible=True,
            same_failure_count=0,
            last_failure_fingerprint=None,
        )
        event(
            state,
            key="deliver:eligible",
            node=HarnessNode.DELIVER,
            event_type="delivery_published",
            summary="验证通过，正式交付文件已完成独立 QA 并原子发布",
            details={
                "formal_download_created": True,
                "delivery_id": manifest.delivery_id,
                "output_count": len(manifest.outputs),
                "outputs": [
                    {
                        "output_id": item.output_id,
                        "format": item.format.value,
                        "download_url": item.download_url,
                    }
                    for item in manifest.outputs
                ],
            },
        )
        return {
            "delivery": manifest.model_dump(
                mode="json", exclude={"user_id"}
            ),
            "route": "end",
        }

    builder = StateGraph(HarnessState)
    builder.add_node(HarnessNode.INTERPRET.value, interpret_node)
    builder.add_node(HarnessNode.INSPECT.value, inspect_node)
    builder.add_node(HarnessNode.BIND.value, bind_node)
    builder.add_node(HarnessNode.PLAN.value, plan_node)
    builder.add_node(HarnessNode.EXECUTE.value, execute_node)
    builder.add_node(HarnessNode.VERIFY.value, verify_node)
    builder.add_node(HarnessNode.REPAIR.value, repair_node)
    builder.add_node(HarnessNode.NEEDS_USER.value, needs_user_node)
    builder.add_node(HarnessNode.DELIVER.value, deliver_node)
    builder.add_edge(START, HarnessNode.INTERPRET.value)
    builder.add_conditional_edges(
        HarnessNode.INTERPRET.value,
        lambda state: state["route"],
        {
            "inspect": HarnessNode.INSPECT.value,
            "needs_user": HarnessNode.NEEDS_USER.value,
            "repair": HarnessNode.REPAIR.value,
        },
    )
    builder.add_conditional_edges(
        HarnessNode.INSPECT.value,
        lambda state: state["route"],
        {
            "bind": HarnessNode.BIND.value,
            "repair": HarnessNode.REPAIR.value,
        },
    )
    builder.add_conditional_edges(
        HarnessNode.BIND.value,
        lambda state: state["route"],
        {
            "plan": HarnessNode.PLAN.value,
            "repair": HarnessNode.REPAIR.value,
        },
    )
    builder.add_conditional_edges(
        HarnessNode.PLAN.value,
        lambda state: state["route"],
        {
            "execute": HarnessNode.EXECUTE.value,
            "repair": HarnessNode.REPAIR.value,
        },
    )
    builder.add_conditional_edges(
        HarnessNode.EXECUTE.value,
        lambda state: state["route"],
        {
            "verify": HarnessNode.VERIFY.value,
            "repair": HarnessNode.REPAIR.value,
        },
    )
    builder.add_conditional_edges(
        HarnessNode.VERIFY.value,
        lambda state: state["route"],
        {
            "deliver": HarnessNode.DELIVER.value,
            "repair": HarnessNode.REPAIR.value,
        },
    )
    builder.add_conditional_edges(
        HarnessNode.REPAIR.value,
        lambda state: state["route"],
        {
            "execute": HarnessNode.EXECUTE.value,
            "plan": HarnessNode.PLAN.value,
            "needs_user": HarnessNode.NEEDS_USER.value,
            "end": END,
        },
    )
    builder.add_conditional_edges(
        HarnessNode.NEEDS_USER.value,
        lambda state: state["route"],
        {
            "inspect": HarnessNode.INSPECT.value,
            "execute": HarnessNode.EXECUTE.value,
            "end": END,
        },
    )
    builder.add_edge(HarnessNode.DELIVER.value, END)
    return builder.compile(checkpointer=checkpointer)


async def invoke_harness(
    runtime: HarnessRuntime,
    checkpointer: Any,
    *,
    user_id: str,
    run_id: str,
    resume: HarnessResume | None = None,
) -> dict[str, Any]:
    """用稳定 thread_id 启动或恢复；业务状态始终从用户隔离存储读取。"""

    graph = _build_graph(runtime, checkpointer)
    config = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": 64,
    }
    if resume is None:
        await graph.ainvoke(
            {"run_id": run_id, "user_id": user_id},
            config=config,
        )
    else:
        await graph.ainvoke(
            Command(resume=resume.model_dump(mode="json")),
            config=config,
        )
    row = runtime.store.get_semantic_harness_run(user_id, run_id)
    if row is None:
        raise PermissionError("Harness run 不存在或无权访问")
    return row
