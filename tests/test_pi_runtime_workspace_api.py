# -*- coding: utf-8 -*-
"""Pi 全能力灰度入口的工作台纵切面测试。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import src.api.auth as auth_mod
import src.api.semantic_workspace_runtime as runtime_mod
import src.model_connections.broker as broker_mod
from src.agentic_runtime.models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeRequest,
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
from src.api.store import WebUIStore
from src.config.settings import settings
from src.candidate_verification import (
    AttemptStatus,
    CandidateVerificationService,
    ReverificationBlocker,
    SqliteCandidateVerificationRepository,
    VerifierRulesetBinding,
    migrate_candidate_verification,
)
from src.delivery_publishing.repository import DeliveryPublishingRepository
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


class _FixedCandidateRulesetResolver:
    def resolve(self, _verifier) -> VerifierRulesetBinding:
        ruleset_hash = "5" * 64
        return VerifierRulesetBinding(
            verifier_ruleset_hash=ruleset_hash,
            verifier_code_commit="6" * 40,
            verifier_source_hash="7" * 64,
            verifier_execution_identity_hash="8" * 64,
            verifier_ruleset_manifest_json=json.dumps(
                {
                    "schema_version": 1,
                    "verifier_ruleset_hash": ruleset_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def resolve_target(self) -> VerifierRulesetBinding:
        return self.resolve(None)


class _BlockSecondRulesetResolver(_FixedCandidateRulesetResolver):
    def __init__(self) -> None:
        self.calls = 0
        self.second_started = threading.Event()
        self.release = threading.Event()

    def resolve(self, verifier) -> VerifierRulesetBinding:
        self.calls += 1
        # Offer、requested 冻结后，第三次才是 Worker 启动前的执行身份复核。
        if self.calls == 3:
            self.second_started.set()
            self.release.wait(timeout=5)
        return super().resolve(verifier)


class _AllowCandidateVerifierAdapter:
    def assert_verifier_binding(self, _request, _run_id, _verifier) -> None:
        return None


class _StaticReportVerifier:
    def __init__(self, report: VerificationReport) -> None:
        self._report = report

    async def verify(self, **_kwargs) -> VerificationReport:
        return self._report


class FakePiRuntime:
    """不启动 Docker，只验证工作台与 Runtime Seam 的状态契约。"""

    def __init__(self) -> None:
        self.start_calls = 0
        self.requests: list[object] = []
        self.resume_calls: list[object] = []
        self._candidate_verification = None

    def bind_candidate_verification(self, service) -> None:
        self._candidate_verification = service

    async def start(self, request, *, on_event):
        self.start_calls += 1
        self.requests.append(request)
        return await self._complete(request, on_event=on_event)

    async def resume(self, request, *, checkpoint, on_event):
        self.requests.append(request)
        self.resume_calls.append(checkpoint)
        return await self._complete(
            request,
            on_event=on_event,
            run_id=checkpoint.run_id,
        )

    async def _complete(self, request, *, on_event, run_id=None):
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
            / f"r{request.revision}"
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
        source = request.sources[0]
        (output / "candidate-manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "artifacts": [
                        {
                            "filename": candidate.name,
                            "format": requested_format,
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
        result = PiRuntimeResult(
            status=RuntimeStatus.CANDIDATE_READY,
            run_id=run_id or f"pi_run_test_r{request.revision}",
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
            verification=self._verification_report(),
        )
        if self._candidate_verification is None:
            raise RuntimeError("测试 Runtime 未绑定 CandidateVerification Module")
        attempt = await self._candidate_verification.verify_initial_current(
            request=request,
            run_id=result.run_id,
            candidates=result.candidates,
            manifest_path=output / "candidate-manifest.json",
            verifier=_StaticReportVerifier(result.verification),
            actor_id=request.user_id,
        )
        assert attempt.report_json is not None
        return result.model_copy(
            update={
                "verification": VerificationReport.model_validate_json(
                    attempt.report_json
                )
            }
        )

    def _verification_report(self) -> VerificationReport:
        return VerificationReport(
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

    def _verification_report(self) -> VerificationReport:
        return VerificationReport(
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


class PassingRetryJudge:
    async def judge(self, **_kwargs):
        return SemanticDecision(
            passed=True,
            contains_unrequested_content=False,
            reason="候选满足目标且没有额外内容",
        )


class BlockingFullVerifier:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    async def verify(self, **_kwargs) -> VerificationReport:
        self.started.set()
        await asyncio.to_thread(self.release.wait)
        return FakePiRuntime()._verification_report()


class CountingFullVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, **_kwargs) -> VerificationReport:
        self.calls += 1
        return FakePiRuntime()._verification_report()


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

    def bind_candidate_verification(self, _service) -> None:
        return None

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

    def bind_candidate_verification(self, _service) -> None:
        return None

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
    routing_mode: RolloutMode | None = RolloutMode.ADMIN_GRAY,
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
    auth_mod.get_store()
    database = Path(settings.webui_db_path)
    if routing_mode is not None:
        migrate_runtime_routing(
            database,
            tmp_path / "workspace-before-runtime-routing.db",
        )


        routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
        passed = _g3_snapshot(passed=True)
        admin_actor = RolloutActor(actor_id="admin-a", role="admin")
        routing.record_gate(passed, admin_actor)
        gray_approval = RolloutApproval(
            approval_id="approval-test-admin-gray",
            target_mode=RolloutMode.ADMIN_GRAY,
            gate_snapshot_id=passed.snapshot_id,
            approved_by="maintainer-a",
        )
        routing.record_approval(
            gray_approval,
            RolloutActor(actor_id="maintainer-a", role="user"),
        )
        routing.change_mode(
            RolloutMode.ADMIN_GRAY,
            gray_approval,
            admin_actor,
        )
        if routing_mode is RolloutMode.VNEXT_DEFAULT:
            default_approval = RolloutApproval(
                approval_id="approval-test-vnext-default",
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
                admin_actor,
            )
    migrate_candidate_verification(
        database,
        tmp_path / "workspace-before-candidate-verification.db",
    )
    candidate_verification = CandidateVerificationService(
        repository=SqliteCandidateVerificationRepository(database),
        ruleset_resolver=_FixedCandidateRulesetResolver(),
        p0_reader=lambda _request: False,
        broker_adapter=_AllowCandidateVerifierAdapter(),
        event_writer=lambda _event_type, _attempt: None,
        reverification_authority=runtime_mod._WorkspaceReverificationAuthority(),
    )
    manager = SemanticWorkspaceManager(
        pi_runtime=pi_runtime or FakePiRuntime(),
        candidate_verification=candidate_verification,
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


def test_runtime_version_contract_is_optional_without_legacy_default(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        routing_mode=None,
    )

    schema = client.get("/openapi.json").json()
    runtime_schema = schema["components"]["schemas"]["WorkspaceTaskCreateIn"][
        "properties"
    ]["runtime_version"]

    assert "runtime_version" not in schema["components"]["schemas"][
        "WorkspaceTaskCreateIn"
    ].get("required", [])
    assert "default" not in runtime_schema
    assert "anyOf" not in runtime_schema

    request = {
        "objective_text": "验证 Runtime 输入契约",
        "upload_ids": ["upload-not-used"],
        "output_formats": ["json"],
    }
    for invalid_runtime in (None, "", "unknown"):
        rejected = client.post(
            "/api/semantic-workspace/tasks",
            json={**request, "runtime_version": invalid_runtime},
        )
        assert rejected.status_code == 422, rejected.text


def test_missing_routing_schema_fails_closed_for_explicit_pi(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "pi_runtime_enabled", True)
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        routing_mode=None,
    )
    document, _ = _uploads(tmp_path)
    request = {
        "objective_text": "提取附件内容并输出 JSON",
        "upload_ids": [document],
        "output_formats": ["json"],
        "provider": "local",
    }

    with client:
        explicit_pi = client.post(
            "/api/semantic-workspace/tasks",
            json={**request, "runtime_version": "pi"},
        )
        assert explicit_pi.status_code == 503, explicit_pi.text

        tasks_after_rejection = client.get("/api/semantic-workspace/tasks")
        assert tasks_after_rejection.status_code == 200
        assert tasks_after_rejection.json() == []

        platform_default = client.post(
            "/api/semantic-workspace/tasks",
            json=request,
        )
        assert platform_default.status_code == 202, platform_default.text
        assert platform_default.json()["runtime_version"] == "legacy"
        cancelled = client.post(
            f"/api/semantic-workspace/tasks/{platform_default.json()['task_id']}/cancel"
        )
        assert cancelled.status_code in {200, 409}, cancelled.text


def test_runtime_rollout_only_changes_new_tasks_and_p0_restores_legacy(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "pi_runtime_enabled", True)
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        routing_mode=None,
    )
    document, _ = _uploads(tmp_path)
    # 先让现有 Store 建完历史表，再在同一测试库执行显式 G3 迁移。
    auth_mod.get_store()
    database = Path(settings.webui_db_path)
    migrate_runtime_routing(database, tmp_path / "workspace-before-g3.db")
    routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
    passed = _g3_snapshot(passed=True)
    actor = RolloutActor(actor_id="admin-a", role="admin")
    routing.record_gate(passed, actor)
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
        vnext = client.post(
            "/api/semantic-workspace/tasks",
            json=request,
            headers={"Idempotency-Key": "runtime-intent-distinction"},
        )
        assert vnext.status_code == 202, vnext.text
        assert vnext.json()["runtime_version"] == "pi"

        changed_intent = client.post(
            "/api/semantic-workspace/tasks",
            json={**request, "runtime_version": "legacy"},
            headers={"Idempotency-Key": "runtime-intent-distinction"},
        )
        assert changed_intent.status_code == 409, changed_intent.text

        explicit_legacy = client.post(
            "/api/semantic-workspace/tasks",
            json={**request, "runtime_version": "legacy"},
        )
        assert explicit_legacy.status_code == 202, explicit_legacy.text
        assert explicit_legacy.json()["runtime_version"] == "legacy"

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
            json={
                **request,
                "provider": "deepseek",
                "model": "deepseek-chat",
                "model_connection_id": "conn-must-not-freeze",
                "model_connection_model": "model-must-not-freeze",
                "external_api_confirmed": True,
                "capability_pack_refs": [{
                    "pack_id": "pack-must-not-freeze",
                    "version": "1.0.0",
                    "digest": "sha256:" + "d" * 64,
                }],
            },
            headers={"Idempotency-Key": "g3-p0-retry"},
        )
        assert legacy.status_code == 202, legacy.text
        assert legacy.json()["runtime_version"] == "legacy"
        assert legacy.json()["model_connection_id"] is None
        assert legacy.json()["provider"] == "local"
        assert legacy.json()["model"] is None
        frozen_legacy = AgenticRuntimeRepository(database).get(
            "user-a",
            legacy.json()["task_id"],
            1,
        )
        assert frozen_legacy is not None
        assert frozen_legacy["model_connection_id"] is None
        assert frozen_legacy["external_api_confirmed"] is False

        unchanged = client.get(
            f"/api/semantic-workspace/tasks/{vnext.json()['task_id']}"
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["runtime_version"] == "pi"
        for task_id in (
            vnext.json()["task_id"],
            explicit_legacy.json()["task_id"],
            legacy.json()["task_id"],
        ):
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
        routing_mode=RolloutMode.VNEXT_DEFAULT,
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
    client = _client(
        tmp_path,
        monkeypatch,
        role="user",
        routing_mode=RolloutMode.VNEXT_DEFAULT,
    )
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


def test_user_can_use_platform_default_with_own_verified_connection(
    tmp_path,
    monkeypatch,
) -> None:
    fake_runtime = FakePiRuntime()
    client = _client(
        tmp_path,
        monkeypatch,
        role="user",
        pi_runtime=fake_runtime,
        routing_mode=RolloutMode.VNEXT_DEFAULT,
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
        provider_runtime = AgenticRuntimeRepository(
            settings.webui_db_path
        ).get("user-a", created.json()["task_id"], 2)
        assert provider_runtime is not None

        def logical_fingerprint() -> str:
            with sqlite3.connect(settings.webui_db_path) as connection:
                dump = "\n".join(connection.iterdump())
            return hashlib.sha256(dump.encode("utf-8")).hexdigest()

        before_offer = logical_fingerprint()
        monkeypatch.setattr(broker_mod, "_default_broker", None)
        authority_blockers = runtime_mod._WorkspaceReverificationAuthority().blockers(
            fake_runtime.requests[-1],
            provider_runtime["run_id"],
        )
        assert ReverificationBlocker.PROVIDER_BINDING_UNAVAILABLE not in (
            authority_blockers
        )
        assert logical_fingerprint() == before_offer
        assert broker_mod._default_broker is None
        monkeypatch.setattr(broker_mod, "_default_broker", broker)
        assert broker.delete_connection(
            str(connection["connection_id"]),
            "user-a",
            can_manage=False,
        ) is True
        unavailable = client.get(
            f"/api/semantic-workspace/tasks/{created.json()['task_id']}"
        )
        assert unavailable.status_code == 200
        unavailable_offer = unavailable.json()["agentic_runtime"][
            "reverification_offer"
        ]
        assert "provider_binding_unavailable" in unavailable_offer["blockers"]
        assert unavailable_offer["eligible"] is False

    assert task["agentic_runtime"]["model_connection_id"] == connection[
        "connection_id"
    ]
    assert task["agentic_runtime"]["reverification_offer"][
        "requires_provider"
    ] is True
    assert "provider_binding_unavailable" not in task["agentic_runtime"][
        "reverification_offer"
    ]["blockers"]
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
        assert candidate["agentic_runtime"]["latest_verification_attempt"][
            "status"
        ] == "inconclusive"
        offer = candidate["agentic_runtime"]["reverification_offer"]
        assert offer["eligible"] is True
        assert offer["reason"] == "semantic_inconclusive"
        assert offer["blockers"] == []
        assert offer["ruleset_changed"] is False
        assert offer["requires_provider"] is False
        assert offer["model_id"]
        assert offer["egress_categories"] == []
        assert offer["egress_summary"] == "本次不外发"
        assert candidate["agentic_runtime"]["awaiting_publication"] is False
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
        runtime_state = AgenticRuntimeRepository(
            settings.webui_db_path
        ).get("user-a", task_id, 1)
        assert runtime_state is not None
        authority_request = dict(runtime_state["request"])
        authority_request["api_key"] = "local-runtime"
        frozen_request = PiRuntimeRequest.model_validate(authority_request)
        authority = runtime_mod._WorkspaceReverificationAuthority()
        assert authority.blockers(
            frozen_request,
            runtime_state["run_id"],
        ) == ()
        assert ReverificationBlocker.TASK_REVISION_DRIFT in authority.blockers(
            frozen_request.model_copy(update={"objective_text": "漂移目标"}),
            runtime_state["run_id"],
        )
        changed_source = frozen_request.sources[0].model_copy(
            update={"upload_id": "upload-drift"}
        )
        assert ReverificationBlocker.SOURCE_BINDING_DRIFT in authority.blockers(
            frozen_request.model_copy(update={"sources": (changed_source,)}),
            runtime_state["run_id"],
        )
        orphan_database = tmp_path / "workspace-without-assignment.db"
        with sqlite3.connect(settings.webui_db_path) as source_connection:
            with sqlite3.connect(orphan_database) as target_connection:
                source_connection.backup(target_connection)
        with sqlite3.connect(orphan_database) as connection:
            connection.execute("DROP TRIGGER runtime_assignments_no_update")
            connection.execute("DROP TRIGGER runtime_assignments_no_delete")
            row = connection.execute(
                "SELECT payload_json FROM runtime_assignments WHERE owner_id=? "
                "AND task_id=? AND revision=1",
                ("user-a", task_id),
            ).fetchone()
            assert row is not None
            malformed = json.loads(row[0])
            malformed.pop("assigned_by")
            connection.execute(
                "UPDATE runtime_assignments SET payload_json=? WHERE owner_id=? "
                "AND task_id=? AND revision=1",
                (json.dumps(malformed), "user-a", task_id),
            )
        live_database = settings.webui_db_path
        monkeypatch.setattr(settings, "webui_db_path", str(orphan_database))
        assert (
            ReverificationBlocker.RUNTIME_ASSIGNMENT_DRIFT
            in runtime_mod._WorkspaceReverificationAuthority().blockers(
                frozen_request,
                runtime_state["run_id"],
            )
        )
        monkeypatch.setattr(settings, "webui_db_path", live_database)
        with sqlite3.connect(orphan_database) as connection:
            connection.execute(
                "DELETE FROM runtime_assignments WHERE owner_id=? "
                "AND task_id=? AND revision=1",
                ("user-a", task_id),
            )
        monkeypatch.setattr(settings, "webui_db_path", str(orphan_database))
        assert (
            ReverificationBlocker.RUNTIME_ASSIGNMENT_DRIFT
            in runtime_mod._WorkspaceReverificationAuthority().blockers(
                frozen_request,
                runtime_state["run_id"],
            )
        )
        monkeypatch.setattr(settings, "webui_db_path", live_database)
        attempt_repository = SqliteCandidateVerificationRepository(
            settings.webui_db_path
        )
        initial_attempts = attempt_repository.list_for_candidate(
            "user-a",
            task_id=task_id,
            revision=1,
            run_id=runtime_state["run_id"],
            candidate_set_hash=runtime_state["verified_candidate_set_hash"],
        )
        assert [item.status for item in initial_attempts] == [
            AttemptStatus.INCONCLUSIVE
        ]

        retried = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verification/retry"
        )

        assert retried.status_code == 200, retried.text
        payload = retried.json()
        assert payload["status"] == "completed"
        assert payload["delivery"]["status"] == "succeeded"
        assert payload["agentic_runtime"]["verification"]["status"] == "passed"
        assert runtime.start_calls == 1
        attempts = attempt_repository.list_for_candidate(
            "user-a",
            task_id=task_id,
            revision=1,
            run_id=runtime_state["run_id"],
            candidate_set_hash=runtime_state["verified_candidate_set_hash"],
        )
        assert [item.status for item in attempts] == [
            AttemptStatus.INCONCLUSIVE,
            AttemptStatus.PASSED,
        ]


def test_candidate_offer_cross_owner_returns_404_without_content(
    tmp_path,
    monkeypatch,
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=InconclusivePiRuntime(),
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
        owner_payload = _wait_for_status(client, task_id, "candidate_ready")
        candidate_filename = owner_payload["agentic_runtime"]["candidates"][0][
            "filename"
        ]
        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-b",
            "role": "user",
        }

        rejected = client.get(f"/api/semantic-workspace/tasks/{task_id}")

        assert rejected.status_code == 404
        assert task_id not in rejected.text
    assert candidate_filename not in rejected.text


def test_full_candidate_reverification_returns_202_without_rerunning_pi_or_publishing(
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
    monkeypatch.setattr(
        SemanticWorkspaceManager,
        "_build_full_candidate_verifier",
        lambda *_args, **_kwargs: _StaticReportVerifier(
            FakePiRuntime()._verification_report()
        ),
        raising=False,
    )
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    runtime_mod._manager._candidate_verification._event_writer = (
        runtime_mod._write_candidate_verification_event
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
        before = _wait_for_status(client, task_id, "candidate_ready")
        previous_attempt_id = before["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        runtime_state = AgenticRuntimeRepository(settings.webui_db_path).get(
            "user-a",
            task_id,
            1,
        )
        assert runtime_state is not None
        candidate_path = runtime_state["candidates"][0].host_path
        candidate_before = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        workspace_root = Path(runtime_state["workspace_root"])
        tree_before = {
            path.relative_to(workspace_root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in workspace_root.rglob("*")
            if path.is_file()
        }
        events_before = len(
            auth_mod.get_store().list_semantic_workspace_events(
                "user-a",
                task_id,
            )
        )
        with sqlite3.connect(settings.webui_db_path) as connection:
            provider_tables = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND (name LIKE '%grant%' OR name LIKE '%usage%')"
                ).fetchall()
            )
            provider_counts_before = {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in provider_tables
            }

        response = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
            headers={"Idempotency-Key": "reverify-api-1"},
            json={
                "expected_revision": 1,
                "expected_previous_attempt_id": previous_attempt_id,
                "external_api_confirmed": False,
            },
        )

        assert response.status_code == 202, response.text
        receipt = response.json()
        assert receipt["previous_attempt_id"] == previous_attempt_id
        assert receipt["status"] in {"requested", "running", "passed"}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = client.get(
                f"/api/semantic-workspace/tasks/{task_id}"
            ).json()
            latest = current["agentic_runtime"]["latest_verification_attempt"]
            if latest["attempt_id"] == receipt["attempt_id"] and latest[
                "status"
            ] == "passed":
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"完整候选重验未进入 passed 终态：{latest}")

        assert current["agentic_runtime"]["awaiting_publication"] is True
        assert current["delivery"] is None
        assert runtime.start_calls == 1
        assert runtime.resume_calls == []
        assert hashlib.sha256(candidate_path.read_bytes()).hexdigest() == (
            candidate_before
        )
        assert {
            path.relative_to(workspace_root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in workspace_root.rglob("*")
            if path.is_file()
        } == tree_before
        new_events = auth_mod.get_store().list_semantic_workspace_events(
            "user-a",
            task_id,
        )[events_before:]
        attempt_events = [
            event
            for event in new_events
            if event["details"].get("attempt_id") == receipt["attempt_id"]
        ]
        assert [event["event_type"] for event in attempt_events] == [
            "candidate_verification_attempt_requested",
            "candidate_verification_attempt_started",
            "candidate_verification_attempt_finished",
        ]
        assert all(
            "capability" not in event["event_type"]
            and "dependency" not in event["event_type"]
            for event in new_events
        )
        with sqlite3.connect(settings.webui_db_path) as connection:
            assert {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in provider_tables
            } == provider_counts_before
        attempts = SqliteCandidateVerificationRepository(
            settings.webui_db_path
        ).list_for_candidate(
            "user-a",
            task_id=task_id,
            revision=1,
            run_id=runtime_state["run_id"],
            candidate_set_hash=runtime_state["verified_candidate_set_hash"],
        )
        assert [item.attempt_id for item in attempts] == [
            previous_attempt_id,
            receipt["attempt_id"],
        ]

        stores = [WebUIStore(settings.webui_db_path) for _ in range(4)]

        def append_concurrently(index: int, event_id: str) -> dict[str, object]:
            return stores[index].append_semantic_workspace_event(
                "user-a",
                task_id,
                event_id=event_id,
                stage="verify",
                event_type=f"atomic_event_{event_id}",
                summary="并发事件原子性测试",
            )

        duplicate_id = "workspace_event_cv_atomic_duplicate"
        with ThreadPoolExecutor(max_workers=4) as executor:
            duplicate_results = tuple(
                executor.map(
                    lambda index: append_concurrently(index, duplicate_id),
                    range(4),
                )
            )
        assert {item["event_id"] for item in duplicate_results} == {duplicate_id}

        distinct_ids = tuple(
            f"workspace_event_cv_atomic_distinct_{index}" for index in range(4)
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            tuple(
                executor.map(
                    lambda pair: append_concurrently(*pair),
                    enumerate(distinct_ids),
                )
            )
        atomic_events = auth_mod.get_store().list_semantic_workspace_events(
            "user-a",
            task_id,
        )
        assert sum(event["event_id"] == duplicate_id for event in atomic_events) == 1
        assert {
            event["event_id"]
            for event in atomic_events
            if event["event_id"] in distinct_ids
        } == set(distinct_ids)


def test_passed_candidate_reverification_requires_explicit_idempotent_publish(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = InconclusivePiRuntime()
    monkeypatch.setattr(
        SemanticWorkspaceManager,
        "_build_full_candidate_verifier",
        lambda *_args, **_kwargs: _StaticReportVerifier(
            FakePiRuntime()._verification_report()
        ),
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
        previous_attempt_id = candidate["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        requested = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
            headers={"Idempotency-Key": "reverify-before-publish"},
            json={
                "expected_revision": 1,
                "expected_previous_attempt_id": previous_attempt_id,
                "external_api_confirmed": False,
            },
        )
        assert requested.status_code == 202, requested.text
        attempt_id = requested.json()["attempt_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = client.get(
                f"/api/semantic-workspace/tasks/{task_id}"
            ).json()
            latest = current["agentic_runtime"]["latest_verification_attempt"]
            if latest["attempt_id"] == attempt_id and latest["status"] == "passed":
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"完整候选重验未进入 passed 终态：{latest}")
        assert current["delivery"] is None
        assert current["agentic_runtime"]["awaiting_publication"] is True
        runtime_state = AgenticRuntimeRepository(settings.webui_db_path).get(
            "user-a",
            task_id,
            1,
        )
        assert runtime_state is not None
        candidate_path = runtime_state["candidates"][0].host_path
        candidate_before = candidate_path.read_bytes()
        attempt_repository = SqliteCandidateVerificationRepository(
            settings.webui_db_path
        )
        attempt_before = attempt_repository.get("user-a", attempt_id)
        assert attempt_before is not None

        publish_path = (
            f"/api/semantic-workspace/tasks/{task_id}/"
            f"candidate-verifications/{attempt_id}/publish"
        )
        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-b",
            "role": "user",
        }
        cross_owner = client.post(
            publish_path,
            headers={"Idempotency-Key": "publish-cross-owner"},
            json={"expected_revision": 1},
        )
        assert cross_owner.status_code == 404, cross_owner.text
        assert attempt_id not in cross_owner.text
        client.app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-a",
            "role": "admin",
        }
        stale = client.post(
            publish_path,
            headers={"Idempotency-Key": "publish-stale-revision"},
            json={"expected_revision": 2},
        )
        assert stale.status_code == 409, stale.text
        def publish_same_request(_index: int):
            return client.post(
                publish_path,
                headers={"Idempotency-Key": "publish-exact-attempt"},
                json={"expected_revision": 1},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, replay = tuple(
                executor.map(publish_same_request, range(2))
            )
        assert first.status_code == 200, first.text
        assert first.json()["provenance"]["verification_attempt_id"] == attempt_id
        assert replay.status_code == 200, replay.text
        assert replay.json()["delivery_id"] == first.json()["delivery_id"]
        conflict = client.post(
            publish_path,
            headers={"Idempotency-Key": "publish-other-request"},
            json={"expected_revision": 1},
        )
        assert conflict.status_code == 409, conflict.text
        assert candidate_path.read_bytes() == candidate_before
        assert attempt_repository.get("user-a", attempt_id) == attempt_before
        with sqlite3.connect(settings.webui_db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM formal_delivery_runs WHERE owner_id=?",
                ("user-a",),
            ).fetchone()[0] == 1

        original_claim_intent = DeliveryPublishingRepository.claim_intent

        def fail_database(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(
            DeliveryPublishingRepository,
            "claim_intent",
            fail_database,
        )
        unavailable = client.post(
            publish_path,
            headers={"Idempotency-Key": "publish-exact-attempt"},
            json={"expected_revision": 1},
        )
        assert unavailable.status_code == 503, unavailable.text
        monkeypatch.setattr(
            DeliveryPublishingRepository,
            "claim_intent",
            original_claim_intent,
        )

        store = auth_mod.get_store()
        store.create_semantic_workspace_revision(
            "user-a",
            task_id,
            objective_text="新版本不得被旧发布覆盖",
            output_formats=["json"],
            change_summary="验证发布后任务状态 CAS",
            expected_revision=2,
        )
        with pytest.raises(RuntimeError, match="活动版本已变化"):
            store.update_semantic_workspace_task(
                "user-a",
                task_id,
                expected_active_revision=1,
                status="completed",
            )
        after_revision = store.get_semantic_workspace_task("user-a", task_id)
        assert after_revision is not None
        assert after_revision["active_revision"] == 2
        assert after_revision["status"] == "queued"


def test_full_candidate_reverification_is_idempotent_and_rejects_concurrent_key(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = InconclusivePiRuntime()
    verifier = BlockingFullVerifier()
    monkeypatch.setattr(
        SemanticWorkspaceManager,
        "_build_full_candidate_verifier",
        lambda *_args, **_kwargs: verifier,
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
        task = _wait_for_status(client, task_id, "candidate_ready")
        previous_attempt_id = task["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        payload = {
            "expected_revision": 1,
            "expected_previous_attempt_id": previous_attempt_id,
            "external_api_confirmed": False,
        }
        url = (
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications"
        )

        first = client.post(
            url,
            headers={"Idempotency-Key": "reverify-idempotent"},
            json=payload,
        )
        assert first.status_code == 202, first.text
        assert verifier.started.wait(timeout=5)
        try:
            same = client.post(
                url,
                headers={"Idempotency-Key": "reverify-idempotent"},
                json=payload,
            )
            conflict = client.post(
                url,
                headers={"Idempotency-Key": "reverify-concurrent"},
                json=payload,
            )

            assert same.status_code == 202, same.text
            assert same.json()["attempt_id"] == first.json()["attempt_id"]
            assert conflict.status_code == 409
        finally:
            verifier.release.set()


def test_full_candidate_reverification_closes_requested_when_p0_flips(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = InconclusivePiRuntime()
    verifier = CountingFullVerifier()
    resolver = _BlockSecondRulesetResolver()
    monkeypatch.setattr(
        SemanticWorkspaceManager,
        "_build_full_candidate_verifier",
        lambda *_args, **_kwargs: verifier,
        raising=False,
    )
    client = _client(
        tmp_path,
        monkeypatch,
        role="admin",
        pi_runtime=runtime,
    )
    manager = runtime_mod._manager
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
        task = _wait_for_status(client, task_id, "candidate_ready")
        previous_attempt_id = task["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        manager._candidate_verification._ruleset_resolver = resolver
        response = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
            headers={"Idempotency-Key": "reverify-p0-flip"},
            json={
                "expected_revision": 1,
                "expected_previous_attempt_id": previous_attempt_id,
                "external_api_confirmed": False,
            },
        )
        assert response.status_code == 202, response.text
        assert resolver.second_started.wait(timeout=5)
        try:
            with sqlite3.connect(settings.webui_db_path) as connection:
                connection.execute(
                    "UPDATE runtime_rollout_state SET p0_blocked=1 "
                    "WHERE state_id=1"
                )
            resolver.release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                latest = client.get(
                    f"/api/semantic-workspace/tasks/{task_id}"
                ).json()["agentic_runtime"]["latest_verification_attempt"]
                if latest["attempt_id"] == response.json()["attempt_id"] and latest[
                    "status"
                ] == "cancelled":
                    break
                time.sleep(0.02)
            else:
                raise AssertionError(f"P0 翻转后 Attempt 未安全收口：{latest}")
            assert verifier.calls == 0
        finally:
            resolver.release.set()


def test_full_candidate_reverification_closes_running_after_hard_restart(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_mod,
        "REVERIFICATION_RECOVERY_POLL_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        runtime_mod,
        "WORKSPACE_PURGE_INTERVAL_SECONDS",
        0.02,
    )
    runtime = InconclusivePiRuntime()
    monkeypatch.setattr(
        SemanticWorkspaceManager,
        "_build_full_candidate_verifier",
        lambda *_args, **_kwargs: _StaticReportVerifier(
            FakePiRuntime()._verification_report()
        ),
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
        task = _wait_for_status(client, task_id, "candidate_ready")
        previous_attempt_id = task["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        service = runtime_mod._manager._candidate_verification
        requested = asyncio.run(
            service.request_reverification(
                owner_id="user-a",
                task_id=task_id,
                revision=1,
                expected_previous_attempt_id=previous_attempt_id,
                external_api_confirmed=False,
                idempotency_key="reverify-crash-window",
                verifier_factory=lambda *_args: _StaticReportVerifier(
                    FakePiRuntime()._verification_report()
                ),
            )
        )
        assert requested.status is AttemptStatus.REQUESTED
        active_lease = SemanticWorkspaceManager._candidate_reverification_lease(
            requested.attempt_id
        )
        active_lease.acquire(timeout=0)
        with sqlite3.connect(settings.webui_db_path) as connection:
            connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (
                    datetime.now(timezone.utc).isoformat(),
                    "user-a",
                    requested.attempt_id,
                ),
            )

    original_list_running = service.list_running_reverifications
    scan_calls = 0

    def fail_first_running_scan():
        nonlocal scan_calls
        scan_calls += 1
        if scan_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_list_running()

    monkeypatch.setattr(
        service,
        "list_running_reverifications",
        fail_first_running_scan,
    )

    async def recover() -> AttemptStatus:
        protected_manager = SemanticWorkspaceManager(
            pi_runtime=runtime,
            candidate_verification=service,
        )
        protected_manager.start()
        store = auth_mod.get_store()
        original_purge = store.purge_expired_semantic_workspace_tasks
        purge_calls = 0

        def fail_first_purge():
            nonlocal purge_calls
            purge_calls += 1
            if purge_calls == 1:
                raise sqlite3.OperationalError("database is locked")
            return original_purge()

        monkeypatch.setattr(
            store,
            "purge_expired_semantic_workspace_tasks",
            fail_first_purge,
        )
        try:
            await asyncio.sleep(0.05)
            protected = SqliteCandidateVerificationRepository(
                settings.webui_db_path
            ).get("user-a", requested.attempt_id)
            assert protected is not None
            assert protected.status is AttemptStatus.RUNNING
            active_lease.release()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                recovered = SqliteCandidateVerificationRepository(
                    settings.webui_db_path
                ).get("user-a", requested.attempt_id)
                assert recovered is not None
                if (
                    recovered.status is AttemptStatus.INCONCLUSIVE
                    and purge_calls >= 2
                ):
                    assert scan_calls >= 2
                    return recovered.status
                await asyncio.sleep(0.02)
            raise AssertionError("替代进程未持续接管已释放租约的 running Attempt")
        finally:
            await protected_manager.stop()
            if active_lease.is_locked:
                active_lease.release()

    assert asyncio.run(recover()) is AttemptStatus.INCONCLUSIVE


def test_full_candidate_reverification_reports_database_lock_as_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = InconclusivePiRuntime()
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
        task = _wait_for_status(client, task_id, "candidate_ready")
        previous_attempt_id = task["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        original_connect = SqliteCandidateVerificationRepository._connect

        def connect_without_wait(repository):
            connection = sqlite3.connect(repository._db_path, timeout=0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            return connection

        lock = sqlite3.connect(settings.webui_db_path)
        lock.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(
            SqliteCandidateVerificationRepository,
            "_connect",
            connect_without_wait,
        )
        try:
            response = client.post(
                f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
                headers={"Idempotency-Key": "reverify-lock-timeout"},
                json={
                    "expected_revision": 1,
                    "expected_previous_attempt_id": previous_attempt_id,
                    "external_api_confirmed": False,
                },
            )
        finally:
            lock.rollback()
            lock.close()
            monkeypatch.setattr(
                SqliteCandidateVerificationRepository,
                "_connect",
                original_connect,
            )

        assert response.status_code == 503
        assert response.json()["detail"] == "候选重验服务暂时不可用"


def test_full_candidate_reverification_rejects_stale_active_revision(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = InconclusivePiRuntime()
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
        task = _wait_for_status(client, task_id, "candidate_ready")
        previous_attempt_id = task["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        with sqlite3.connect(settings.webui_db_path) as connection:
            connection.execute(
                "UPDATE semantic_workspace_tasks SET active_revision=2 "
                "WHERE user_id='user-a' AND task_id=?",
                (task_id,),
            )

        response = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
            headers={"Idempotency-Key": "reverify-stale-revision"},
            json={
                "expected_revision": 1,
                "expected_previous_attempt_id": previous_attempt_id,
                "external_api_confirmed": False,
            },
        )

        assert response.status_code == 409


def test_full_candidate_reverification_reports_corrupt_frozen_context_as_422(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = InconclusivePiRuntime()
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
        task = _wait_for_status(client, task_id, "candidate_ready")
        previous_attempt_id = task["agentic_runtime"][
            "latest_verification_attempt"
        ]["attempt_id"]
        with sqlite3.connect(settings.webui_db_path) as connection:
            connection.execute(
                "UPDATE agentic_runtime_runs SET request_json='{' "
                "WHERE user_id='user-a' AND task_id=? AND revision=1",
                (task_id,),
            )

        response = client.post(
            f"/api/semantic-workspace/tasks/{task_id}/candidate-verifications",
            headers={"Idempotency-Key": "reverify-corrupt-context"},
            json={
                "expected_revision": 1,
                "expected_previous_attempt_id": previous_attempt_id,
                "external_api_confirmed": False,
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == (
            "候选缺少冻结运行信息，不能检查重验资格"
        )


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
