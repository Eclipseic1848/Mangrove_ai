# -*- coding: utf-8 -*-
"""AC-07 #35：供应链证据必须绑定精确 digest 并按硬门失败关闭。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from src.capability_catalog.integrity import write_capability_integrity
from src.capability_governance import (
    CapabilityGovernanceTarget,
    CapabilitySupplyChainEvidenceService,
    InMemoryCapabilityGovernanceRepository,
    LockedCliSupplyChainTools,
    SqliteCapabilityGovernanceRepository,
    SupplyChainCollection,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    migrate_capability_governance,
)
from src.conversation_steering import ProcedureScope


class FixedSupplyChainTools:
    """外部 CLI 边界夹具；已知值独立于治理策略实现。"""

    def collect(self, target, subject_root):
        return SupplyChainCollection(
            subject_digest=target.digest,
            trivy_version="0.70.0",
            trivy_config_sha256="1" * 64,
            trivy_result_sha256="2" * 64,
            trivy_database=TrivyDatabaseMetadata(
                version=2,
                updated_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
                next_update=datetime(2026, 8, 7, tzinfo=timezone.utc),
                downloaded_at=datetime(2026, 8, 6, 1, tzinfo=timezone.utc),
            ),
            secret_count=0,
            critical_count=1,
            fixable_high_count=0,
            misconfiguration_failure_count=0,
            syft_version="1.50.0",
            syft_json_sha256="3" * 64,
            cyclonedx_json_sha256="4" * 64,
            cyclonedx_spec_version="1.6",
        )


class MisconfiguredSupplyChainTools(FixedSupplyChainTools):
    def collect(self, target, subject_root):
        return super().collect(target, subject_root).model_copy(
            update={
                "critical_count": 0,
                "misconfiguration_failure_count": 1,
            }
        )


class CriticalMisconfigurationTools(FixedSupplyChainTools):
    def collect(self, target, subject_root):
        values = super().collect(target, subject_root).model_dump()
        values.update(
            critical_count=0,
            misconfiguration_failure_count=1,
            critical_misconfiguration_count=1,
        )
        return SupplyChainCollection(**values)


class FixableHighMisconfigurationTools(FixedSupplyChainTools):
    def collect(self, target, subject_root):
        values = super().collect(target, subject_root).model_dump()
        values.update(
            critical_count=0,
            misconfiguration_failure_count=1,
            fixable_high_misconfiguration_count=1,
        )
        return SupplyChainCollection(**values)


def _complete_trivy_report(
    subject: Path,
    *,
    report_id: str,
    results: list[dict] | None = None,
) -> dict:
    report = {
        "SchemaVersion": 2,
        "Trivy": {"Version": "0.70.0"},
        "ReportID": report_id,
        "CreatedAt": "2026-08-07T00:00:00Z",
        "ArtifactName": str(subject.resolve()),
        "ArtifactType": "filesystem",
    }
    if results is not None:
        report["Results"] = results
    return report


def _complete_syft_report(subject: Path, *, source_id: str) -> dict:
    return {
        "artifacts": [],
        "artifactRelationships": [],
        "source": {
            "id": source_id,
            "name": str(subject.resolve()),
            "type": "directory",
            "metadata": {"path": str(subject.resolve())},
        },
        "descriptor": {"name": "syft", "version": "1.50.0"},
        "schema": {
            "version": "16.1.10",
            "url": "https://raw.githubusercontent.com/anchore/syft/main/schema/json/schema-16.1.10.json",
        },
    }


def _complete_cyclonedx_report(subject: Path) -> dict:
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:3e671687-395b-41f5-a30f-a58921a69b79",
        "version": 1,
        "metadata": {
            "timestamp": "2026-08-07T00:00:00Z",
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "syft",
                        "version": "1.50.0",
                    }
                ]
            },
            "component": {
                "type": "file",
                "name": str(subject.resolve()),
            },
        },
    }


@pytest.fixture
def locked_tool_files(tmp_path):
    tool_root = tmp_path / "tools"
    tool_root.mkdir()
    trivy = tool_root / "trivy.exe"
    syft = tool_root / "syft.exe"
    trivy.write_bytes(b"trivy-0.70.0")
    syft.write_bytes(b"syft-1.50.0")
    lock_path = tmp_path / "supply-chain-tools.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "trivy": {
                    "version": "0.70.0",
                    "executable": "trivy.exe",
                    "executable_sha256": __import__("hashlib")
                    .sha256(trivy.read_bytes())
                    .hexdigest(),
                    "source_verification": {"verified": True},
                },
                "syft": {
                    "version": "1.50.0",
                    "executable": "syft.exe",
                    "executable_sha256": __import__("hashlib")
                    .sha256(syft.read_bytes())
                    .hexdigest(),
                    "source_verification": {"verified": True},
                },
            }
        ),
        encoding="utf-8",
    )
    return tool_root, trivy, syft, lock_path


def test_critical_finding_blocks_exact_digest_and_persists_sanitized_summary(tmp_path) -> None:
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )
    repository = InMemoryCapabilityGovernanceRepository()
    service = CapabilitySupplyChainEvidenceService(
        repository,
        FixedSupplyChainTools(),
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    evidence = service.collect(target, tmp_path)
    restored = service.get(target)

    assert evidence.status is SupplyChainEvidenceStatus.BLOCKED
    assert evidence.blockers == ("critical_vulnerability",)
    assert restored == evidence
    assert evidence.subject_digest == target.digest
    assert evidence.cyclonedx_spec_version == "1.6"
    assert str(tmp_path) not in evidence.model_dump_json()


def test_medium_misconfiguration_is_recorded_without_blocking(tmp_path) -> None:
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "8" * 64,
    )
    service = CapabilitySupplyChainEvidenceService(
        InMemoryCapabilityGovernanceRepository(),
        MisconfiguredSupplyChainTools(),
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    evidence = service.collect(target, tmp_path)

    assert evidence.status is SupplyChainEvidenceStatus.PASSED
    assert evidence.blockers == ()
    assert evidence.misconfiguration_failure_count == 1


@pytest.mark.parametrize(
    ("tools", "count_field"),
    [
        (CriticalMisconfigurationTools(), "critical_misconfiguration_count"),
        (
            FixableHighMisconfigurationTools(),
            "fixable_high_misconfiguration_count",
        ),
    ],
)
def test_severe_misconfiguration_is_a_hard_blocker(
    tmp_path,
    tools,
    count_field,
) -> None:
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "9" * 64,
    )
    service = CapabilitySupplyChainEvidenceService(
        InMemoryCapabilityGovernanceRepository(),
        tools,
        now=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    evidence = service.collect(target, tmp_path)

    assert evidence.status is SupplyChainEvidenceStatus.BLOCKED
    assert evidence.blockers == ("misconfiguration_failure",)
    assert getattr(evidence, count_field) == 1


def test_stale_database_blocks_and_sqlite_reopens_same_evidence(tmp_path) -> None:
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "b" * 64,
    )
    db_path = tmp_path / "governance.db"
    migrate_capability_governance(db_path, tmp_path / "before.db")
    first = CapabilitySupplyChainEvidenceService(
        SqliteCapabilityGovernanceRepository(str(db_path)),
        FixedSupplyChainTools(),
        now=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
    ).collect(target, tmp_path)
    reopened = CapabilitySupplyChainEvidenceService(
        SqliteCapabilityGovernanceRepository(str(db_path)),
        FixedSupplyChainTools(),
        now=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
    ).get(target)

    assert first.status is SupplyChainEvidenceStatus.BLOCKED
    assert "trivy_database_stale" in first.blockers
    assert reopened == first


def test_database_freshness_window_expires_only_after_seven_days(tmp_path) -> None:
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "6" * 64,
    )
    current_time = {"value": datetime(2026, 8, 7, tzinfo=timezone.utc)}
    service = CapabilitySupplyChainEvidenceService(
        InMemoryCapabilityGovernanceRepository(),
        FixedSupplyChainTools(),
        now=lambda: current_time["value"],
    )
    service.collect(target, tmp_path)

    current_time["value"] = datetime(2026, 8, 13, tzinfo=timezone.utc)
    assert service.requires_collection(target) is False

    current_time["value"] = datetime(2026, 8, 13, 0, 0, 1, tzinfo=timezone.utc)
    assert service.requires_collection(target) is True


def test_locked_cli_collects_trivy_and_two_sbom_formats_for_exact_digest(
    tmp_path,
    locked_tool_files,
) -> None:
    target = CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "c" * 64,
    )
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / ".mangrove-capability-digest").write_text(
        target.digest + "\n", encoding="utf-8"
    )
    write_capability_integrity(subject, target.digest)
    tool_root, _trivy, _syft, lock_path = locked_tool_files
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append([str(item) for item in command])
        if "--version" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "Version": "0.70.0",
                        "VulnerabilityDB": {
                            "Version": 2,
                            "UpdatedAt": "2026-08-06T00:00:00Z",
                            "NextUpdate": "2026-08-07T00:00:00Z",
                            "DownloadedAt": "2026-08-06T01:00:00Z",
                        },
                    }
                ),
                stderr="",
            )
        output_argument = command[command.index("--output") + 1]
        output = Path(output_argument.split("=", 1)[-1])
        if Path(command[0]).name == "trivy.exe":
            output.write_text(
                json.dumps(
                    _complete_trivy_report(
                        subject,
                        report_id="report-with-findings",
                        results=[
                            {
                                "Target": "package-lock.json",
                                "Class": "lang-pkgs",
                                "Type": "npm",
                                "Vulnerabilities": [
                                    {"Severity": "CRITICAL", "FixedVersion": ""},
                                    {"Severity": "HIGH", "FixedVersion": "2.0"},
                                ],
                                "Secrets": [{"RuleID": "private-key"}],
                                "Misconfigurations": [
                                    {
                                        "Status": "FAIL",
                                        "Severity": "CRITICAL",
                                        "Resolution": "修复关键配置",
                                    },
                                    {
                                        "Status": "FAIL",
                                        "Severity": "HIGH",
                                        "Resolution": "应用可用修复",
                                    },
                                ],
                            }
                        ],
                    )
                ),
                encoding="utf-8",
            )
        elif any(str(argument).startswith("syft-json=") for argument in command):
            output.write_text(
                json.dumps(
                    _complete_syft_report(subject, source_id="source-with-findings")
                ),
                encoding="utf-8",
            )
        else:
            output.write_text(
                json.dumps(_complete_cyclonedx_report(subject)),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    collected = LockedCliSupplyChainTools(
        tool_root=tool_root,
        evidence_root=tmp_path / "evidence",
        cache_root=tmp_path / "cache",
        lock_path=lock_path,
        runner=fake_run,
    ).collect(target, subject)

    assert collected.subject_digest == target.digest
    assert collected.secret_count == 1
    assert collected.critical_count == 1
    assert collected.fixable_high_count == 1
    assert collected.misconfiguration_failure_count == 2
    assert collected.critical_misconfiguration_count == 1
    assert collected.fixable_high_misconfiguration_count == 1
    assert collected.cyclonedx_spec_version == "1.6"
    assert len(calls) == 4
    assert all(
        any(str(subject.resolve()) in argument for argument in call)
        for call in calls
        if "--version" not in call
    )
    assert all(
        "--skip-db-update" in call
        for call in calls
        if Path(call[0]).name == "trivy.exe" and "--version" not in call
    )


def test_locked_cli_accepts_complete_zero_finding_reports_for_same_subject(
    tmp_path,
    locked_tool_files,
) -> None:
    target = CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="empty-skill",
        version="1.0.0",
        digest="sha256:" + "7" * 64,
    )
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / ".mangrove-capability-digest").write_text(
        target.digest,
        encoding="utf-8",
    )
    write_capability_integrity(subject, target.digest)
    tool_root, _trivy, _syft, lock_path = locked_tool_files

    def fake_run(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "Version": "0.70.0",
                        "VulnerabilityDB": {
                            "Version": 2,
                            "UpdatedAt": "2026-08-06T00:00:00Z",
                        },
                    }
                ),
                stderr="",
            )
        output_argument = command[command.index("--output") + 1]
        output = Path(output_argument.split("=", 1)[-1])
        if Path(command[0]).name == "trivy.exe":
            output.write_text(
                json.dumps(
                    _complete_trivy_report(
                        subject,
                        report_id="report-zero-findings",
                    )
                ),
                encoding="utf-8",
            )
        elif any(str(argument).startswith("syft-json=") for argument in command):
            output.write_text(
                json.dumps(
                    _complete_syft_report(subject, source_id="source-zero-findings")
                ),
                encoding="utf-8",
            )
        else:
            output.write_text(
                json.dumps(_complete_cyclonedx_report(subject)),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    collected = LockedCliSupplyChainTools(
        tool_root=tool_root,
        evidence_root=tmp_path / "evidence",
        cache_root=tmp_path / "cache",
        lock_path=lock_path,
        runner=fake_run,
    ).collect(target, subject)

    assert (
        collected.secret_count,
        collected.critical_count,
        collected.fixable_high_count,
        collected.misconfiguration_failure_count,
    ) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("trivy_report", "syft_report", "cyclonedx_report", "expected_error"),
    [
        (
            {},
            None,
            None,
            "Trivy 扫描报告缺少结果列表",
        ),
        (
            {"Results": [{}]},
            None,
            None,
            "Trivy 扫描报告结果项缺少目标元数据",
        ),
        (
            None,
            {},
            None,
            "Syft SBOM 缺少制品列表",
        ),
        (
            None,
            {"artifacts": []},
            None,
            "Syft SBOM 缺少完整主体元数据",
        ),
        (
            None,
            {"artifacts": [{}]},
            None,
            "Syft SBOM 制品项格式不正确",
        ),
        (
            None,
            None,
            {"bomFormat": "CycloneDX", "specVersion": "1.6"},
            "CycloneDX 输出缺少完整主体元数据",
        ),
        (
            None,
            None,
            {"specVersion": "1.6"},
            "CycloneDX 输出格式不正确",
        ),
    ],
)
def test_locked_cli_rejects_incomplete_report(
    tmp_path,
    locked_tool_files,
    trivy_report,
    syft_report,
    cyclonedx_report,
    expected_error,
) -> None:
    target = CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "f" * 64,
    )
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / ".mangrove-capability-digest").write_text(
        target.digest, encoding="utf-8"
    )
    write_capability_integrity(subject, target.digest)
    tool_root, _trivy, _syft, lock_path = locked_tool_files

    def fake_run(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "Version": "0.70.0",
                        "VulnerabilityDB": {
                            "Version": 2,
                            "UpdatedAt": "2026-08-06T00:00:00Z",
                        },
                    }
                ),
                stderr="",
            )
        output_argument = command[command.index("--output") + 1]
        output = Path(output_argument.split("=", 1)[-1])
        if Path(command[0]).name == "trivy.exe":
            valid_trivy = _complete_trivy_report(
                subject,
                report_id="complete-trivy-report",
            )
            selected_trivy = valid_trivy
            if trivy_report == {}:
                selected_trivy = {}
            elif trivy_report is not None:
                selected_trivy = {**valid_trivy, **trivy_report}
            output.write_text(
                json.dumps(selected_trivy),
                encoding="utf-8",
            )
        elif any(str(argument).startswith("syft-json=") for argument in command):
            valid_syft = _complete_syft_report(subject, source_id="complete-source")
            selected_syft = valid_syft
            if syft_report is not None:
                selected_syft = syft_report
            output.write_text(
                json.dumps(selected_syft),
                encoding="utf-8",
            )
        else:
            valid_cyclonedx = _complete_cyclonedx_report(subject)
            output.write_text(
                json.dumps(
                    valid_cyclonedx
                    if cyclonedx_report is None
                    else cyclonedx_report
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    tools = LockedCliSupplyChainTools(
        tool_root=tool_root,
        evidence_root=tmp_path / "evidence",
        cache_root=tmp_path / "cache",
        lock_path=lock_path,
        runner=fake_run,
    )

    with pytest.raises(ValueError, match=expected_error):
        tools.collect(target, subject)


def test_locked_cli_refuses_changed_executable_or_subject_digest(
    tmp_path,
    locked_tool_files,
) -> None:
    target = CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "d" * 64,
    )
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / ".mangrove-capability-digest").write_text(
        "sha256:" + "e" * 64, encoding="utf-8"
    )
    tool_root, trivy, syft, lock_path = locked_tool_files
    trivy.write_bytes(b"changed")
    syft.write_bytes(b"syft")
    lock_path.write_text(
        json.dumps(
            {
                "trivy": {
                    "version": "0.70.0",
                    "executable": "trivy.exe",
                    "executable_sha256": "0" * 64,
                    "source_verification": {"verified": True},
                },
                "syft": {
                    "version": "1.50.0",
                    "executable": "syft.exe",
                    "executable_sha256": "0" * 64,
                    "source_verification": {"verified": True},
                },
            }
        ),
        encoding="utf-8",
    )

    tools = LockedCliSupplyChainTools(
        tool_root=tool_root,
        evidence_root=tmp_path / "evidence",
        cache_root=tmp_path / "cache",
        lock_path=lock_path,
    )

    try:
        tools.collect(target, subject)
    except ValueError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("主体 digest 不一致时必须失败关闭")

    (subject / ".mangrove-capability-digest").write_text(
        target.digest, encoding="utf-8"
    )
    write_capability_integrity(subject, target.digest)
    try:
        tools.collect(target, subject)
    except ValueError as exc:
        assert "可执行文件 digest" in str(exc)
    else:
        raise AssertionError("扫描器可执行文件变化时必须失败关闭")


def test_locked_cli_rejects_changed_subject_content_before_or_during_scanning(
    tmp_path,
    locked_tool_files,
) -> None:
    target = CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "9" * 64,
    )
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / ".mangrove-capability-digest").write_text(
        target.digest, encoding="utf-8"
    )
    payload = subject / "payload"
    payload.write_bytes(b"frozen")
    write_capability_integrity(subject, target.digest)
    payload.write_bytes(b"changed")

    tools = LockedCliSupplyChainTools(
        tool_root=tmp_path / "missing-tools",
        evidence_root=tmp_path / "evidence",
        cache_root=tmp_path / "cache",
        lock_path=tmp_path / "missing-lock.json",
    )

    with pytest.raises(RuntimeError, match="完整性"):
        tools.collect(target, subject)

    write_capability_integrity(subject, target.digest)
    tool_root, _trivy, _syft, lock_path = locked_tool_files

    def fake_run(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "Version": "0.70.0",
                        "VulnerabilityDB": {
                            "Version": 2,
                            "UpdatedAt": "2026-08-06T00:00:00Z",
                        },
                    }
                ),
                stderr="",
            )
        output_argument = command[command.index("--output") + 1]
        output = Path(output_argument.split("=", 1)[-1])
        if Path(command[0]).name == "trivy.exe":
            output.write_text(
                json.dumps(
                    _complete_trivy_report(
                        subject,
                        report_id="report-during-mutation",
                    )
                ),
                encoding="utf-8",
            )
            payload.write_bytes(b"changed-during-scan")
        elif any(str(argument).startswith("syft-json=") for argument in command):
            output.write_text(
                json.dumps(
                    _complete_syft_report(subject, source_id="source-during-mutation")
                ),
                encoding="utf-8",
            )
        else:
            output.write_text(
                json.dumps(_complete_cyclonedx_report(subject)),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    tools = LockedCliSupplyChainTools(
        tool_root=tool_root,
        evidence_root=tmp_path / "evidence-during",
        cache_root=tmp_path / "cache-during",
        lock_path=lock_path,
        runner=fake_run,
    )

    with pytest.raises(RuntimeError, match="完整性"):
        tools.collect(target, subject)
