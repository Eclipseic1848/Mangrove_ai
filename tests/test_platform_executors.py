# -*- coding: utf-8 -*-
"""#15 AC07-10 阶段 3 暴露缺陷回归：平台六步目录级执行器兼容标准 manifest 名。

真实能力归档的 manifest 是 mangrove-capability.json（mount_resolver 物化展开
也校验该名）；快照生成器重打包后保留该名。平台验证执行器原硬编码读
manifest.json，导致第一步 SyntheticSmoke 抛异常、worker 崩溃、六步永远不推进。
"""
from __future__ import annotations

import json

import pytest

from src.capability_governance.platform_executors import (
    FailClosedDirectoryRunner,
    IndependentVerifierDirectoryRunner,
    MountProbeDirectoryRunner,
    SyntheticSmokeDirectoryRunner,
)
from src.capability_governance.models import PlatformValidationStep


def _manifest(extra: dict | None = None) -> dict:
    data = {
        "schema_version": 1,
        "name": "python-table-summary",
        "version": "2.0.0",
        "kind": "python",
        "entrypoint": {"program": "python", "arguments": ["table_summary.py"]},
        "healthcheck": {"program": "python", "arguments": ["table_summary.py", "--health"]},
        "permissions": ["process:child", "network:none"],
    }
    if extra:
        data.update(extra)
    return data


def _make_subject(tmp_path, manifest_name: str) -> object:
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / manifest_name).write_text(
        json.dumps(_manifest()), encoding="utf-8"
    )
    (subject / "table_summary.py").write_text(
        "def run():\n    return 1\n", encoding="utf-8"
    )
    return subject


@pytest.mark.parametrize(
    "manifest_name", ("mangrove-capability.json", "manifest.json")
)
class TestPlatformExecutorsStandardManifest:
    def test_synthetic_smoke_accepts_manifest(self, tmp_path, manifest_name) -> None:
        runner = SyntheticSmokeDirectoryRunner()
        evidence = runner.run(_make_subject(tmp_path, manifest_name))
        assert evidence.step is PlatformValidationStep.SYNTHETIC_SMOKE
        assert evidence.status.value == "passed"

    def test_fail_closed_accepts_manifest(self, tmp_path, manifest_name) -> None:
        runner = FailClosedDirectoryRunner()
        evidence = runner.run(_make_subject(tmp_path, manifest_name))
        assert evidence.step is PlatformValidationStep.FAIL_CLOSED
        assert evidence.status.value == "passed"

    def test_mount_probe_accepts_manifest(self, tmp_path, manifest_name) -> None:
        runner = MountProbeDirectoryRunner()
        evidence = runner.run(_make_subject(tmp_path, manifest_name))
        assert evidence.step is PlatformValidationStep.MOUNT_PROBE
        assert evidence.status.value == "passed"

    def test_independent_verifier_hashes_manifest(self, tmp_path, manifest_name) -> None:
        runner = IndependentVerifierDirectoryRunner()
        evidence = runner.run(_make_subject(tmp_path, manifest_name))
        assert evidence.step is PlatformValidationStep.INDEPENDENT_VERIFIER
        assert evidence.status.value == "passed"
        assert evidence.evidence_sha256


def test_missing_manifest_fails_closed(tmp_path) -> None:
    """物化目录既无标准名也无兼容名时必须失败关闭。"""
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / "table_summary.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        SyntheticSmokeDirectoryRunner().run(subject)
