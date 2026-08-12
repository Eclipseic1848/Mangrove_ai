# -*- coding: utf-8 -*-
"""AC-07 #35：供应链证据必须绑定精确 digest 并按硬门失败关闭。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

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


def test_locked_cli_collects_trivy_and_two_sbom_formats_for_exact_digest(tmp_path) -> None:
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
    tool_root = tmp_path / "tools"
    tool_root.mkdir()
    trivy = tool_root / "trivy.exe"
    syft = tool_root / "syft.exe"
    trivy.write_bytes(b"trivy-0.70.0")
    syft.write_bytes(b"syft-1.50.0")
    lock = {
        "trivy": {
            "version": "0.70.0",
            "executable": "trivy.exe",
            "executable_sha256": __import__("hashlib").sha256(trivy.read_bytes()).hexdigest(),
            "source_verification": {"verified": True},
        },
        "syft": {
            "version": "1.50.0",
            "executable": "syft.exe",
            "executable_sha256": __import__("hashlib").sha256(syft.read_bytes()).hexdigest(),
            "source_verification": {"verified": True},
        },
    }
    lock_path = tmp_path / "supply-chain-tools.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
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
                    {
                        "Results": [
                            {
                                "Vulnerabilities": [
                                    {"Severity": "CRITICAL", "FixedVersion": ""},
                                    {"Severity": "HIGH", "FixedVersion": "2.0"},
                                ],
                                "Secrets": [{"RuleID": "private-key"}],
                                "Misconfigurations": [{"Status": "FAIL"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
        elif "syft-json" in command:
            output.write_text('{"artifacts": []}', encoding="utf-8")
        else:
            output.write_text(
                '{"bomFormat":"CycloneDX","specVersion":"1.6"}', encoding="utf-8"
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
    assert collected.misconfiguration_failure_count == 1
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


def test_locked_cli_refuses_changed_executable_or_subject_digest(tmp_path) -> None:
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
    tool_root = tmp_path / "tools"
    tool_root.mkdir()
    (tool_root / "trivy.exe").write_bytes(b"changed")
    (tool_root / "syft.exe").write_bytes(b"syft")
    lock_path = tmp_path / "lock.json"
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
    try:
        tools.collect(target, subject)
    except ValueError as exc:
        assert "可执行文件 digest" in str(exc)
    else:
        raise AssertionError("扫描器可执行文件变化时必须失败关闭")
