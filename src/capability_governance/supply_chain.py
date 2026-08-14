# -*- coding: utf-8 -*-
"""Trivy/Syft 外部工具与能力治理之间的失败关闭证据 Seam。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable, Protocol, Sequence

from src.capability_catalog.integrity import verify_capability_integrity

from .models import (
    CapabilityGovernanceTarget,
    CapabilitySupplyChainEvidence,
    SupplyChainCollection,
    SupplyChainEvidenceStatus,
    TRIVY_DATABASE_MAX_AGE,
)
from .repository import CapabilityGovernanceRepository
from .tool_lock import load_locked_executable, sha256_file


class SupplyChainTools(Protocol):
    def collect(
        self,
        target: CapabilityGovernanceTarget,
        subject_root: Path,
    ) -> SupplyChainCollection: ...


class LockedCliSupplyChainTools:
    """只运行哈希锁定的 Trivy/Syft，并将原始证据留在受控目录。"""

    def __init__(
        self,
        *,
        tool_root: str | Path,
        evidence_root: str | Path,
        cache_root: str | Path,
        lock_path: str | Path,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._tool_root = Path(tool_root).resolve()
        self._evidence_root = Path(evidence_root).resolve()
        self._cache_root = Path(cache_root).resolve()
        self._lock_path = Path(lock_path).resolve()
        self._runner = runner

    def _load_tool(self, name: str, expected_version: str) -> Path:
        lock = json.loads(self._lock_path.read_text(encoding="utf-8"))
        return load_locked_executable(
            lock=lock,
            name=name,
            expected_version=expected_version,
            tool_root=self._tool_root,
        )

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        completed = self._runner(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )
        if completed.returncode != 0:
            # 原始 stderr 可能包含宿主路径，只向上返回稳定错误类别。
            raise RuntimeError(f"供应链工具执行失败: {Path(command[0]).stem}")
        return completed

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _is_same_subject(value: Any, subject: Path) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            return Path(value).resolve() == subject
        except OSError:
            return False

    def collect(
        self,
        target: CapabilityGovernanceTarget,
        subject_root: Path,
    ) -> SupplyChainCollection:
        subject = subject_root.resolve()
        if not subject.is_dir():
            raise ValueError("供应链扫描主体不存在")
        marker = subject / ".mangrove-capability-digest"
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != target.digest:
            raise ValueError("供应链扫描主体 digest 与治理目标不一致")
        verify_capability_integrity(subject, target.digest)

        trivy = self._load_tool("trivy", "0.70.0")
        syft = self._load_tool("syft", "1.50.0")
        self._evidence_root.mkdir(parents=True, exist_ok=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        evidence_dir = Path(tempfile.mkdtemp(prefix="scan-", dir=self._evidence_root))
        trivy_output = evidence_dir / "trivy.json"
        syft_output = evidence_dir / "syft.json"
        cyclonedx_output = evidence_dir / "cyclonedx-1.6.json"
        trivy_config = {
            "scanners": ["vuln", "misconfig", "secret"],
            "skip_db_update": True,
            "skip_check_update": True,
            "offline_scan": True,
        }
        config_sha256 = hashlib.sha256(
            json.dumps(trivy_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self._run(
            [
                str(trivy), "fs", "--scanners", "vuln,misconfig,secret",
                "--format", "json", "--output", str(trivy_output),
                "--cache-dir", str(self._cache_root / "trivy"),
                "--skip-db-update", "--skip-check-update", "--offline-scan",
                str(subject),
            ]
        )
        version_result = self._run(
            [
                str(trivy),
                "--version",
                "--format",
                "json",
                "--cache-dir",
                str(self._cache_root / "trivy"),
            ]
        )
        self._run(
            [str(syft), "scan", f"dir:{subject}", "--output", f"syft-json={syft_output}"]
        )
        self._run(
            [
                str(syft),
                "scan",
                f"dir:{subject}",
                "--output",
                f"cyclonedx-json@1.6={cyclonedx_output}",
            ]
        )

        trivy_data = json.loads(trivy_output.read_text(encoding="utf-8"))
        trivy_identity = trivy_data.get("Trivy") if isinstance(trivy_data, dict) else None
        artifact_name = (
            trivy_data.get("ArtifactName") if isinstance(trivy_data, dict) else None
        )
        if (
            not isinstance(trivy_data, dict)
            or trivy_data.get("SchemaVersion") != 2
            or not isinstance(trivy_identity, dict)
            or trivy_identity.get("Version") != "0.70.0"
            or trivy_data.get("ArtifactType") != "filesystem"
            or not self._is_same_subject(artifact_name, subject)
            or not isinstance(trivy_data.get("ReportID"), str)
            or not trivy_data["ReportID"]
            or self._parse_time(trivy_data.get("CreatedAt")) is None
        ):
            # 零发现报告可以省略 Results，但工具、主体或生成时间不可判定时必须失败关闭。
            raise ValueError("Trivy 扫描报告缺少结果列表或主体元数据")
        raw_results = trivy_data.get("Results")
        if raw_results is None:
            results = []
        elif isinstance(raw_results, list) and all(
            isinstance(result, dict) for result in raw_results
        ):
            results = raw_results
        else:
            raise ValueError("Trivy 扫描报告结果列表格式不正确")
        for result in results:
            if not all(
                isinstance(result.get(field), str) and result[field]
                for field in ("Target", "Class", "Type")
            ):
                raise ValueError("Trivy 扫描报告结果项缺少目标元数据")
            for field in (
                "Packages",
                "Vulnerabilities",
                "Secrets",
                "Misconfigurations",
            ):
                items = result.get(field)
                if items is not None and (
                    not isinstance(items, list)
                    or not all(isinstance(item, dict) for item in items)
                ):
                    raise ValueError("Trivy 扫描报告结果项格式不正确")
        vulnerabilities = [
            item
            for result in results
            for item in (result.get("Vulnerabilities") or [])
        ]
        secrets = [item for result in results for item in (result.get("Secrets") or [])]
        misconfigurations = [
            item
            for result in results
            for item in (result.get("Misconfigurations") or [])
            if item.get("Status") == "FAIL"
        ]
        version_data = json.loads(version_result.stdout)
        if version_data.get("Version") != "0.70.0":
            raise ValueError("Trivy 运行版本与锁定版本不一致")
        database = version_data.get("VulnerabilityDB") or {}
        updated_at = self._parse_time(database.get("UpdatedAt"))
        if updated_at is None:
            raise RuntimeError("Trivy 漏洞库元数据不可用")
        syft_data = json.loads(syft_output.read_text(encoding="utf-8"))
        if not isinstance(syft_data, dict) or not isinstance(
            syft_data.get("artifacts"), list
        ):
            raise ValueError("Syft SBOM 缺少制品列表")
        for artifact in syft_data["artifacts"]:
            if (
                not isinstance(artifact, dict)
                or not all(
                    isinstance(artifact.get(field), str) and artifact[field]
                    for field in ("id", "name", "type", "foundBy")
                )
                or not isinstance(artifact.get("version"), str)
                or not isinstance(artifact.get("locations"), list)
                or not all(
                    isinstance(location, dict) for location in artifact["locations"]
                )
            ):
                raise ValueError("Syft SBOM 制品项格式不正确")
        syft_source = syft_data.get("source")
        syft_descriptor = syft_data.get("descriptor")
        syft_schema = syft_data.get("schema")
        syft_relationships = syft_data.get("artifactRelationships")
        source_metadata = (
            syft_source.get("metadata") if isinstance(syft_source, dict) else None
        )
        schema_version = (
            syft_schema.get("version") if isinstance(syft_schema, dict) else None
        )
        if (
            not isinstance(syft_relationships, list)
            or not isinstance(syft_source, dict)
            or not isinstance(syft_source.get("id"), str)
            or not syft_source["id"]
            or syft_source.get("type") != "directory"
            or not self._is_same_subject(syft_source.get("name"), subject)
            or not isinstance(source_metadata, dict)
            or not self._is_same_subject(source_metadata.get("path"), subject)
            or not isinstance(syft_descriptor, dict)
            or syft_descriptor.get("name") != "syft"
            or syft_descriptor.get("version") != "1.50.0"
            or not isinstance(schema_version, str)
            or not schema_version
            or not isinstance(syft_schema.get("url"), str)
            or syft_schema["url"]
            != (
                "https://raw.githubusercontent.com/anchore/syft/main/schema/json/"
                f"schema-{schema_version}.json"
            )
        ):
            raise ValueError("Syft SBOM 缺少完整主体元数据")
        cyclonedx_data = json.loads(cyclonedx_output.read_text(encoding="utf-8"))
        if not isinstance(cyclonedx_data, dict) or (
            cyclonedx_data.get("bomFormat") != "CycloneDX"
        ):
            raise ValueError("CycloneDX 输出格式不正确")
        if cyclonedx_data.get("specVersion") != "1.6":
            raise ValueError("CycloneDX 输出版本不是 1.6")
        cyclonedx_metadata = cyclonedx_data.get("metadata")
        cyclonedx_tools = (
            cyclonedx_metadata.get("tools")
            if isinstance(cyclonedx_metadata, dict)
            else None
        )
        tool_components = (
            cyclonedx_tools.get("components")
            if isinstance(cyclonedx_tools, dict)
            else None
        )
        described_component = (
            cyclonedx_metadata.get("component")
            if isinstance(cyclonedx_metadata, dict)
            else None
        )
        if (
            cyclonedx_data.get("$schema")
            != "http://cyclonedx.org/schema/bom-1.6.schema.json"
            or not isinstance(cyclonedx_data.get("version"), int)
            or cyclonedx_data["version"] < 1
            or not isinstance(cyclonedx_data.get("serialNumber"), str)
            or re.fullmatch(
                r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                cyclonedx_data["serialNumber"],
            )
            is None
            or not isinstance(cyclonedx_metadata, dict)
            or self._parse_time(cyclonedx_metadata.get("timestamp")) is None
            or not isinstance(tool_components, list)
            or not any(
                isinstance(tool, dict)
                and tool.get("name") == "syft"
                and tool.get("version") == "1.50.0"
                for tool in tool_components
            )
            or not isinstance(described_component, dict)
            or not self._is_same_subject(described_component.get("name"), subject)
        ):
            raise ValueError("CycloneDX 输出缺少完整主体元数据")
        # 扫描可能持续较久；结束时再次确认工具分析的仍是同一份冻结内容。
        verify_capability_integrity(subject, target.digest)
        return SupplyChainCollection(
            subject_digest=target.digest,
            trivy_version="0.70.0",
            trivy_config_sha256=config_sha256,
            trivy_result_sha256=sha256_file(trivy_output),
            trivy_database={
                "version": database.get("Version"),
                "updated_at": updated_at,
                "next_update": self._parse_time(database.get("NextUpdate")),
                "downloaded_at": self._parse_time(database.get("DownloadedAt")),
            },
            secret_count=len(secrets),
            critical_count=sum(item.get("Severity") == "CRITICAL" for item in vulnerabilities),
            fixable_high_count=sum(
                item.get("Severity") == "HIGH" and bool(item.get("FixedVersion"))
                for item in vulnerabilities
            ),
            misconfiguration_failure_count=len(misconfigurations),
            critical_misconfiguration_count=sum(
                item.get("Severity") == "CRITICAL" for item in misconfigurations
            ),
            fixable_high_misconfiguration_count=sum(
                item.get("Severity") == "HIGH" and bool(item.get("Resolution"))
                for item in misconfigurations
            ),
            syft_version="1.50.0",
            syft_json_sha256=sha256_file(syft_output),
            cyclonedx_json_sha256=sha256_file(cyclonedx_output),
            cyclonedx_spec_version="1.6",
        )


class CapabilitySupplyChainEvidenceService:
    """只接受受控 CLI 摘要，并按 ADR-0029 的硬门形成不可变结论。"""

    def __init__(
        self,
        repository: CapabilityGovernanceRepository,
        tools: SupplyChainTools,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._tools = tools
        self._now = now or (lambda: datetime.now(timezone.utc))

    def collect(
        self,
        target: CapabilityGovernanceTarget,
        subject_root: str | Path,
    ) -> CapabilitySupplyChainEvidence:
        collected = self._tools.collect(target, Path(subject_root))
        if collected.subject_digest != target.digest:
            raise ValueError("供应链证据主体 digest 与治理目标不一致")
        blockers: list[str] = []
        if collected.secret_count:
            blockers.append("secret_detected")
        if collected.critical_count:
            blockers.append("critical_vulnerability")
        if collected.fixable_high_count:
            blockers.append("fixable_high_vulnerability")
        if (
            collected.critical_misconfiguration_count
            or collected.fixable_high_misconfiguration_count
        ):
            blockers.append("misconfiguration_failure")
        occurred_at = self._now()
        if occurred_at - collected.trivy_database.updated_at > TRIVY_DATABASE_MAX_AGE:
            blockers.append("trivy_database_stale")
        identity = hashlib.sha256(
            (
                target.digest
                + collected.trivy_result_sha256
                + collected.syft_json_sha256
                + collected.cyclonedx_json_sha256
                + occurred_at.isoformat()
            ).encode("utf-8")
        ).hexdigest()[:20]
        evidence = CapabilitySupplyChainEvidence(
            evidence_id=f"supply_{identity}",
            target=target,
            subject_digest=collected.subject_digest,
            status=(
                SupplyChainEvidenceStatus.BLOCKED
                if blockers
                else SupplyChainEvidenceStatus.PASSED
            ),
            blockers=tuple(blockers),
            secret_count=collected.secret_count,
            critical_count=collected.critical_count,
            fixable_high_count=collected.fixable_high_count,
            misconfiguration_failure_count=(
                collected.misconfiguration_failure_count
            ),
            critical_misconfiguration_count=(
                collected.critical_misconfiguration_count
            ),
            fixable_high_misconfiguration_count=(
                collected.fixable_high_misconfiguration_count
            ),
            trivy_version=collected.trivy_version,
            trivy_config_sha256=collected.trivy_config_sha256,
            trivy_result_sha256=collected.trivy_result_sha256,
            trivy_database=collected.trivy_database,
            syft_version=collected.syft_version,
            syft_json_sha256=collected.syft_json_sha256,
            cyclonedx_json_sha256=collected.cyclonedx_json_sha256,
            cyclonedx_spec_version=collected.cyclonedx_spec_version,
            occurred_at=occurred_at,
        )
        return self._repository.save_supply_chain_evidence(evidence)

    def get(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilitySupplyChainEvidence | None:
        return self._repository.get_latest_supply_chain_evidence(target)

    def requires_collection(self, target: CapabilityGovernanceTarget) -> bool:
        evidence = self.get(target)
        return evidence is None or (
            self._now() - evidence.trivy_database.updated_at > TRIVY_DATABASE_MAX_AGE
        )
