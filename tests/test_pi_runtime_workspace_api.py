# -*- coding: utf-8 -*-
"""Pi 全能力灰度入口的工作台纵切面测试。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import src.api.auth as auth_mod
import src.api.semantic_workspace_runtime as runtime_mod
import src.model_connections.broker as broker_mod
from src.agentic_runtime.models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeResult,
    RuntimeEvent,
    RuntimeStatus,
    RuntimeTaskConfig,
    RuntimeVersion,
    SemanticDecision,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.auth import get_current_user
from src.api.routes import semantic_deliveries, semantic_workspace
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.config.settings import settings
from src.model_connections import ConnectionBroker
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from src.services.upload_store import UploadStore
from src.runtime_routing import (
    GateCheck,
    GateSnapshot,
    RolloutActor,
    RolloutApproval,
    RolloutMode,
    RuntimeRouting,
    SqliteRuntimeRoutingRepository,
    migrate_runtime_routing,
)


class FakePiRuntime:
    """不启动 Docker，只验证工作台与 Runtime Seam 的状态契约。"""

    def __init__(self) -> None:
        self.start_calls = 0
        self.requests: list[object] = []
        self.resume_calls: list[object] = []

    async def start(self, request, *, on_event):
        self.start_calls += 1
        self.requests.append(request)
        return await self._complete(request, on_event=on_event)

    async def resume(self, request, *, checkpoint, on_event):
        self.requests.append(request)
        self.resume_calls.append(checkpoint)
        return await self._complete(request, on_event=on_event)

    @staticmethod
    async def _complete(request, *, on_event):
        await on_event(
            RuntimeEvent(
                event_type="agent.started",
                summary="Pi 已开始观察资料并执行任务",
            )
        )


        root = (
            Path(settings.semantic_execution_root)
            / "fake-pi"
            / request.task_id
        )
        output = root / "output"
        output.mkdir(parents=True, exist_ok=True)
        requested_format = request.requested_output_formats[0]
        if requested_format == "json":
            candidate = output / "第一个报销审批单.json"
            candidate.write_text(
                json.dumps(
                    {
                        "李靖": {
                            "小计": 1,
                            "合计发票金额": 100,
                            "结算金额": 90,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        else:
            candidate = output / "服务费用标准及明细.csv"
            candidate.write_text(
                "姓名,费用合计\n张三,100\n董琳,150\n董琳,200\n",
                encoding="utf-8-sig",
            )
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return PiRuntimeResult(
            status=RuntimeStatus.CANDIDATE_READY,
            run_id="pi_run_test",
            workspace_root=root,
            container_name="mangrove-pi-test",
            candidates=(
                CandidateArtifact(
                    artifact_id=f"candidate_{digest[:16]}",
                    filename=candidate.name,
                    format=requested_format,
                    host_path=candidate,
                    sha256=digest,
                    size_bytes=candidate.stat().st_size,
                    openable=True,
                    qa_checks=("non_empty", "reopened"),
                ),
            ),
            verification=VerificationReport(
                status=VerificationStatus.PASSED,
                summary="候选已通过文件、来源证据和目标语义验证",
                checks=(
                    VerificationCheck(
                        code="source_grounding",
                        passed=True,
                        summary="已从原件重新确认 1 条证据",
                    ),
                ),
                evidence_count=1,
                formal_delivery_eligible=False,
            ),
        )

    async def cancel(
        self,
        _user_id: str,
        _task_id: str,
        _revision: int,
    ) -> None:
        return None


class InconclusivePiRuntime(FakePiRuntime):
    """先形成可下载候选，但模拟语义 Provider 瞬时无结论。"""

    @staticmethod
    async def _complete(request, *, on_event):
        result = await FakePiRuntime._complete(request, on_event=on_event)
        candidate = result.candidates[0]
        output = candidate.host_path.parent
        source = request.sources[0]
        (output / "candidate-manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "artifacts": [
                        {
                            "filename": candidate.filename,
                            "format": candidate.format,
                            "description": "用户要求的候选结果",
                            "evidence": [
                                {
                                    "source": source.original_name,
                                    "locator": "page:1",
                                    "quote": "已在首次验证中确认的来源证据",
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return result.model_copy(
            update={
                "verification": VerificationReport(
                    status=VerificationStatus.INCONCLUSIVE,
                    summary="文件与来源证据有效，但独立语义验证未形成可靠结论",
                    checks=(
                        VerificationCheck(
                            code="artifact_set",
                            passed=True,
                            summary="候选文件与证据清单一致",
                        ),
                        VerificationCheck(
                            code="artifact_count",
                            passed=True,
                            summary="候选数量符合用户明确要求",
                        ),
                        VerificationCheck(
                            code="source_grounding",
                            passed=True,
                            summary="已从原件重新确认 1 条证据",
                        ),
                        VerificationCheck(
                            code="semantic_goal",
                            passed=False,
                            summary=(
                                "1 validation error for SemanticDecision Invalid "
                                "JSON: EOF https://errors.pydantic.dev/json_invalid"
                            ),
                        ),
                    ),
                    evidence_count=1,
                    formal_delivery_eligible=False,
                )
            }
        )


class PassingRetryJudge:
    async def judge(self, **_kwargs):
        return SemanticDecision(
            passed=True,
            contains_unrequested_content=False,
            reason="候选满足目标且没有额外内容",
        )


class ClarifyingPiRuntime(FakePiRuntime):
    async def start(self, request, *, on_event):
        self.start_calls += 1
        self.requests.append(request)
        await on_event(
            RuntimeEvent(
                event_type="tool.completed",
                summary="发现范围歧义",
                details={"tool": "request_clarification"},
            )
        )
        root = (
            Path(settings.semantic_execution_root)
            / "fake-pi"
            / request.task_id
        )
        root.mkdir(parents=True, exist_ok=True)
        return PiRuntimeResult(
            status=RuntimeStatus.NEEDS_INPUT,
            run_id="pi_run_clarify",
            workspace_root=root,
            summary="你需要第一条记录，还是全部记录？",
            clarification={
                "question": "你需要第一条记录，还是全部记录？",
                "reason": "两种解释会改变结果数量",
            },
        )


class CapabilityPiRuntime(FakePiRuntime):
    """模拟任务确实加载了冻结能力，验证可选阶段会按事实出现。"""

    async def start(self, request, *, on_event):
        self.start_calls += 1
        self.requests.append(request)
        await on_event(
            RuntimeEvent(
                event_type="capability.completed",
                summary="已准备 1 项能力：MinerU 文档解析（Tool）",
                details={
                    "capability_count": 1,
                    "refs": {
                        "capabilities": [
                            {
                                "name": "MinerU 文档解析",
                                "kind": "tool",
                                "version": "2.1.0",
                                "purpose": "解析 PDF 文档结构",
                            }
                        ]
                    },
                },
            )
        )
        return await self._complete(request, on_event=on_event)


class BlockingPiRuntime:
    """模拟正在运行的容器，验证取消会到达 Runtime 硬终止接口。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancel_calls: list[tuple[str, str, int]] = []

    async def start(self, request, *, on_event):
        await on_event(
            RuntimeEvent(
                event_type="agent.started",
                summary="Pi 已开始执行长任务",
            )
        )
        self.started.set()
        await asyncio.Event().wait()

    async def resume(self, request, *, checkpoint, on_event):
        return await self.start(request, on_event=on_event)

    async def cancel(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> None:
        self.cancel_calls.append((user_id, task_id, revision))


class PreFreezeInspectingPiRuntime(BlockingPiRuntime):
    """模拟 Pi 为理解目标先观察来源，随后才冻结覆盖契约。"""

    async def start(self, request, *, on_event):
        await on_event(
            RuntimeEvent(
                event_type="agent.started",
                summary="Pi 已开始观察资料并执行任务",
            )
        )
        await on_event(
            RuntimeEvent(
                event_type="tool.started",
                summary="正在识别来源结构",
                details={"tool": "inspect_source"},
            )
        )
        await on_event(
            RuntimeEvent(
                event_type="tool.completed",
                summary="inspect_source 已完成",
                details={"tool": "inspect_source"},
            )
        )
        self.started.set()
        await asyncio.Event().wait()


class EmptyOutputPiRuntime:
    """模拟容器已建立工作区，但没有形成任何用户结果。"""

    async def start(self, request, *, on_event):
        root = (
            Path(settings.semantic_execution_root)
            / "empty-pi"
            / request.task_id
        )
        (root / "output").mkdir(parents=True, exist_ok=True)
        await on_event(
            RuntimeEvent(
                event_type="runtime.preparing",
                summary="已建立隔离工作区",
                details={
                    "_checkpoint": {
                        "run_id": "pi_run_empty",
                        "workspace_root": str(root),
                        "container_name": "mangrove-pi-empty",
                        "session_file": None,
                    }
                },
            )
        )
        raise ValueError("Pi 未生成可重新打开的请求格式文件：json")

    async def resume(self, request, *, checkpoint, on_event):
        return await self.start(request, on_event=on_event)

    async def cancel(
        self,
        _user_id: str,
        _task_id: str,
        _revision: int,
    ) -> None:
        return None


class AmbiguousProviderPiRuntime(EmptyOutputPiRuntime):
    """模拟请求可能已到 Provider，但客户端没有拿到确定结果。"""

    failure_message = (
        "模型请求结果不确定，已停止自动重试；"
        "请由用户决定是否创建新版本重新执行"
    )

    async def start(self, request, *, on_event):
        try:
            await super().start(request, on_event=on_event)
        except ValueError as exc:
            raise ValueError(self.failure_message) from exc

    async def resume(self, request, *, checkpoint, on_event):
        await self.start(request, on_event=on_event)


class ProviderTimeoutPiRuntime(AmbiguousProviderPiRuntime):
    failure_message = "Pi 执行超过 1800 秒预算"


def _client(
    tmp_path: Path,
    monkeypatch,
    *,
    role: str,
    pi_runtime: (
        FakePiRuntime
        | ClarifyingPiRuntime
        | BlockingPiRuntime
        | EmptyOutputPiRuntime
        | None
    ) = None,
) -> TestClient:
    monkeypatch.setattr(
        settings, "webui_db_path", str(tmp_path / "workspace.db")
    )
    monkeypatch.setattr(
        settings, "data_prep_upload_root", str(tmp_path / "uploads")
    )
    monkeypatch.setattr(
        settings,
        "semantic_execution_root",
        str(tmp_path / "executions"),
    )
    auth_mod._store = None
    manager = SemanticWorkspaceManager(
        pi_runtime=pi_runtime or FakePiRuntime(),
    )
    monkeypatch.setattr(runtime_mod, "_manager", manager)

    @asynccontextmanager
    async def lifespan(_app):
        manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(semantic_workspace.router)
    app.include_router(semantic_deliveries.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": role,
    }
    return TestClient(app)


def _uploads(tmp_path: Path) -> tuple[str, str]:
    store = UploadStore(
        root=str(tmp_path / "uploads"),
        max_bytes=10 * 1024 * 1024,
    )
    document = store.save_bytes(
        "user-a",
        "附件2.pdf",
        b"%PDF-test",
        media_type="application/pdf",
    )
    table = store.save_bytes(
        "user-a",
        "补充信息.csv",
        b"name,value\nx,1\n",
        media_type="text/csv",
    )
    return document.upload_id, table.upload_id


def _g3_snapshot(*, passed: bool) -> GateSnapshot:
    return GateSnapshot.build(
        gate_version="phase4-g3-api-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(
                gate_id="delivery-integrity",
                passed=passed,
                evidence_hash="c" * 64,
            ),
        ),
    )


def test_runtime_rollout_only_changes_new_tasks_and_p0_restores_legacy(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "pi_runtime_enabled", True)
    client = _client(tmp_path, monkeypatch, role="admin")
    document, _ = _uploads(tmp_path)
    # 先让现有 Store 建完历史表，再在同一测试库执行显式 G3 迁移。
    auth_mod.get_store()
    database = Path(settings.webui_db_path)
    migrate_runtime_routing(database, tmp_path / "workspace-before-g3.db")
    routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
    passed = _g3_snapshot(passed=True)
    actor = RolloutActor(actor_id="admin-a", role="admin")
    routing.record_gate(passed, actor)
    gray_approval = RolloutApproval(
        approval_id="approval-api-gray",
        target_mode=RolloutMode.EXPLICIT_OPT_IN,
        gate_snapshot_id=passed.snapshot_id,
        approved_by="maintainer-a",
    )
    routing.record_approval(
        gray_approval,
        RolloutActor(actor_id="maintainer-a", role="user"),
    )
    routing.change_mode(
        RolloutMode.EXPLICIT_OPT_IN,
        gray_approval,
        actor,
    )
    default_approval = RolloutApproval(
        approval_id="approval-api-default",
        target_mode=RolloutMode.VNEXT_DEFAULT,
        gate_snapshot_id=passed.snapshot_id,
        approved_by="maintainer-a",
    )
    routing.record_approval(
        default_approval,
        RolloutActor(actor_id="maintainer-a", role="user"),
    )
    routing.change_mode(
        RolloutMode.VNEXT_DEFAULT,
        default_approval,
        actor,
    )

    request = {
        "objective_text": "提取附件内容并输出 JSON",
        "upload_ids": [document],
        "output_formats": ["json"],
        "provider": "local",
    }
    with client:
        vnext = client.post("/api/semantic-workspace/tasks", json=request)
        assert vnext.status_code == 202, vnext.text
        assert vnext.json()["runtime_version"] == "pi"

        cancelled = client.post(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}/cancel"
        )
        assert cancelled.status_code in {200, 409}, cancelled.text
        original_register = AgenticRuntimeRepository.register_in_transaction

        def fail_after_runtime_config(self, connection, config):
            original_register(self, connection, config)
            raise RuntimeError("模拟 RuntimeConfig 事务失败")

        monkeypatch.setattr(
            AgenticRuntimeRepository,
            "register_in_transaction",
            fail_after_runtime_config,
        )
        failed_revision = client.post(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}/revisions",
            json={
                "instruction": "模拟路由绑定后的存储失败",
                "expected_active_revision": 1,
            },
        )
        assert failed_revision.status_code == 409, failed_revision.text
        monkeypatch.setattr(
            AgenticRuntimeRepository,
            "register_in_transaction",
            original_register,
        )
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM runtime_assignments "
                "WHERE owner_id=? AND task_id=? AND revision=2",
                ("user-a", vnext.json()["task_id"]),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT active_revision FROM semantic_workspace_tasks "
                "WHERE user_id=? AND task_id=?",
                ("user-a", vnext.json()["task_id"]),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=2",
                ("user-a", vnext.json()["task_id"]),
            ).fetchone()[0] == 0

        routing.record_gate(_g3_snapshot(passed=False), actor)
        blocked = client.post(
            "/api/semantic-workspace/tasks",
            json={**request, "runtime_version": "pi"},
            headers={"Idempotency-Key": "g3-p0-retry"},
        )
        assert blocked.status_code == 409, blocked.text
        legacy = client.post(
            "/api/semantic-workspace/tasks",
            json=request,
            headers={"Idempotency-Key": "g3-p0-retry"},
        )
        assert legacy.status_code == 202, legacy.text
        assert legacy.json()["runtime_version"] == "legacy"

        unchanged = client.get(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}"
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["runtime_version"] == "pi"
        for task_id in (vnext.json()["task_id"], legacy.json()["task_id"]):
            cancelled = client.post(
                f"/api/semantic-workspace/tasks/{task_id}/cancel"
            )
            assert cancelled.status_code in {200, 409}, cancelled.text
        rejected_revision = client.post(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}/revisions",
            json={
                "instruction": "回退后错误请求表格契约",
                "expected_active_revision": 1,
                "output_formats": ["csv"],
                "table_output_contracts": [{
                    "format": "csv",
                    "exact_columns": ["姓名"],
                }],
            },
        )
        assert rejected_revision.status_code == 422, rejected_revision.text
        with sqlite3.connect(database) as connection:
            assignment_count = connection.execute(
                "SELECT COUNT(*) FROM runtime_assignments "
                "WHERE owner_id=? AND task_id=? AND revision=2",
                ("user-a", vnext.json()["task_id"]),
            ).fetchone()[0]
        assert assignment_count == 0
        revised = client.post(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}/revisions",
            json={
                "instruction": "保持原目标，创建回退后的新版本",
                "expected_active_revision": 1,
            },
        )
        assert revised.status_code == 202, revised.text
        active = client.get(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}"
        )
        historical = client.get(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}",
            params={"revision": 1},
        )
        assert active.json()["runtime_version"] == "legacy"
        assert historical.json()["runtime_version"] == "pi"
        client.post(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}/cancel"
        )


def _wait_for_delivery(client: TestClient, task_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/semantic-workspace/tasks/{task_id}"
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] == "completed":
            return payload
        if payload["status"] == "failed":
            raise AssertionError(f"Pi 正式发布失败：{payload.get('failure')}")
        time.sleep(0.05)
    raise AssertionError("Pi 任务未形成正式交付终态")


def _wait_for_status(
    client: TestClient,
    task_id: str,
    expected: str,
) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/semantic-workspace/tasks/{task_id}"
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] == expected:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Pi 任务未进入 {expected} 状态")


def test_pi_request_uses_table_contract_frozen_by_task_revision(
    tmp_path,
    monkeypatch,
) -> None:
    fake_runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=fake_runtime,
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "提取姓名和费用合计并输出 CSV",
                "upload_ids": [document],
                "output_formats": ["csv"],
                "table_output_contracts": [{
                    "format": "csv",
                    "exact_columns": ["姓名", "费用合计"],
                }],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        _wait_for_delivery(client, created.json()["task_id"])
        mismatched_revision = client.post(
            (
                "/api/semantic-workspace/tasks/"
                f"{created.json()['task_id']}/revisions"
            ),
            json={
                "instruction": "保持输出格式但改用另一份结构契约",
                "expected_active_revision": 1,
                "table_output_contracts": [{
                    "format": "xlsx",
                    "exact_columns": ["姓名", "费用合计"],
                }],
            },
        )
        assert mismatched_revision.status_code == 422

    assert fake_runtime.requests[0].table_output_contracts[0].exact_columns == (
        "姓名",
        "费用合计",
    )


def test_scanned_pdf_is_not_eagerly_ocr_prepared_before_pi_execution(
    tmp_path,
    monkeypatch,
) -> None:
    fake_runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=fake_runtime,
    )
    image_path = tmp_path / "scan.pdf"
    first_page = Image.new("RGB", (300, 200), "white")
    second_page = Image.new("RGB", (300, 200), "white")
    first_page.save(
        image_path,
        format="PDF",
        save_all=True,
        append_images=[second_page],
    )
    upload = UploadStore(
        root=str(tmp_path / "uploads"),
        max_bytes=10 * 1024 * 1024,
    ).save_bytes(
        "user-a",
        "扫描报销单.pdf",
        image_path.read_bytes(),
        media_type="application/pdf",
    )

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "提取报销人和结算金额并输出 CSV",
                "upload_ids": [upload.upload_id],
                "output_formats": ["csv"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        _wait_for_delivery(client, created.json()["task_id"])
        revised = client.post(
            (
                "/api/semantic-workspace/tasks/"
                f"{created.json()['task_id']}/revisions"
            ),
            json={
                "instruction": "保持字段不变，重新生成结果",
                "expected_active_revision": 1,
            },
        )
        assert revised.status_code == 202, revised.text
        _wait_for_delivery(client, created.json()["task_id"])

    request = fake_runtime.requests[0]
    assert [source.original_name for source in request.sources] == [
        "扫描报销单.pdf",
    ]
    # OCR 只能在 Pi 调用文档能力后按需发生，不能在理解目标前遍历整份来源。
    assert not list((tmp_path / "executions").glob("**/*.jsonl"))


def test_unavailable_ocr_does_not_block_pi_before_tool_is_requested(
    tmp_path,
    monkeypatch,
) -> None:
    fake_runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=fake_runtime,
    )
    image_path = tmp_path / "scan-unavailable.pdf"
    Image.new("RGB", (300, 200), "white").save(
        image_path,
        format="PDF",
    )
    upload = UploadStore(
        root=str(tmp_path / "uploads"),
        max_bytes=10 * 1024 * 1024,
    ).save_bytes(
        "user-a",
        "扫描报销单.pdf",
        image_path.read_bytes(),
        media_type="application/pdf",
    )

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "提取报销人和结算金额并输出 CSV",
                "upload_ids": [upload.upload_id],
                "output_formats": ["csv"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_status(
            client,
            created.json()["task_id"],
            "completed",
        )

    assert fake_runtime.start_calls == 1
    assert task["failure"] is None
    assert [
        source.original_name
        for source in fake_runtime.requests[0].sources
    ] == ["扫描报销单.pdf"]


def test_pi_material_ambiguity_becomes_one_reopenable_question(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = ClarifyingPiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "查一下张三的数据",
                "upload_ids": [document],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_status(
            client,
            created.json()["task_id"],
            "needs_input",
        )

    assert task["question"]["kind"] == "plan"
    assert task["question"]["allow_free_text"] is True
    assert task["question"]["prompt"] == (
        "你需要第一条记录，还是全部记录？"
    )
    assert runtime.start_calls == 1


def test_empty_pi_workspace_is_not_reported_as_intermediate_result(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=EmptyOutputPiRuntime(),
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取附件并输出 JSON",
                "upload_ids": [document],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_status(
            client,
            created.json()["task_id"],
            "failed",
        )

    assert task["failure"]["error_code"] == "PI_RUNTIME_FAILED"
    assert task["failure"]["intermediate_created"] is False
    assert task["failure"]["next_actions"] == [
        "查看任务执行记录",
        "修改要求后创建新版本",
        "停止任务",
    ]


def test_ambiguous_provider_outcome_requires_user_retry_decision(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=AmbiguousProviderPiRuntime(),
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取附件并输出 JSON",
                "upload_ids": [document],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_status(
            client,
            created.json()["task_id"],
            "failed",
        )
        missing_revision = client.post(
            f"/api/semantic-workspace/tasks/{task['task_id']}/revisions",
            json={
                "instruction": "保持原要求，重新执行",
                "external_api_confirmed": True,
            },
        )
        retry_payload = {
            "instruction": "保持原要求，重新执行",
            "external_api_confirmed": True,
            "expected_active_revision": task["active_revision"],
        }
        retried = client.post(
            f"/api/semantic-workspace/tasks/{task['task_id']}/revisions",
            json=retry_payload,
        )
        assert retried.status_code == 202, retried.text
        stale_retry = client.post(
            f"/api/semantic-workspace/tasks/{task['task_id']}/revisions",
            json=retry_payload,
        )

    assert task["failure"]["error_code"] == "MODEL_OUTCOME_UNKNOWN"
    assert missing_revision.status_code == 422
    assert task["failure"]["attempt_count"] == 1
    assert task["failure"]["next_actions"] == [
        "由你决定是否创建新版本重新执行",
        "取消并保留当前失败记录",
    ]
    assert stale_retry.status_code == 409
    assert "查看最新结果" in stale_retry.json()["detail"]


def test_external_provider_timeout_requires_user_retry_decision(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="user",
        pi_runtime=ProviderTimeoutPiRuntime(),
    )
    document, _ = _uploads(tmp_path)
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(settings.webui_db_path),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "OK"}}],
                    "usage": {"total_tokens": 1},
                },
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    connection = asyncio.run(
        broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="personal-provider-secret-1234",
        )
    )
    broker.set_usage_preference(
        "user-a",
        str(connection["connection_id"]),
        str(connection["default_model"]),
    )
    monkeypatch.setattr(broker_mod, "_default_broker", broker)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取附件并输出 JSON",
                "upload_ids": [document],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "external_api_confirmed": True,
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_status(client, created.json()["task_id"], "failed")

    assert task["failure"]["error_code"] == "MODEL_OUTCOME_UNKNOWN"
    assert task["failure"]["attempt_count"] == 1


def test_pi_local_gray_entry_requires_admin(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, role="user")
    document, _ = _uploads(tmp_path)
    with client:
        response = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "提取附件表格并输出 CSV",
                "upload_ids": [document],
                "output_formats": ["csv"],
                "runtime_version": "pi",
            },
        )
    assert response.status_code == 403
    assert "需要选择自己的连接或管理员发布的连接" in response.json()[
        "detail"
    ]


def test_user_can_explicitly_use_own_verified_connection_for_standard_vnext_task(
    tmp_path,
    monkeypatch,
) -> None:
    fake_runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="user",
        pi_runtime=fake_runtime,
    )
    document, _ = _uploads(tmp_path)
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(settings.webui_db_path),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "OK",
                            }
                        }
                    ]
                },
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    connection = asyncio.run(
        broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="personal-provider-secret-1234",
        )
    )
    binding = broker.freeze_connection(
        "user-a",
        str(connection["connection_id"]),
    )
    broker.set_usage_preference(
        "user-a",
        str(connection["connection_id"]),
        str(connection["default_model"]),
    )
    monkeypatch.setattr(broker_mod, "_default_broker", broker)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取附件并输出一个 CSV",
                "upload_ids": [document],
                "output_formats": ["csv"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "external_api_confirmed": True,
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_delivery(client, created.json()["task_id"])
        unconfirmed_revision = client.post(
            (
                "/api/semantic-workspace/tasks/"
                f"{created.json()['task_id']}/revisions"
            ),
            json={
                "instruction": "把结果中的金额按降序排列",
                "expected_active_revision": 1,
            },
        )
        assert unconfirmed_revision.status_code == 422
        assert "再次确认" in unconfirmed_revision.json()["detail"]
        confirmed_revision = client.post(
            (
                "/api/semantic-workspace/tasks/"
                f"{created.json()['task_id']}/revisions"
            ),
            json={
                "instruction": "把结果中的金额按降序排列",
                "external_api_confirmed": True,
                "expected_active_revision": 1,
            },
        )
        assert confirmed_revision.status_code == 202, confirmed_revision.text
        revised = _wait_for_delivery(
            client,
            created.json()["task_id"],
        )

    assert task["agentic_runtime"]["model_connection_id"] == connection[
        "connection_id"
    ]
    runtime_request = fake_runtime.requests[0]
    assert runtime_request.model_connection_id == connection["connection_id"]
    assert (
        runtime_request.model_connection_version
        == binding.connection_version
    )
    assert runtime_request.model is None
    assert runtime_request.base_url is None
    assert runtime_request.api_key is None
    assert revised["active_revision"] == 2
    assert fake_runtime.requests[-1].model_connection_id == connection[
        "connection_id"
    ]
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    for revision in (1, 2):
        frozen = repository.get(
            "user-a",
            created.json()["task_id"],
            revision,
        )
        assert frozen is not None
        assert frozen["model_connection_version"] == (
            binding.connection_version
        )
        assert frozen["model_connection_model"] == connection["default_model"]
        assert frozen["external_api_confirmed"] is True
    assert "personal-provider-secret-1234" not in created.text
    assert "personal-provider-secret-1234" not in str(task)


def test_pi_gray_entry_accepts_mixed_sources_and_exposes_candidate(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(tmp_path, monkeypatch, role="admin")
    document, table = _uploads(tmp_path)
    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": (
                    "从附件中抽取服务费用标准及明细，只输出一张 CSV"
                ),
                "upload_ids": [document, table],
                "output_formats": ["csv"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_delivery(
            client, created.json()["task_id"]
        )
        runtime = task["agentic_runtime"]
        assert runtime["runtime_version"] == "pi"
        assert runtime["status"] == "candidate_ready"
        assert runtime["verification"]["status"] == "passed"
        assert task["delivery"] is not None
        assert task["delivery"]["status"] == "succeeded"
        assert len(runtime["candidates"]) == 1
        persisted = AgenticRuntimeRepository(
            settings.webui_db_path
        ).get("user-a", task["task_id"], 1)
        assert persisted is not None
        assert "api_key" not in persisted["request"]
        assert persisted["verified_candidate_set_hash"] == task[
            "delivery"
        ]["provenance"]["candidate_set_hash"]

        candidate = runtime["candidates"][0]
        assert candidate["download_allowed"] is True
        downloaded = client.get(candidate["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.headers[
            "x-mangrove-artifact-status"
        ] == "unverified-candidate"
        assert "姓名,费用合计" in downloaded.content.decode(
            "utf-8-sig"
        )
        formal_output = task["delivery"]["outputs"][0]
        formal_download = client.get(formal_output["download_url"])
        assert formal_download.status_code == 200
        assert formal_download.content == downloaded.content

        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-b",
            "role": "admin",
        }
        assert client.get(candidate["download_url"]).status_code == 404
        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-a",
            "role": "admin",
        }

        AgenticRuntimeRepository(settings.webui_db_path).update(
            "user-a",
            task["task_id"],
            1,
            verification=VerificationReport(
                status=VerificationStatus.FAILED,
                summary="候选来源无法确认",
                checks=(
                    VerificationCheck(
                        code="source_grounding",
                        passed=False,
                        summary="来源中找不到声明的证据",
                    ),
                ),
                evidence_count=0,
                formal_delivery_eligible=False,
            ),
        )
        review_download = client.get(candidate["download_url"])
        assert review_download.status_code == 200
        assert review_download.headers[
            "x-mangrove-artifact-status"
        ] == "unverified-candidate"

        unsafe_path = persisted["candidates"][0].host_path.with_name(
            "未验证报告.pdf"
        )
        unsafe_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        unsafe_digest = hashlib.sha256(unsafe_path.read_bytes()).hexdigest()
        unsafe = CandidateArtifact(
            artifact_id=f"candidate_{unsafe_digest[:16]}",
            filename=unsafe_path.name,
            format="pdf",
            host_path=unsafe_path,
            sha256=unsafe_digest,
            size_bytes=unsafe_path.stat().st_size,
            openable=True,
            qa_checks=("non_empty", "reopened"),
        )
        AgenticRuntimeRepository(settings.webui_db_path).update(
            "user-a",
            task["task_id"],
            1,
            candidates=(unsafe,),
        )
        blocked = client.get(
            f"/api/semantic-workspace/tasks/{task['task_id']}/candidates/"
            f"{unsafe.artifact_id}?revision=1"
        )
        assert blocked.status_code == 409
        assert "已禁止下载" in blocked.json()["detail"]


def test_retry_inconclusive_candidate_verification_does_not_rerun_pi(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = InconclusivePiRuntime()
    monkeypatch.setattr(
        SemanticWorkspaceManager,
        "_build_retry_semantic_judge",
        lambda *_args, **_kwargs: PassingRetryJudge(),
        raising=False,
    )
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取附件并只输出一份 JSON",
                "upload_ids": [document],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        candidate = _wait_for_status(client, task_id, "candidate_ready")
        assert candidate["agentic_runtime"]["verification"]["status"] == (
            "inconclusive"
        )
        semantic_check = next(
            check
            for check in candidate["agentic_runtime"]["verification"]["checks"]
            if check["code"] == "semantic_goal"
        )
        assert semantic_check["summary"] == (
            "语义验证服务暂时不可用，请稍后重新验证候选。"
        )
        assert "pydantic" not in str(candidate).lower()
        assert runtime.start_calls == 1

        retried = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verification/retry"
        )

        assert retried.status_code == 200, retried.text
        payload = retried.json()
        assert payload["status"] == "completed"
        assert payload["delivery"]["status"] == "succeeded"
        assert payload["agentic_runtime"]["verification"]["status"] == "passed"
        assert runtime.start_calls == 1


def test_published_pi_json_can_be_previewed_from_formal_delivery(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=FakePiRuntime(),
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取第一个报销审批单，按人员输出 JSON",
                "upload_ids": [document],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_delivery(client, created.json()["task_id"])
        preview = client.get(
            f"/api/semantic-workspace/tasks/{task['task_id']}/preview",
            params={"revision": 1},
        )

    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["kind"] == "document"
    assert payload["total"] == 1
    assert payload["items"][0]["label"] == "李靖"
    assert '"结算金额": 90' in payload["items"][0]["content"]


def test_published_pi_csv_supports_table_preview_search_and_sort(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=FakePiRuntime(),
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "提取董琳相关数据并输出 CSV",
                "upload_ids": [document],
                "output_formats": ["csv"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_delivery(client, created.json()["task_id"])
        preview = client.get(
            f"/api/semantic-workspace/tasks/{task['task_id']}/preview",
            params={
                "revision": 1,
                "search": "董琳",
                "sort_by": "费用合计",
                "sort_direction": "desc",
                "limit": 1,
            },
        )

    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["kind"] == "table"
    assert payload["columns"] == ["姓名", "费用合计"]
    assert payload["total"] == 2
    assert payload["rows"] == [{"姓名": "董琳", "费用合计": 200}]


def test_pi_progress_shows_capability_stage_only_when_capability_was_loaded(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=CapabilityPiRuntime(),
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "使用已验证方案提取费用并输出 CSV",
                "upload_ids": [document],
                "output_formats": ["csv"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task = _wait_for_delivery(client, created.json()["task_id"])

    capability = next(
        stage
        for stage in task["progress"]["stages"]
        if stage["stage"] == "prepare_capabilities"
    )
    assert capability == {
        "stage": "prepare_capabilities",
        "status": "completed",
        "summary": "已准备 1 项能力：MinerU 文档解析（Tool）",
    }
    capability_event = next(
        event
        for event in task["progress"]["events"]
        if event["stage"] == "prepare_capabilities"
    )
    assert capability_event["refs"] == {
        "capabilities": [
            {
                "name": "MinerU 文档解析",
                "kind": "tool",
                "version": "2.1.0",
                "purpose": "解析 PDF 文档结构",
            }
        ]
    }


def test_manager_restart_resumes_persisted_pi_run_instead_of_starting_again(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings, "webui_db_path", str(tmp_path / "workspace.db")
    )
    monkeypatch.setattr(
        settings, "data_prep_upload_root", str(tmp_path / "uploads")
    )
    monkeypatch.setattr(
        settings,
        "semantic_execution_root",
        str(tmp_path / "executions"),
    )
    auth_mod._store = None
    document, _ = _uploads(tmp_path)
    store = auth_mod.get_store()
    task_id = "workspace_interrupted_pi"
    store.create_semantic_workspace_task(
        "user-a",
        task_id=task_id,
        title="恢复 Pi 任务",
        objective_text="从附件抽取表格，只输出一张 CSV",
        upload_ids=[document],
        output_formats=["csv"],
        provider="local",
        model="Qwen3.6-35B-A3B",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task(
        "user-a",
        task_id,
        status="running",
    )
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    repository.register(
        RuntimeTaskConfig(
            user_id="user-a",
            task_id=task_id,
            revision=1,
            runtime_version=RuntimeVersion.PI,
            permission_profile=PermissionProfile.STANDARD,
        )
    )
    checkpoint_root = tmp_path / "executions" / "interrupted"
    repository.update(
        "user-a",
        task_id,
        1,
        status=RuntimeStatus.RUNNING,
        run_id="pi_run_interrupted",
        container_name="mangrove-pi-interrupted",
        workspace_root=checkpoint_root,
        session_file="session/interrupted.jsonl",
    )
    fake_runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=fake_runtime,
    )

    with client:
        task = _wait_for_delivery(client, task_id)

    assert task["status"] == "completed"
    assert fake_runtime.start_calls == 0
    assert len(fake_runtime.resume_calls) == 1


def test_cancel_running_pi_task_calls_runtime_hard_stop(
    tmp_path,
    monkeypatch,
) -> None:
    blocking_runtime = BlockingPiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=blocking_runtime,
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取附件并输出一个 CSV",
                "upload_ids": [document],
                "output_formats": ["csv"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        assert blocking_runtime.started.wait(timeout=5)
        _wait_for_status(client, task_id, "running")

        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-b",
            "role": "admin",
        }
        denied = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/cancel"
        )
        assert denied.status_code == 404
        assert blocking_runtime.cancel_calls == []
        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-a",
            "role": "admin",
        }
        cancelled = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/cancel"
        )

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert blocking_runtime.cancel_calls == [
        ("user-a", task_id, 1)
    ]


def test_pi_start_exposes_understanding_before_data_processing(
    tmp_path,
    monkeypatch,
) -> None:
    """Runtime 启动是技术事实，不能被普通用户误读为已经开始处理数据。"""

    blocking_runtime = PreFreezeInspectingPiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=blocking_runtime,
    )
    document, _ = _uploads(tmp_path)

    with client:
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "读取附件中的第一张报销单并输出 JSON",
                "upload_ids": [document],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
            },
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        assert blocking_runtime.started.wait(timeout=5)
        task = _wait_for_status(client, task_id, "running")
        try:
            assert task["progress"]["active_stage"] == "understand"
            stages = {
                stage["stage"]: stage["status"]
                for stage in task["progress"]["stages"]
            }
            assert stages["understand"] == "active"
            assert stages["execute"] == "pending"
            summaries = " ".join(
                stage["summary"] for stage in task["progress"]["stages"]
            )
            assert "inspect_source" not in summaries
            assert "已完成来源结构检查" in summaries
        finally:
            cancelled = client.post(
                f"/api/semantic-workspace/tasks/{task_id}/cancel"
            )
            assert cancelled.status_code == 200, cancelled.text


def test_create_pi_task_is_idempotent_for_same_http_key(
    tmp_path,
    monkeypatch,
) -> None:
    fake_runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=fake_runtime,
    )
    document, _ = _uploads(tmp_path)
    payload = {
        "objective_text": "读取附件并输出一个 CSV",
        "upload_ids": [document],
        "output_formats": ["csv"],
        "runtime_version": "pi",
        "permission_profile": "standard",
        "provider": "local",
    }
    headers = {"Idempotency-Key": "pi-create-user-action-001"}

    with client:
        first = client.post(
            "/api/semantic-workspace/tasks",
            json=payload,
            headers=headers,
        )
        second = client.post(
            "/api/semantic-workspace/tasks",
            json=payload,
            headers=headers,
        )
        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        _wait_for_delivery(client, first.json()["task_id"])

        conflict = client.post(
            "/api/semantic-workspace/tasks",
            json={
                **payload,
                "objective_text": "读取附件并输出一个 TXT",
                "output_formats": ["txt"],
            },
            headers=headers,
        )

    assert first.json()["task_id"] == second.json()["task_id"]
    assert fake_runtime.start_calls == 1
    assert conflict.status_code == 409
    assert "幂等键" in conflict.json()["detail"]


def test_admin_can_freeze_visible_capability_when_creating_pi_task(
    tmp_path,
    monkeypatch,
) -> None:
    """管理员选择必须通过 API 冻结到 revision，不能只停留在前端状态。"""

    from src.capability_catalog import SqliteCapabilityCatalogRepository
    from src.conversation_steering import (
        CapabilityMaturity,
        CapabilityPack,
        ProcedureScope,
    )

    monkeypatch.setattr(settings, "pi_capability_host_enabled", True)
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=FakePiRuntime(),
    )
    digest = "sha256:" + "a" * 64
    repository = SqliteCapabilityCatalogRepository(settings.webui_db_path)
    repository.save_pack(
        CapabilityPack(
            pack_id="gray-python-table",
            version="1.0.0",
            digest=digest,
            scope=ProcedureScope.PLATFORM,
            maturity=CapabilityMaturity.VERIFIED,
            manifest=(
                ("display_name", "Python 表格处理"),
                ("purpose", "按任务要求处理表格数据"),
                ("kind", "tool"),
            ),
        )
    )
    _, table = _uploads(tmp_path)

    with client:
        listed = client.get("/api/semantic-workspace/capabilities")
        assert listed.status_code == 200, listed.text
        assert listed.json() == {
            "enabled": True,
            "items": [
                {
                    "pack_id": "gray-python-table",
                    "version": "1.0.0",
                    "digest": digest,
                    "name": "Python 表格处理",
                    "kind": "tool",
                    "purpose": "按任务要求处理表格数据",
                    "scope": "platform",
                    "recommended": False,
                }
            ],
        }
        created = client.post(
            "/api/semantic-workspace/tasks",
            json={
                "objective_text": "按部门汇总金额并输出 JSON",
                "upload_ids": [table],
                "output_formats": ["json"],
                "runtime_version": "pi",
                "permission_profile": "standard",
                "provider": "local",
                "capability_pack_refs": [
                    {
                        "pack_id": "gray-python-table",
                        "version": "1.0.0",
                        "digest": digest,
                    }
                ],
            },
        )
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        _wait_for_delivery(client, task_id)
        selected = client.get(
            f"/api/semantic-workspace/tasks/{task_id}/capabilities?revision=1"
        )
        revised = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/revisions",
            json={
                "instruction": "字段名称改成中文",
                "expected_active_revision": 1,
            },
        )
        assert revised.status_code == 202, revised.text
        _wait_for_delivery(client, task_id)
        inherited = client.get(
            f"/api/semantic-workspace/tasks/{task_id}/capabilities?revision=2"
        )

    assert selected.status_code == 200, selected.text
    assert selected.json() == {
        "task_id": task_id,
        "revision": 1,
        "items": [
            {
                "name": "Python 表格处理",
                    "kind": "tool",
                "version": "1.0.0",
                "purpose": "按任务要求处理表格数据",
            }
        ],
    }
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()["items"] == selected.json()["items"]


def test_capability_gray_selection_is_admin_only_and_fails_closed(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "pi_capability_host_enabled", True)
    client = _client(tmp_path, monkeypatch, role="user")
    with client:
        response = client.get("/api/semantic-workspace/capabilities")
    assert response.status_code == 403

    monkeypatch.setattr(settings, "pi_capability_host_enabled", False)
    admin = _client(tmp_path / "disabled", monkeypatch, role="admin")
    with admin:
        disabled = admin.get("/api/semantic-workspace/capabilities")
    assert disabled.status_code == 409
    assert disabled.json()["detail"] == "任务级能力 Sidecar 灰度尚未启用"
