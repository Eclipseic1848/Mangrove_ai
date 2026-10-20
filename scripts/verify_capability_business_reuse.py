"""在全新隔离库中验证两个业务样本的真实 Pi 与能力宿主调用。"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_result(result: dict, expected: dict, row_count: int) -> None:
    if not isinstance(result, dict) or set(result) != {"groups", "row_count"}:
        raise AssertionError("结果字段不符合任务要求")
    if not isinstance(result["groups"], dict) or any(not isinstance(value, str) for value in result["groups"].values()):
        raise AssertionError("汇总值必须为十进制字符串")
    if type(result["row_count"]) is not int or result["row_count"] != row_count:
        raise AssertionError("原始数据行数不符")
    if {key: Decimal(number) for key, number in result["groups"].items()} != {key: Decimal(number) for key, number in expected.items()}:
        raise AssertionError("汇总数值与独立预期不符")


async def verify(output: Path, timeout: int) -> None:
    import src.agent
    from src.api import auth
    from src.config.runtime_config import apply_global_overrides
    from src.config.settings import settings
    from src.database_migrations import DatabaseTarget, apply_migrations

    # 只读取现有模型配置；后续所有任务、账号、制品和运行状态均落在新目录。
    apply_global_overrides(auth.get_store())
    production_database = Path(settings.webui_db_path).resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings.webui_db_path = str(output / "isolated.db")
    settings.data_prep_upload_root = str(output / "uploads")
    settings.semantic_execution_root = str(output / "executions")
    settings.capability_oci_layout_path = str(output / "oci")
    settings.capability_platform_oci_layout_path = str(output / "oci-platform")
    settings.capability_mount_cache_path = str(output / "mounts")
    if Path(settings.webui_db_path).resolve() == production_database:
        raise RuntimeError("隔离库不得指向生产数据库")
    apply_migrations(DatabaseTarget(profile="webui", path=Path(settings.webui_db_path)), output / "before-migration.db")
    auth._store = None
    store = auth.get_store()
    owner = store.create_user("capability-isolated-check", auth.hash_password(secrets.token_urlsafe(32)), role="admin")["user_id"]

    from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeVersion
    from src.account_execution import execution_context
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
    from src.api.execution import execution_lock
    from src.agentic_runtime.draft_snapshot import read_draft
    from src.capability_catalog import CapabilityCatalog, CapabilityPackRef, CatalogActor, OrasOciLayoutStore, SqliteCapabilityCatalogRepository
    from src.capability_catalog.reuse import ToolNeed, match_installed_tools
    from src.conversation_steering import CapabilityMaturity, CapabilityPack, ProcedureScope
    from src.services.upload_store import UploadStore

    package = ROOT / "examples/capabilities/csv-group-summary"
    archive = output / "csv-group-summary-1.0.0.tar"
    with tarfile.open(archive, "w") as stream:
        for name in ("LICENSE", "README.md", "mangrove-capability.json", "reuse-contract.json", "table_summary.py"):
            path = package / name
            info = stream.gettarinfo(str(path), arcname=name)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with path.open("rb") as source:
                stream.addfile(info, source)
    artifact = OrasOciLayoutStore(settings.capability_oci_layout_path).push_file(
        archive, artifact_name="csv-group-summary", version="1.0.0",
        artifact_type="application/vnd.mangrove.capability.v1",
        layer_media_type="application/vnd.mangrove.capability.tar",
    )
    actor = CatalogActor(owner_id=owner, role="admin")
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(settings.webui_db_path))
    ref = CapabilityPackRef(pack_id="csv-group-summary", version="1.0.0", digest=artifact.digest)
    catalog.register_pack(actor, CapabilityPack(**ref.model_dump(), owner_id=owner,
        scope=ProcedureScope.PERSONAL, maturity=CapabilityMaturity.DRAFT,
        manifest=(("reuse_contract", (package / "reuse-contract.json").read_text(encoding="utf-8")),
                  ("license", "MIT"), ("kind", "tool"), ("purpose", "CSV 按列分组求和")),
        source_provenance=("https://github.com/Eclipseic1848/Mangrove_ai",)))
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    uploads = UploadStore(root=settings.data_prep_upload_root, max_bytes=settings.data_prep_max_upload_bytes)
    report = {"mode": "isolated_draft_validation", "production_tool_installed": False,
              "ref": ref.model_dump(), "cases": []}

    def save():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    manager = SemanticWorkspaceManager()
    manager.start()
    try:
        for index, (content, group, value, expected) in enumerate((
            ("部门,金额\n研发,0.1\n市场,12.50\n研发,0.2\n", "部门", "金额", {"研发": "0.3", "市场": "12.50"}),
            ("类别,件数\n办公,8\n设备,-2\n办公,4\n设备,0\n", "类别", "件数", {"办公": "12", "设备": "-2"}),
        ), start=1):
            need = ToolNeed(purpose=f"按{group}汇总{value}", operations=("group_sum",), input_formats=("csv",), output_formats=("json",))
            match = match_installed_tools(catalog, actor, need)
            if not match["matches"] or match["matches"][0]["ref"] != ref.model_dump():
                raise AssertionError("兼容匹配没有保持精确能力身份")
            upload = uploads.save_bytes(owner, f"业务样本{index}.csv", content.encode("utf-8"), media_type="text/csv")
            task_id = f"workspace_tool_reuse_{index}_{secrets.token_hex(5)}"
            with execution_context(store.capture_account_execution(owner)):
                store.create_semantic_workspace_task(owner, task_id=task_id, title=f"扩展工具业务验证 {index}",
                    objective_text=f"使用本次选用的本地分组求和工具读取附件 CSV，按“{group}”分组计算“{value}”总和。只输出一份 JSON，包含 groups（分组到总和的映射，数值用十进制字符串）和 row_count（原始数据行数）。保留零和负数，其他内容不要。",
                    upload_ids=[upload.upload_id], output_formats=["json"], provider="local", model=settings.llm_model_name,
                    external_api_confirmed=False, source_refs=[{"upload_id": upload.upload_id, "sha256": upload.sha256}])
            repository.register(RuntimeTaskConfig(user_id=owner, task_id=task_id, revision=1, runtime_version=RuntimeVersion.PI))
            # 新草稿只在明确标记的验证任务装载，不绕过普通业务的成熟度门。
            catalog.freeze_selection(actor, task_id=task_id, revision=1, pack_refs=(ref,), validation_target=ref)
            case = {"task_id": task_id, "state": "started", "input_sha256": upload.sha256, "ref": ref.model_dump(), "public_discovery_requested": False}
            report["cases"].append(case)
            save()
            manager.enqueue(owner, task_id)
            deadline = time.monotonic() + timeout
            while True:
                task = store.get_semantic_workspace_task(owner, task_id)
                if task["status"] == "needs_input":
                    run = repository.get(owner, task_id, 1)
                    question = task.get("question") or {}
                    if not question.get("draft_review_id") or question.get("draft_review_approved"):
                        raise AssertionError("任务需要额外输入或核验已停滞，保留原记录")
                    draft_root = Path(run["workspace_root"])
                    draft = read_draft(draft_root, question["draft_review_id"], owner_id=owner,
                                       task_id=task_id, revision=1, run_id=run["run_id"])
                    assert len(draft["files"]) == 1
                    draft_path = draft_root / "drafts" / draft["draft_id"] / draft["files"][0]["filename"]
                    check_result(json.loads(draft_path.read_text(encoding="utf-8")), expected, len(content.splitlines()) - 1)
                    # 只在独立核对合成初稿后批准继续核验，沿用产品的同版本、同 Run 门。
                    with execution_context(store.capture_account_execution(owner)), execution_lock(store, owner, "workspace", task_id):
                        enqueue = store.approve_workspace_draft_review(owner, task_id, 1, draft["draft_id"], run["run_id"])
                    if enqueue:
                        manager.enqueue(owner, task_id)
                    case["draft_independent_check"] = True
                    save()
                if task["status"] in {"completed", "candidate_ready", "failed", "cancelled"}:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("本次真实任务超时；保留原记录，不自动重发")
                await asyncio.sleep(2)
            case["state"] = task["status"]
            if task["status"] != "completed":
                raise AssertionError("任务未通过完整核验并完成")
            events = repository.list_events(owner, task_id, 1)
            case["successful_tools"] = sorted({event.get("details", {}).get("tool") for event in events if event["event_type"] == "tool.completed" and not event.get("details", {}).get("failed")})
            case["usage"] = [event["details"] for event in events if event["event_type"] == "provider.usage"]
            save()
            if "capability_csv_group_summary" not in case["successful_tools"]:
                raise AssertionError("未实际成功调用扩展工具")
            run = repository.get(owner, task_id, 1)
            candidates = run.get("candidates", [])
            if len(candidates) != 1:
                raise AssertionError("候选文件数量不符")
            candidate_path = candidates[0].host_path
            result = json.loads(candidate_path.read_text(encoding="utf-8"))
            check_result(result, expected, len(content.splitlines()) - 1)
            case["semantic_verification"] = run["verification"].status.value if run.get("verification") else "not_completed"
            case.update(result_verified=True, result_sha256=hashlib.sha256(candidate_path.read_bytes()).hexdigest())
            save()
        report["passed"] = True
    except Exception as error:
        report.update(passed=False, error_type=type(error).__name__)
        raise
    finally:
        await manager.stop()
        save()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    arguments = parser.parse_args()
    os.chdir(ROOT)
    asyncio.run(verify(arguments.output_directory, arguments.timeout))
