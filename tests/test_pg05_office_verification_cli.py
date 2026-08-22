# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DRIVER = PROJECT_ROOT / "scripts" / "verify_pi_runtime_pg05_office.py"


def _load_driver():
    spec = importlib.util.spec_from_file_location("pg05_office_driver", DRIVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _run_cli(
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DRIVER), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **(env or {})},
    )


def test_office_cli_exposes_configurable_long_task_timeout() -> None:
    completed = _run_cli("--help")

    assert completed.returncode == 0
    assert "--timeout-seconds" in completed.stdout
    assert "default: 1800" in completed.stdout


@pytest.mark.parametrize("value", ("", "0", "-1"))
def test_office_cli_rejects_non_positive_or_empty_timeout(value: str) -> None:
    completed = _run_cli(
        "--case",
        "word",
        "--owner-id",
        "u_owner",
        "--upload-id",
        "missing",
        "--timeout-seconds",
        value,
    )

    assert completed.returncode == 2
    assert "timeout-seconds 必须是正整数" in completed.stderr


def test_office_cli_rejects_timeout_above_admin_budget() -> None:
    completed = _run_cli(
        "--case",
        "word",
        "--owner-id",
        "u_owner",
        "--upload-id",
        "a" * 32,
        "--timeout-seconds",
        "7201",
    )

    assert completed.returncode == 2
    assert "timeout-seconds 不能超过 7200" in completed.stderr


@pytest.mark.parametrize(
    ("owner_id", "upload_id", "message"),
    [
        ("", "a" * 32, "owner-id 不能为空"),
        ("u_owner", "", "upload-id 不能为空"),
        ("../u_owner", "a" * 32, "owner-id 不能包含路径分隔符"),
    ],
)
def test_office_cli_rejects_empty_or_unsafe_identifiers(
    owner_id: str,
    upload_id: str,
    message: str,
) -> None:
    completed = _run_cli(
        "--case",
        "word",
        "--owner-id",
        owner_id,
        "--upload-id",
        upload_id,
    )

    assert completed.returncode == 2
    assert message in completed.stderr


def test_office_cli_fails_closed_before_model_for_cross_owner_upload(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    owner_a = "u_owner_a"
    owner_b = "u_owner_b"
    upload_id = "a" * 32
    objects = upload_root / owner_a / "objects"
    objects.mkdir(parents=True)
    source = objects / upload_id
    source.write_bytes(b"owner-a-source")
    (objects / f"{upload_id}.meta").write_text(
        json.dumps(
            {
                "upload_id": upload_id,
                "user_id": owner_a,
                "original_name": "source.docx",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "--case",
        "word",
        "--owner-id",
        owner_b,
        "--upload-id",
        upload_id,
        env={"DATA_PREP_UPLOAD_ROOT": str(upload_root)},
    )

    assert completed.returncode == 1
    assert "指定 Owner 下未找到上传对象" in completed.stderr
    assert "category=input_invalid" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert "agent.started" not in completed.stdout


def test_office_cli_rejects_metadata_owned_by_another_user(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    owner_id = "u_owner"
    upload_id = "b" * 32
    objects = upload_root / owner_id / "objects"
    objects.mkdir(parents=True)
    source = objects / upload_id
    source.write_bytes(b"source")
    (objects / f"{upload_id}.meta").write_text(
        json.dumps(
            {
                "upload_id": upload_id,
                "user_id": "u_another_owner",
                "original_name": "source.docx",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "--case",
        "word",
        "--owner-id",
        owner_id,
        "--upload-id",
        upload_id,
        env={"DATA_PREP_UPLOAD_ROOT": str(upload_root)},
    )

    assert completed.returncode == 1
    assert "category=permission_denied" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert "agent.started" not in completed.stdout


@pytest.mark.parametrize(
    "empty_field",
    ("upload_id", "user_id", "original_name", "sha256"),
)
def test_office_cli_rejects_empty_required_upload_metadata_before_model(
    tmp_path: Path,
    empty_field: str,
) -> None:
    upload_root = tmp_path / "uploads"
    owner_id = "u_owner"
    upload_id = "c" * 32
    objects = upload_root / owner_id / "objects"
    objects.mkdir(parents=True)
    source = objects / upload_id
    source.write_bytes(b"source")
    metadata = {
        "upload_id": upload_id,
        "user_id": owner_id,
        "original_name": "source.docx",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    metadata[empty_field] = ""
    (objects / f"{upload_id}.meta").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )

    completed = _run_cli(
        "--case",
        "word",
        "--owner-id",
        owner_id,
        "--upload-id",
        upload_id,
        env={"DATA_PREP_UPLOAD_ROOT": str(upload_root)},
    )

    assert completed.returncode == 1
    assert "category=input_invalid" in completed.stderr
    assert f"上传对象元数据字段 {empty_field} 不能为空" in completed.stderr
    assert "agent.started" not in completed.stdout


def test_office_cli_does_not_expose_missing_source_host_path(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    owner_id = "u_owner"
    upload_id = "d" * 32
    objects = upload_root / owner_id / "objects"
    objects.mkdir(parents=True)
    (objects / f"{upload_id}.meta").write_text(
        json.dumps(
            {
                "upload_id": upload_id,
                "user_id": owner_id,
                "original_name": "source.docx",
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "--case",
        "word",
        "--owner-id",
        owner_id,
        "--upload-id",
        upload_id,
        env={"DATA_PREP_UPLOAD_ROOT": str(upload_root)},
    )

    assert completed.returncode == 1
    assert "category=input_invalid" in completed.stderr
    assert "上传对象源文件不存在" in completed.stderr
    assert str(upload_root) not in completed.stderr


def test_repeated_concurrent_cli_requests_use_distinct_batch_identities(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    arguments = (
        "--case",
        "word",
        "--owner-id",
        "u_owner",
        "--upload-id",
        "a" * 32,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = list(
            executor.map(
                lambda _: _run_cli(
                    *arguments,
                    env={"DATA_PREP_UPLOAD_ROOT": str(upload_root)},
                ),
                range(2),
            )
        )

    assert [result.returncode for result in completed] == [1, 1]
    batch_ids = [
        re.search(r"\[batch:([0-9a-f]{16})\]", result.stdout)
        for result in completed
    ]
    assert all(match is not None for match in batch_ids)
    assert len({match.group(1) for match in batch_ids if match}) == 2


def test_concurrent_batches_allocate_distinct_task_and_workspace_identities(
    tmp_path: Path,
) -> None:
    driver = _load_driver()
    first = driver.OfficeValidationBatch(
        case="word",
        batch_id="1" * 16,
        owner_id="u_owner",
        upload_id="a" * 32,
        repeat=3,
        timeout_seconds=1800,
    )
    second = driver.OfficeValidationBatch(
        case="word",
        batch_id="2" * 16,
        owner_id="u_owner",
        upload_id="a" * 32,
        repeat=3,
        timeout_seconds=1800,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        roots = list(
            executor.map(
                lambda batch: batch.allocate_execution_root(tmp_path, 1),
                (first, second),
            )
        )

    assert first.task_id(1) != second.task_id(1)
    assert roots[0] != roots[1]
    assert roots[0].parent.name == first.batch_id
    assert roots[1].parent.name == second.batch_id


def test_error_detail_redacts_host_paths_but_preserves_model_endpoint() -> None:
    driver = _load_driver()
    message = (
        r"读取 F:\private\owner\source.docx 失败；"
        r"UNC=\\server\share\source.xlsx；"
        "endpoint=http://model.example.invalid/v1"
    )

    redacted = driver._redact_host_paths(message)

    assert r"F:\private" not in redacted
    assert r"\\server\share" not in redacted
    assert redacted.count("<host-path>") == 2
    assert "http://model.example.invalid/v1" in redacted
