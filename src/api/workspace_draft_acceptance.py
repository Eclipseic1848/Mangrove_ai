"""用户接受初稿：停止旧执行，在新修订中复用同一发布器。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.agentic_runtime.draft_snapshot import read_draft
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.execution import execution_lock, execution_to_thread
from src.delivery_publishing.models import CandidateRef, DeliverySpec, OwnerAcceptance, PublicationGate, PublishCommand, canonical_hash
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.delivery_publishing.service import DeliveryPublisher
from src.runtime_routing import runtime_routing_is_p0_blocked
from src.source_acquisition.reuse import freeze_source_call
from src.services.managed_paths import ManagedPathCodec


class DraftAcceptanceConflict(ValueError):
    """可直接展示的恢复原因；不包含文件路径或底层解析异常。"""


async def accept_draft(*, store, manager, output_root: Path, owner_id: str, task_id: str,
                       source_revision: int, draft_id: str):
    task = store.get_semantic_workspace_task(owner_id, task_id)
    if task is None or task.get("deleted_at"):
        raise ValueError("任务不存在或无权访问")
    source = store.get_semantic_workspace_revision(owner_id, task_id, source_revision)
    runtime = AgenticRuntimeRepository(store.db_path).get(owner_id, task_id, source_revision)
    if source is None or runtime is None or not runtime.get("workspace_root"):
        raise ValueError("初稿所属执行不存在")
    root = Path(runtime["workspace_root"])
    try:
        draft = read_draft(root, draft_id, owner_id=owner_id, task_id=task_id,
                           revision=source_revision, run_id=runtime["run_id"])
    except (ValueError, OSError, KeyError) as exc:
        raise DraftAcceptanceConflict("初稿文件完整性校验未通过，不能采用；请检查初稿或选择新版本。") from exc
    target_revision = source_revision + 1
    current_contract = store.get_source_contract(owner_id, task_id, task["active_revision"]) or {}
    existing = current_contract.get("owner_acceptance")
    replay = (task["active_revision"] == target_revision and existing
              and existing.get("draft_id") == draft_id and existing.get("source_revision") == source_revision)
    if not replay:
        # 已停止的初稿可由 Owner 再次明确接受；本次操作之后的新取消仍由代数门拦截。
        if task["status"] == "completed":
            raise DraftAcceptanceConflict("任务已有正式交付，请查看当前正式结果；不会再次发布初稿。")
        if task["status"] == "cancelling":
            raise DraftAcceptanceConflict("验证仍在停止，请等待停止完成后检查处理状态，再决定是否采用。")
        if task["active_revision"] != source_revision or task["status"] in {"paused", "pausing"}:
            raise DraftAcceptanceConflict("任务版本或状态已变化，请检查处理状态并查看当前版本后再决定。")
        cancel_generation = task["cancel_generation"]
        if task["status"] in {"queued", "running", "needs_input"}:
            await manager.cancel(owner_id, task_id, for_revision=True)
    with execution_lock(store, owner_id, "workspace", task_id):
        current = store.get_semantic_workspace_task(owner_id, task_id)
        if current is None or current.get("deleted_at"):
            raise ValueError("任务不存在")
        # 等待停止期间另一请求可能已发布同一初稿，锁内重新识别重放。
        existing = (store.get_source_contract(owner_id, task_id, current["active_revision"]) or {}).get("owner_acceptance")
        replay = (current["active_revision"] == target_revision and existing
                  and existing.get("draft_id") == draft_id and existing.get("source_revision") == source_revision)
        if not replay and (current["active_revision"] != source_revision
                           or current["status"] in {"running", "queued", "cancelling", "completed"}
                           or current["cancel_generation"] != cancel_generation):
            raise DraftAcceptanceConflict("任务尚未停止或已收到新的操作，请检查处理状态；未发布新的结果。")
        candidates = tuple(CandidateRef(**{key: item[key] for key in
                           ("artifact_id", "filename", "format", "sha256", "size_bytes")}) for item in draft["files"])
        candidate_hash = canonical_hash([item.model_dump(mode="json") for item in sorted(candidates, key=lambda i: i.artifact_id)])
        if replay:
            acceptance = OwnerAcceptance.model_validate(existing)
        else:
            gaps = ["用户选择停止后续验证；未完成的系统检查不视为通过"]
            failure = runtime.get("failure")
            if failure:
                cause = failure.get("cause_summary") if isinstance(failure, dict) else getattr(failure, "cause_summary", None)
                if cause:
                    gaps.append(cause)
            acceptance = OwnerAcceptance(actor_id=owner_id, source_revision=source_revision,
                                         cancel_generation=current["cancel_generation"],
                                         draft_id=draft_id, candidate_set_hash=candidate_hash,
                                         accepted_at=datetime.now(timezone.utc).isoformat(), gaps=tuple(gaps))
            contract = dict(source.get("source_contract") or {})
            contract["owner_acceptance"] = acceptance.model_dump(mode="json")

            def freeze_revision():
                def keep_publication_pending(connection):
                    # 同一事务结束前阻止默认队列重新执行；接受只发布，不再次调用模型。
                    # 删除与接受不得互相覆盖；删除写者也持有同一任务执行锁。
                    if connection.execute("SELECT 1 FROM formal_delivery_runs WHERE owner_id=? AND task_id=? AND task_revision=?",
                                          (owner_id, task_id, source_revision)).fetchone():
                        raise ValueError("原任务已正式交付，不再接受初稿")
                    connection.execute("UPDATE semantic_workspace_tasks SET status='candidate_ready' WHERE user_id=? AND task_id=?",
                                       (owner_id, task_id))
                    connection.execute("UPDATE semantic_workspace_revisions SET status='candidate_ready' WHERE user_id=? AND task_id=? AND revision=?",
                                       (owner_id, task_id, target_revision))
                return store.create_semantic_workspace_revision(
                    owner_id, task_id, objective_text=source["objective_text"], output_formats=source["output_formats"],
                    change_summary="用户接受初稿并停止后续验证；原要求及未完成校验保留",
                    source_refs=source["source_refs"], source_contract=contract,
                    table_output_contracts=source.get("table_output_contracts", []),
                    expected_revision=target_revision, expected_cancel_generation=cancel_generation,
                    require_not_deleted=True,
                    transaction_hook=keep_publication_pending,
                )
            await execution_to_thread(freeze_source_call, owner_id, source["source_refs"], freeze_revision)
        target = store.get_semantic_workspace_revision(owner_id, task_id, target_revision)
        acceptance_hash = canonical_hash(acceptance.model_dump(mode="json"))
        command = PublishCommand.build(
            owner_id=owner_id, task_id=task_id, task_revision=target_revision,
            task_revision_hash=canonical_hash({"revision": target_revision, "owner_acceptance": acceptance_hash,
                                              "source_refs": target["source_refs"], "objective": target["objective_text"]}),
            goal_contract_hash=canonical_hash({"objective": target["objective_text"], "owner_acceptance": acceptance_hash}),
            run_id=f"accepted_{acceptance_hash[:24]}", candidates=candidates,
            verification_report_id=f"owner_acceptance_{acceptance_hash}", verification_report_hash=acceptance_hash,
            verification_status="inconclusive", owner_acceptance=acceptance,
            delivery_spec=DeliverySpec(requested_formats=tuple(target["output_formats"]), output_name=task["title"] or "结果",
                                       table_output_contracts=tuple(target.get("table_output_contracts") or ())),
            source_snapshot_refs=tuple(f"{ref.get('upload_id') or ref.get('artifact_id') or ref.get('output_id')}:{ref['sha256']}"
                                       for ref in target["source_refs"]),
        )

        def gate(_command):
            now = store.get_semantic_workspace_task(owner_id, task_id)
            frozen = store.get_source_contract(owner_id, task_id, target_revision) or {}
            return PublicationGate(cancel_requested=not now or bool(now.get("cancel_requested")) or bool(now.get("deleted_at"))
                                   or now["cancel_generation"] != acceptance.cancel_generation,
                                   revision_current=bool(now and now["active_revision"] == target_revision),
                                   p0_blocked=runtime_routing_is_p0_blocked(store.db_path),
                                   owner_acceptance_current=frozen.get("owner_acceptance") == acceptance.model_dump(mode="json"))

        def resolve(_command):
            checked = read_draft(root, draft_id, owner_id=owner_id, task_id=task_id,
                                 revision=source_revision, run_id=runtime["run_id"])
            return {item["artifact_id"]: root / "drafts" / draft_id / item["filename"] for item in checked["files"]}

        publisher = DeliveryPublisher(repository=DeliveryPublishingRepository(store.db_path,
                                      semantic_paths=ManagedPathCodec(output_root, legacy_anchor=("data", "semantic-executions"))), output_root=output_root,
                                      candidate_resolver=resolve, gate_reader=gate)
        delivery = await execution_to_thread(freeze_source_call, owner_id, target["source_refs"],
                                             publisher.publish, command, actor_id=owner_id)
        store.update_semantic_workspace_task(owner_id, task_id, expected_active_revision=target_revision,
                                             status="completed", run_id=command.run_id, summary="用户接受初稿，已生成正式结果（非系统验证通过）",
                                             error=None, failure=None, question=None)
        store.update_semantic_workspace_revision(owner_id, task_id, target_revision, status="completed", run_id=command.run_id,
                                                 summary="用户接受初稿，后续验证已停止")
        if current["status"] != "completed":
            store.append_semantic_workspace_event(owner_id, task_id, stage="deliver", event_type="delivery_published",
                summary="用户接受初稿，已正式交付；未完成的系统验证保留为未验证",
                details={"revision": target_revision, "delivery_id": delivery.delivery_id, "acceptance": "owner_accepted"})
        return {"status": "completed", "revision": target_revision, "delivery_id": delivery.delivery_id}
