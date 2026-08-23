# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import ssl
import sys
import threading
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
from fastapi.testclient import TestClient
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from scripts.verify_g4_provider_safety import (
    _attempt_ledger_callbacks,
    _canonical_sha256,
    _qualification_state_paths,
    _usage_summary,
    authorize_ambiguous_retry,
    assess_g4_evidence,
    execute_pi_provider_chain,
    execute_transport_safety,
    execute_qualification,
    finalize_vault_rotation,
    freeze_manifest,
    prepare_vault_rotation,
    QualificationError,
)
from src.agentic_runtime.models import RuntimeStatus
from src.api.routes import model_relay
from src.model_connections.broker import ConnectionBroker, ConnectionError
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport
from src.model_connections.vault import FernetCredentialVault


def test_usage_summary_aggregates_multi_turn_provider_usage() -> None:
    broker = SimpleNamespace(
        list_usage=lambda *_args, **_kwargs: [
            {
                "status": "recorded",
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "request_count": 1,
            },
            {
                "status": "recorded",
                "input_tokens": 20,
                "output_tokens": 3,
                "total_tokens": 23,
                "request_count": 1,
            },
        ]
    )

    status, summary = _usage_summary(broker, "owner-a", "task-a")

    assert status == "recorded"
    assert summary == {
        "input_tokens": 30,
        "output_tokens": 5,
        "total_tokens": 35,
        "request_count": 2,
    }


def test_usage_summary_fails_closed_when_any_request_is_unknown() -> None:
    broker = SimpleNamespace(
        list_usage=lambda *_args, **_kwargs: [
            {
                "status": "recorded",
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "request_count": 1,
            },
            {
                "status": "unknown",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "request_count": 1,
            },
        ]
    )

    status, summary = _usage_summary(broker, "owner-a", "task-a")

    assert status == "unknown"
    assert summary == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "request_count": 2,
    }


def test_usage_summary_rejects_recorded_rows_with_missing_numbers() -> None:
    broker = SimpleNamespace(
        list_usage=lambda *_args, **_kwargs: [
            {
                "status": "recorded",
                "input_tokens": None,
                "output_tokens": 2,
                "total_tokens": 2,
                "request_count": 1,
            }
        ]
    )

    status, summary = _usage_summary(broker, "owner-a", "task-a")

    assert status == "unknown"
    assert summary == {
        "input_tokens": None,
        "output_tokens": 2,
        "total_tokens": 2,
        "request_count": 1,
    }
from src.connectors.http_security import ValidatedTarget


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "verify_g4_provider_safety.py"


def _create_inventory_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE model_connections (
                connection_id TEXT PRIMARY KEY,
                owner_scope TEXT NOT NULL,
                preset_id TEXT,
                preset_version TEXT NOT NULL,
                display_name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                api_format TEXT NOT NULL,
                locality TEXT NOT NULL,
                secret_id TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE model_connection_models (
                connection_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                status TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO model_connections (
                connection_id, owner_scope, preset_id, preset_version,
                display_name, base_url, model, api_format, locality,
                secret_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "connection-deepseek",
                    "platform_shared",
                    "deepseek",
                    "preset-v1",
                    "平台 DeepSeek",
                    "https://api.deepseek.com",
                    "deepseek-v4-flash",
                    "openai_chat_completions",
                    "public_external",
                    "secret-deepseek-v1",
                    "verified",
                ),
                (
                    "connection-qwen",
                    "platform_shared",
                    "qwen",
                    "preset-v1",
                    "平台百炼",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "qwen3.7-max-2026-06-08",
                    "openai_responses",
                    "public_external",
                    "secret-qwen-v1",
                    "verified",
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO model_connection_models (
                connection_id, model_id, status, enabled
            ) VALUES (?, ?, 'available', 1)
            """,
            [
                ("connection-deepseek", "deepseek-v4-flash"),
                ("connection-qwen", "qwen3.7-max-2026-06-08"),
            ],
        )


def test_freeze_writes_deterministic_secret_free_provider_manifest(tmp_path):
    database = tmp_path / "webui.db"
    output = tmp_path / "manifest.json"
    _create_inventory_database(database)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "freeze",
            "--db-path",
            str(database),
            "--preset",
            "deepseek",
            "--preset",
            "qwen",
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest == {
        "schema_version": "g4-provider-manifest-v1",
        "providers": [
            {
                "connection_id": "connection-deepseek",
                "connection_version": (
                    "3827787e7d056666d14cff0d49620b31f51f2abd33badf441f"
                    "a75a3e0462aaa7"
                ),
                "preset_id": "deepseek",
                "preset_version": "preset-v1",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
                "api_format": "openai_chat_completions",
            },
            {
                "connection_id": "connection-qwen",
                "connection_version": (
                    "35cdee9b3308a1f83868d95e1da67247839ddf8b299413b67fa"
                    "6f221f59c03a1"
                ),
                "preset_id": "qwen",
                "preset_version": "preset-v1",
                "base_url": (
                    "https://dashscope.aliyuncs.com/compatible-mode/v1"
                ),
                "model": "qwen3.7-max-2026-06-08",
                "api_format": "openai_responses",
            },
        ],
        "manifest_sha256": (
            "3d3d98bc18dcec4f18689e400c4255b8c66864486965a9038c"
            "f82e80f1906ed5"
        ),
    }
    serialized = output.read_text(encoding="utf-8")
    assert "secret-deepseek-v1" not in serialized
    assert "secret-qwen-v1" not in serialized


def test_run_rejects_connection_rotation_before_provider_egress(tmp_path):
    database = tmp_path / "webui.db"
    manifest = tmp_path / "manifest.json"
    report = tmp_path / "report.json"
    _create_inventory_database(database)
    frozen = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "freeze",
            "--db-path",
            str(database),
            "--preset",
            "deepseek",
            "--preset",
            "qwen",
            "--output",
            str(manifest),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert frozen.returncode == 0, frozen.stderr
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE model_connections
               SET secret_id = 'secret-deepseek-v2'
             WHERE preset_id = 'deepseek'
            """
        )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "run",
            "--db-path",
            str(database),
            "--manifest",
            str(manifest),
            "--output",
            str(report),
            "--confirm-synthetic-egress",
            "--timeout-seconds",
            "1800",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 2
    assert "冻结后已变化" in completed.stderr
    assert "secret-deepseek-v1" not in completed.stderr
    assert "secret-deepseek-v2" not in completed.stderr
    assert not report.exists()


def test_provider_smoke_uses_http_relay_but_does_not_claim_g4_qualification(
    tmp_path,
):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    provider_secrets = {
        "deepseek-secret-for-test",
        "qwen-secret-for-test",
    }

    def provider(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "id": "response-test",
                    "object": "response",
                    "output_text": "G4_SYNTHETIC_OK",
                    "output": [],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "G4_SYNTHETIC_OK",
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                },
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def configure() -> None:
        await broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
        await broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台百炼",
            preset_id="qwen",
            api_key="qwen-secret-for-test",
            model="qwen3.7-max-2026-06-08",
        )

    asyncio.run(configure())
    manifest = freeze_manifest(
        db_path=database,
        presets=["deepseek", "qwen"],
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(model_relay.router)
    app.dependency_overrides[
        model_relay.get_connection_broker
    ] = lambda: broker

    with TestClient(app) as relay_client:
        report = execute_qualification(
            db_path=database,
            manifest_path=manifest_path,
            output_path=report_path,
            relay_base_url="http://testserver",
            timeout_seconds=1800,
            relay_post=relay_client.post,
            broker=broker,
        )

    assert report["provider_chain_smoke_passed"] is True
    assert report["g4_qualified"] is False
    assert report["qualification_blockers"] == [
        "missing_real_pi_task_evidence",
        "missing_transport_safety_evidence",
        "missing_vault_rotation_evidence",
    ]
    assert [item["preset_id"] for item in report["providers"]] == [
        "deepseek",
        "qwen",
    ]
    assert all(item["response_marker_ok"] for item in report["providers"])
    assert all(item["usage_status"] == "recorded" for item in report["providers"])
    serialized = report_path.read_text(encoding="utf-8")
    assert all(secret not in serialized for secret in provider_secrets)


def test_provider_smoke_records_ambiguous_timeout_as_outcome_unknown(tmp_path):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
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

    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
    )
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def timeout_after_send(*_args, **_kwargs):
        raise httpx.ReadTimeout("synthetic timeout")

    report = execute_qualification(
        db_path=database,
        manifest_path=manifest_path,
        output_path=report_path,
        relay_base_url="http://testserver",
        timeout_seconds=1800,
        relay_post=timeout_after_send,
        broker=broker,
    )

    assert report["providers"][0]["outcome"] == "outcome_unknown"
    assert report["providers"][0]["error_code"] == "relay_timeout"
    assert report["provider_chain_smoke_passed"] is False


def test_provider_smoke_preserves_real_broker_timeout_as_outcome_unknown(
    tmp_path,
):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    provider_timeout = {"enabled": False}

    def provider(_request: httpx.Request) -> httpx.Response:
        if provider_timeout["enabled"]:
            raise httpx.ReadTimeout("synthetic provider timeout")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"total_tokens": 1},
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
    )
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    provider_timeout["enabled"] = True
    app = FastAPI()
    app.include_router(model_relay.router)
    app.dependency_overrides[model_relay.get_connection_broker] = lambda: broker

    with TestClient(app) as relay_client:
        report = execute_qualification(
            db_path=database,
            manifest_path=manifest_path,
            output_path=report_path,
            relay_base_url="http://testserver",
            timeout_seconds=1800,
            relay_post=relay_client.post,
            broker=broker,
        )

    check = report["providers"][0]
    assert check["response_status"] == 502
    assert check["usage_status"] == "unknown"
    assert check["outcome"] == "outcome_unknown"
    assert check["error_code"] == "provider_outcome_unknown"


def test_provider_smoke_writes_fail_report_when_grant_or_revoke_fails(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "OK"}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
    )
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        broker,
        "issue_grant",
        lambda **_kwargs: (_ for _ in ()).throw(ConnectionError("grant race")),
    )

    report = execute_qualification(
        db_path=database,
        manifest_path=manifest_path,
        output_path=report_path,
        relay_base_url="http://testserver",
        timeout_seconds=1800,
        broker=broker,
    )

    assert report_path.is_file()
    assert report["providers"][0]["outcome"] == "failed"
    assert report["providers"][0]["error_code"] == "grant_or_connection_failed"


def test_provider_smoke_refuses_to_overwrite_existing_report(tmp_path):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    _create_inventory_database(database)
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek", "qwen"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(QualificationError, match="报告已存在"):
        execute_qualification(
            db_path=database,
            manifest_path=manifest_path,
            output_path=report_path,
            relay_base_url="http://testserver",
            timeout_seconds=1800,
        )


def test_provider_smoke_lock_is_stable_across_different_output_names(tmp_path):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    entered = threading.Event()
    release = threading.Event()
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
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
    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
    )
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def slow_post(*_args, **_kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return httpx.Response(200, json={})

    first_result = []

    def first_run():
        first_result.append(
            execute_qualification(
                db_path=database,
                manifest_path=manifest_path,
                output_path=tmp_path / "first.json",
                relay_base_url="http://testserver",
                timeout_seconds=1800,
                relay_post=slow_post,
                broker=broker,
            )
        )

    worker = threading.Thread(target=first_run)
    worker.start()
    assert entered.wait(timeout=10)
    try:
        with pytest.raises(QualificationError, match="已有相同 G4 烟测"):
            execute_qualification(
                db_path=database,
                manifest_path=manifest_path,
                output_path=tmp_path / "second.json",
                relay_base_url="http://testserver",
                timeout_seconds=1800,
                relay_post=lambda *_args, **_kwargs: httpx.Response(200),
                broker=broker,
            )
    finally:
        release.set()
        worker.join(timeout=10)

    assert len(first_result) == 1
    assert not worker.is_alive()


def test_provider_smoke_persists_attempt_before_egress_and_refuses_replay(
    tmp_path,
):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=(
                        {
                            "id": "response-test",
                            "object": "response",
                            "output_text": "G4_SYNTHETIC_OK",
                            "output": [],
                            "usage": {
                                "input_tokens": 1,
                                "output_tokens": 1,
                                "total_tokens": 2,
                            },
                    }
                    if request.url.path.endswith("/responses")
                    else {
                        "choices": [
                            {"message": {"content": "G4_SYNTHETIC_OK"}}
                        ],
                        "usage": {"total_tokens": 1},
                    }
                ),
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
    )
    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台百炼",
            preset_id="qwen",
            api_key="qwen-secret-for-test",
            model="qwen3.7-max-2026-06-08",
        )
    )
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek", "qwen"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    outbound_calls = 0
    app = FastAPI()
    app.include_router(model_relay.router)
    app.dependency_overrides[
        model_relay.get_connection_broker
    ] = lambda: broker

    def interrupted_post(*args, **kwargs):
        nonlocal outbound_calls
        outbound_calls += 1
        if outbound_calls == 2:
            raise KeyboardInterrupt("synthetic process interruption")
        return relay_client.post(*args, **kwargs)

    with TestClient(app) as relay_client:
        with pytest.raises(KeyboardInterrupt):
            execute_qualification(
                db_path=database,
                manifest_path=manifest_path,
                output_path=tmp_path / "first.json",
                relay_base_url="http://testserver",
                timeout_seconds=1800,
                relay_post=interrupted_post,
                broker=broker,
            )

    with pytest.raises(QualificationError, match="未决.*拒绝重复外发"):
        execute_qualification(
            db_path=database,
            manifest_path=manifest_path,
            output_path=tmp_path / "second.json",
            relay_base_url="http://testserver",
            timeout_seconds=1800,
            relay_post=interrupted_post,
            broker=broker,
        )

    with pytest.raises(QualificationError, match="台账完整性无效.*拒绝外发"):
        execute_qualification(
            db_path=database,
            manifest_path=manifest_path,
            output_path=tmp_path / "changed-context.json",
            relay_base_url="http://testserver",
            timeout_seconds=1801,
            relay_post=interrupted_post,
            broker=broker,
        )

    assert outbound_calls == 2


def test_ambiguous_retry_preserves_history_and_allows_only_one_retry(
    tmp_path,
) -> None:
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    execution_root = tmp_path / "execution"
    repository = ModelConnectionRepository(str(database))
    broker = ConnectionBroker(
        repository=repository,
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
    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
    )
    manifest = freeze_manifest(db_path=database, presets=["deepseek"])
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    provider = dict(manifest["providers"][0])
    old_context = {
        "git_commit": "old",
        "git_dirty": False,
        "relay_base_url": "http://127.0.0.1:8088/internal/model-relay",
        "timeout_seconds": 1800,
        "owner_user_id": "ordinary-user",
        "expected_commit": "old",
    }
    old_attempt_context = {
        "owner_user_id": "ordinary-user",
        "task_id": "old-task",
        "revision": 1,
        "relay_base_url": "http://127.0.0.1:8088/internal/model-relay",
        "execution_root": str(execution_root.resolve()),
        "source_sha256": "old-source-sha256",
    }
    new_context = {
        "git_commit": "new-commit",
        "git_dirty": False,
        "relay_base_url": "http://127.0.0.1:8088/internal/model-relay",
        "timeout_seconds": 1800,
        "owner_user_id": "ordinary-user",
        "expected_commit": "new-commit",
    }
    _, ledger_path = _qualification_state_paths(
        db_path=database,
        manifest_path=manifest_path,
        action="pi-provider",
    )
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps({
            "schema_version": "g4-provider-attempt-ledger-v1",
            "action": "pi-provider",
            "manifest_sha256": manifest["manifest_sha256"],
            "run_context_sha256": _canonical_sha256(old_context),
            "run_context": old_context,
            "providers": {
                (
                    f"{provider['connection_id']}:"
                    f"{provider['connection_version']}"
                ): {
                    "state": "in_progress",
                    "connection_id": provider["connection_id"],
                    "connection_version": provider["connection_version"],
                    "started_at": "2026-08-23T01:47:38+00:00",
                    "attempt_context": old_attempt_context,
                },
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(QualificationError, match="重复 Provider 请求和费用风险"):
        authorize_ambiguous_retry(
            db_path=database,
            manifest_path=manifest_path,
            owner_user_id="ordinary-user",
            connection_id=str(provider["connection_id"]),
            relay_base_url="http://127.0.0.1:8088/internal/model-relay",
            timeout_seconds=1800,
            expected_commit="new-commit",
            confirm_duplicate_request_and_cost=False,
            git_identity={"git_commit": "new-commit", "git_dirty": False},
        )

    with pytest.raises(QualificationError, match="缺少原始身份，拒绝重绑"):
        authorize_ambiguous_retry(
            db_path=database,
            manifest_path=manifest_path,
            owner_user_id="another-user",
            connection_id=str(provider["connection_id"]),
            relay_base_url="http://127.0.0.1:8088/internal/model-relay",
            timeout_seconds=1800,
            expected_commit="new-commit",
            confirm_duplicate_request_and_cost=True,
            git_identity={"git_commit": "new-commit", "git_dirty": False},
        )

    report = authorize_ambiguous_retry(
        db_path=database,
        manifest_path=manifest_path,
        owner_user_id="ordinary-user",
        connection_id=str(provider["connection_id"]),
        relay_base_url="http://127.0.0.1:8088/internal/model-relay",
        timeout_seconds=1800,
        expected_commit="new-commit",
        confirm_duplicate_request_and_cost=True,
        git_identity={"git_commit": "new-commit", "git_dirty": False},
    )

    assert report["user_confirmed_duplicate_request_and_cost"] is True
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = next(iter(ledger["providers"].values()))
    assert entry["state"] == "retry_authorized"
    assert entry["authorization"]["previous_state"] == "in_progress"
    assert ledger["run_context_sha256"] == _canonical_sha256(new_context)

    _, before_provider, _ = _attempt_ledger_callbacks(
        ledger_path=ledger_path,
        action="pi-provider",
        manifest=manifest,
        run_context=new_context,
    )
    attempt_context = {
        "owner_user_id": "ordinary-user",
        "task_id": "new-task",
        "revision": 1,
        "relay_base_url": "http://127.0.0.1:8088/internal/model-relay",
        "execution_root": str(execution_root.resolve()),
        "source_sha256": "source-sha256",
    }
    before_provider(provider, attempt_context)
    retried = json.loads(ledger_path.read_text(encoding="utf-8"))
    retried_entry = next(iter(retried["providers"].values()))
    assert retried_entry["state"] == "in_progress"
    assert retried_entry["attempt_context"] == attempt_context
    assert retried_entry["previous_attempts"][0]["state"] == "retry_authorized"

    retried_entry["state"] = "outcome_unknown"
    retried["providers"] = {
        next(iter(retried["providers"])): retried_entry,
    }
    ledger_path.write_text(json.dumps(retried), encoding="utf-8")
    with pytest.raises(QualificationError, match="恢复重试次数"):
        authorize_ambiguous_retry(
            db_path=database,
            manifest_path=manifest_path,
            owner_user_id="ordinary-user",
            connection_id=str(provider["connection_id"]),
            relay_base_url="http://127.0.0.1:8088/internal/model-relay",
            timeout_seconds=1800,
            expected_commit="new-commit",
            confirm_duplicate_request_and_cost=True,
            git_identity={"git_commit": "new-commit", "git_dirty": False},
        )


def test_ambiguous_retry_rejects_connection_outside_frozen_manifest(
    tmp_path,
) -> None:
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    _create_inventory_database(database)
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(QualificationError, match="连接不属于冻结清单"):
        authorize_ambiguous_retry(
            db_path=database,
            manifest_path=manifest_path,
            owner_user_id="ordinary-user",
            connection_id="connection-a",
            relay_base_url="http://127.0.0.1:8088/internal/model-relay",
            timeout_seconds=1800,
            expected_commit="new-commit",
            confirm_duplicate_request_and_cost=True,
            git_identity={"git_commit": "new-commit", "git_dirty": False},
        )


def test_pi_provider_chain_uses_standard_ordinary_owner_and_never_claims_g4(
    tmp_path,
):
    database = tmp_path / "webui.db"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "pi-report.json"
    execution_root = tmp_path / "execution"
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "OK"}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    asyncio.run(
        broker.configure_platform_preset(
            actor_user_id="super-admin",
            display_name="平台 DeepSeek",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
    )
    manifest_path.write_text(
        json.dumps(
            freeze_manifest(db_path=database, presets=["deepseek"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen_requests = []

    class FakeRuntime:
        async def start(self, request, *, on_event):
            seen_requests.append(request)
            grant = broker.issue_grant(
                owner_user_id=request.user_id,
                connection_id=request.model_connection_id,
                connection_version=request.model_connection_version,
                model_id=request.model_connection_model,
                task_id=request.task_id,
                revision=request.revision,
                run_id="pi_run_1234567890abcdef",
                purpose="agent_inference",
            )
            response = await broker.relay(
                grant_token=grant.token,
                protocol_path="chat/completions",
                method="POST",
                headers={"content-type": "application/json"},
                body=json.dumps(
                    {
                        "model": request.model_connection_model,
                        "messages": [{"role": "user", "content": "synthetic"}],
                    }
                ).encode("utf-8"),
            )
            _ = b"".join([chunk async for chunk in response.iter_bytes()])
            return SimpleNamespace(
                status=RuntimeStatus.CANDIDATE_READY,
                run_id="pi_run_1234567890abcdef",
                candidates=(SimpleNamespace(sha256="a" * 64, format="json"),),
                verification=SimpleNamespace(status=SimpleNamespace(value="passed")),
            )

    report = asyncio.run(
        execute_pi_provider_chain(
            db_path=database,
            manifest_path=manifest_path,
            output_path=report_path,
            execution_root=execution_root,
            relay_base_url="http://127.0.0.1:8088/internal/model-relay",
            timeout_seconds=1800,
            owner_user_id="g4-ordinary-synthetic-user",
            broker=broker,
            runtime_factory=lambda **_kwargs: FakeRuntime(),
        )
    )

    assert report["pi_provider_chain_passed"] is True
    assert report["g4_qualified"] is False
    assert report["qualification_blockers"] == [
        "missing_transport_safety_evidence",
        "missing_vault_rotation_evidence",
    ]
    assert seen_requests[0].permission_profile.value == "standard"
    assert seen_requests[0].external_api_confirmed is True
    assert seen_requests[0].user_id == "g4-ordinary-synthetic-user"
    assert seen_requests[0].model is None
    assert "deepseek-secret-for-test" not in report_path.read_text(encoding="utf-8")


def test_pi_provider_chain_rejects_external_relay_before_grant_issue(tmp_path):
    with pytest.raises(QualificationError, match="Relay 必须是本机地址"):
        asyncio.run(
            execute_pi_provider_chain(
                db_path=tmp_path / "webui.db",
                manifest_path=tmp_path / "manifest.json",
                output_path=tmp_path / "pi-report.json",
                execution_root=tmp_path / "execution",
                relay_base_url="https://relay.attacker.example/internal/model-relay",
                timeout_seconds=1800,
                owner_user_id="g4-ordinary-synthetic-user",
            )
        )


def test_transport_safety_report_binds_exact_tests_and_commit(tmp_path):
    report_path = tmp_path / "transport-safety.json"

    report = execute_transport_safety(
        output_path=report_path,
        expected_commit="a" * 40,
        git_identity={"git_commit": "a" * 40, "git_dirty": False},
        test_runner=lambda _command: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="6 passed",
            stderr="",
        ),
    )

    assert report["transport_safety_passed"] is True
    assert report["git_commit"] == "a" * 40
    assert report["test_count"] == 6
    assert "6 passed" not in report_path.read_text(encoding="utf-8")


def test_g4_assessment_requires_pi_transport_and_scoped_rotation_evidence(
    tmp_path,
):
    commit = "b" * 40
    manifest_sha = "c" * 64

    def write(name: str, payload: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    pi_report = write(
        "pi.json",
        {
            "schema_version": "g4-pi-provider-report-v1",
            "manifest_sha256": manifest_sha,
            "git_commit": commit,
            "git_dirty": False,
            "synthetic_egress_only": True,
            "code_identity_stable": True,
            "pi_provider_chain_passed": True,
            "providers": [
                {
                    "connection_id": "connection-deepseek",
                    "connection_version": "e" * 64,
                    "preset_id": "deepseek",
                    "model": "deepseek-v4-flash",
                    "api_format": "openai_chat_completions",
                    "permission_profile": "standard",
                    "owner_user_id": "g4-ordinary-user",
                    "outcome": "passed",
                    "runtime_status": "candidate_ready",
                    "usage_status": "recorded",
                    "candidate_count": 1,
                    "verification_status": "passed",
                    "pi_provider_chain_passed": True,
                }
            ],
        },
    )
    safety_report = write(
        "safety.json",
        {
            "schema_version": "g4-transport-safety-report-v1",
            "git_commit": commit,
            "git_dirty": False,
            "code_identity_stable": True,
            "pytest_returncode": 0,
            "test_count": 6,
            "test_ids": [
                "tests/test_g4_provider_safety_cli.py::test_provider_relay_pins_validated_ip_and_preserves_tls_identity",
                "tests/test_g4_provider_safety_cli.py::test_provider_relay_does_not_follow_redirect_or_repeat_dns",
                "tests/test_g4_provider_safety_cli.py::test_pinned_transport_enforces_original_tls_identity_and_lifetime[provider.test-False-True]",
                "tests/test_g4_provider_safety_cli.py::test_pinned_transport_enforces_original_tls_identity_and_lifetime[wrong-host.test-False-False]",
                "tests/test_g4_provider_safety_cli.py::test_pinned_transport_enforces_original_tls_identity_and_lifetime[provider.test-True-False]",
                "tests/test_g4_provider_safety_cli.py::test_keyring_atomic_replace_failure_removes_plaintext_temporary_file",
            ],
            "transport_safety_passed": True,
        },
    )
    rotation_report = write(
        "rotation.json",
        {
            "schema_version": "g4-vault-rotation-report-v2",
            "git_commit": commit,
            "git_dirty": False,
            "code_identity_stable": True,
            "phase": "finalized",
            "old_key_generation_retained": False,
            "key_backup_scope_verified": True,
            "backup_scope_kind": "configured_data_backups_root",
            "key_backup_scope": [
                {"root_name": "backups", "file_count": 2, "byte_count": 20}
            ],
            "verified_database_only_backups_unreadable_with_current_key": [
                {"file_name": "webui-before.db", "sha256": "d" * 64}
            ],
        },
    )
    output = tmp_path / "g4-final.json"
    manifest = write(
        "manifest.json",
        {
            "schema_version": "g4-provider-manifest-v1",
            "providers": [
                {
                    "connection_id": "connection-deepseek",
                    "connection_version": "e" * 64,
                    "preset_id": "deepseek",
                    "preset_version": "preset-v1",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash",
                    "api_format": "openai_chat_completions",
                }
            ],
            "manifest_sha256": manifest_sha,
        },
    )
    unsigned = json.loads(manifest.read_text(encoding="utf-8"))
    from scripts.verify_g4_provider_safety import _canonical_sha256

    unsigned["manifest_sha256"] = _canonical_sha256(
        {
            "schema_version": unsigned["schema_version"],
            "providers": unsigned["providers"],
        }
    )
    manifest_sha = unsigned["manifest_sha256"]
    manifest.write_text(json.dumps(unsigned), encoding="utf-8")
    pi_payload = json.loads(pi_report.read_text(encoding="utf-8"))
    pi_payload["manifest_sha256"] = manifest_sha
    pi_report.write_text(json.dumps(pi_payload), encoding="utf-8")

    result = assess_g4_evidence(
        manifest_path=manifest,
        pi_report_path=pi_report,
        transport_report_path=safety_report,
        rotation_report_path=rotation_report,
        output_path=output,
        expected_commit=commit,
        expected_manifest_sha256=manifest_sha,
    )

    assert result["g4_qualified"] is True
    assert result["qualification_blockers"] == []

    pi_payload = json.loads(pi_report.read_text(encoding="utf-8"))
    pi_payload["providers"] = []
    pi_report.write_text(json.dumps(pi_payload), encoding="utf-8")
    with pytest.raises(QualificationError, match="G4 证据不完整"):
        assess_g4_evidence(
            manifest_path=manifest,
            pi_report_path=pi_report,
            transport_report_path=safety_report,
            rotation_report_path=rotation_report,
            output_path=tmp_path / "g4-final-empty-providers.json",
            expected_commit=commit,
            expected_manifest_sha256=manifest_sha,
        )
    pi_payload["providers"] = [
        {
            "connection_id": "connection-deepseek",
            "connection_version": "e" * 64,
            "preset_id": "deepseek",
            "model": "deepseek-v4-flash",
            "api_format": "openai_chat_completions",
            "permission_profile": "standard",
            "owner_user_id": "g4-ordinary-user",
            "outcome": "passed",
            "runtime_status": "candidate_ready",
            "usage_status": "recorded",
            "candidate_count": 1,
            "verification_status": "passed",
            "pi_provider_chain_passed": True,
        }
    ]
    pi_report.write_text(json.dumps(pi_payload), encoding="utf-8")

    rotation_payload = json.loads(rotation_report.read_text(encoding="utf-8"))
    rotation_payload["key_backup_scope_verified"] = False
    rotation_report.write_text(json.dumps(rotation_payload), encoding="utf-8")
    with pytest.raises(QualificationError, match="G4 证据不完整"):
        assess_g4_evidence(
            manifest_path=manifest,
            pi_report_path=pi_report,
            transport_report_path=safety_report,
            rotation_report_path=rotation_report,
            output_path=tmp_path / "g4-final-fail.json",
            expected_commit=commit,
            expected_manifest_sha256=manifest_sha,
        )


def test_provider_relay_pins_validated_ip_and_preserves_tls_identity(tmp_path):
    observed: dict[str, object] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        if request.read().find(b'"messages"') >= 0:
            observed.update(
                url=str(request.url),
                host=request.headers.get("host"),
                sni_hostname=request.extensions.get("sni_hostname"),
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "OK"}}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(tmp_path / "webui.db")),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def scenario() -> None:
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
        binding = broker.freeze_connection(
            "user-a",
            str(connection["connection_id"]),
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=binding.connection_id,
            connection_version=binding.connection_version,
            task_id="g4-dns-pin",
            revision=1,
            run_id="g4-dns-pin-run",
            purpose="agent_inference",
        )
        response = await broker.relay(
            grant_token=grant.token,
            protocol_path="chat/completions",
            method="POST",
            headers={"content-type": "application/json"},
            body=json.dumps(
                {
                    "model": binding.model,
                    "messages": [{"role": "user", "content": "synthetic"}],
                }
            ).encode("utf-8"),
        )
        _ = b"".join([chunk async for chunk in response.iter_bytes()])

    asyncio.run(scenario())

    assert observed == {
        "url": "https://8.8.8.8/chat/completions",
        "host": "api.deepseek.com",
        "sni_hostname": "api.deepseek.com",
    }


def test_provider_relay_does_not_follow_redirect_or_repeat_dns(tmp_path):
    calls = []
    resolver_calls = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = json.loads(request.read().decode("utf-8"))
        if body.get("messages") != []:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "OK"}}],
                    "usage": {"total_tokens": 1},
                },
            )
        return httpx.Response(
            302,
            headers={"location": "https://attacker.example/collect"},
        )

    def resolver(host: str) -> list[str]:
        resolver_calls.append(host)
        return ["8.8.8.8"]

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(tmp_path / "webui.db")),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=resolver,
    )

    async def scenario() -> int:
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="deepseek-secret-for-test",
            model="deepseek-v4-flash",
        )
        binding = broker.freeze_connection(
            "user-a",
            str(connection["connection_id"]),
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=binding.connection_id,
            connection_version=binding.connection_version,
            task_id="g4-no-redirect",
            revision=1,
            run_id="g4-no-redirect-run",
            purpose="agent_inference",
        )
        response = await broker.relay(
            grant_token=grant.token,
            protocol_path="chat/completions",
            method="POST",
            headers={"content-type": "application/json"},
            body=json.dumps(
                {"model": binding.model, "messages": []}
            ).encode("utf-8"),
        )
        return response.status_code

    # 两次连接验证 + 一次 Relay，各请求只允许一次预检解析，绝不解析重定向目标。
    assert asyncio.run(scenario()) == 302
    assert len(calls) == len(resolver_calls)
    assert all(host == "api.deepseek.com" for host in resolver_calls)
    assert all("attacker.example" not in url for url in calls)


def _certificate_material(
    tmp_path: Path,
    *,
    dns_name: str,
    expired: bool,
) -> tuple[Path, Path, Path]:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "G4 Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_name)])
    not_before = now - timedelta(days=3 if expired else 1)
    not_after = now - timedelta(days=1) if expired else now + timedelta(days=2)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns_name)]), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()
            ),
            False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / f"ca-{dns_name}-{expired}.pem"
    cert_path = tmp_path / f"leaf-{dns_name}-{expired}.pem"
    key_path = tmp_path / f"leaf-{dns_name}-{expired}.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


@pytest.mark.parametrize(
    ("certificate_name", "expired", "should_pass"),
    [
        ("provider.test", False, True),
        ("wrong-host.test", False, False),
        ("provider.test", True, False),
    ],
)
def test_pinned_transport_enforces_original_tls_identity_and_lifetime(
    tmp_path,
    certificate_name,
    expired,
    should_pass,
):
    async def scenario() -> None:
        ca_path, cert_path, key_path = _certificate_material(
            tmp_path,
            dns_name=certificate_name,
            expired=expired,
        )
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert_path, key_path)

        async def handler(reader, writer):
            try:
                await reader.read(4096)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                    b"Connection: close\r\n\r\nOK"
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(
            handler,
            "127.0.0.1",
            0,
            ssl=server_context,
        )
        port = server.sockets[0].getsockname()[1]
        client_context = ssl.create_default_context(cafile=str(ca_path))
        target = ValidatedTarget(
            url=f"https://provider.test:{port}/v1/check",
            scheme="https",
            host="provider.test",
            port=port,
            ips=(str(ipaddress.ip_address("127.0.0.1")),),
        )
        transport = PinnedAsyncHTTPTransport(
            target=target,
            transport=httpx.AsyncHTTPTransport(verify=client_context),
        )
        try:
            async with httpx.AsyncClient(transport=transport) as client:
                if should_pass:
                    response = await client.get(target.url)
                    assert response.status_code == 200
                else:
                    with pytest.raises(httpx.ConnectError):
                        await client.get(target.url)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_two_phase_vault_rotation_keeps_live_secret_and_erases_database_backup(
    tmp_path,
):
    database = tmp_path / "webui.db"
    key_backup_root = tmp_path / "backups"
    key_backup_root.mkdir()
    backup = key_backup_root / "webui-before-rotation.db"
    key_path = tmp_path / "webui.db.model-connections.key"
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "OK"}}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
    )
    initial_broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
        vault=FernetCredentialVault.from_key_file(key_path),
        transport=transport,
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def configure() -> dict[str, object]:
        return await initial_broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="deepseek-secret-for-rotation",
            model="deepseek-v4-flash",
        )

    connection = asyncio.run(configure())
    shutil.copy2(database, backup)

    assert prepare_vault_rotation(
        db_path=database,
        key_path=key_path,
        backend_stopped_check=lambda: True,
    ) == 1
    transitional_vault = FernetCredentialVault.from_key_file(key_path)
    assert transitional_vault.has_inactive_keys is True
    # 准备阶段停服执行并保留旧代际，重启后可同时读取两代密文。
    with sqlite3.connect(backup) as connection_db:
        old_ciphertext = connection_db.execute(
            "SELECT ciphertext FROM model_connection_secrets"
        ).fetchone()[0]
    assert transitional_vault.decrypt(old_ciphertext) == (
        "deepseek-secret-for-rotation"
    )

    (
        finalized_count,
        key_backup_scope,
        database_backup_evidence,
    ) = finalize_vault_rotation(
        db_path=database,
        key_path=key_path,
        backend_stopped_check=lambda: True,
        key_backup_roots=[key_backup_root],
        database_backup_paths=[backup],
    )
    assert finalized_count == 1
    assert key_backup_scope == [
        {
            "root_name": "backups",
            "file_count": 1,
            "byte_count": backup.stat().st_size,
        }
    ]
    assert database_backup_evidence[0]["file_name"] == backup.name
    current_vault = FernetCredentialVault.from_key_file(key_path)
    assert current_vault.has_inactive_keys is False
    live_broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(database)),
        vault=current_vault,
        transport=transport,
        resolver=lambda _host: ["8.8.8.8"],
    )
    old_backup_broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(backup)),
        vault=current_vault,
        transport=transport,
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def relay_once(broker: ConnectionBroker, task_id: str) -> bytes:
        binding = broker.freeze_connection(
            "user-a",
            str(connection["connection_id"]),
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=binding.connection_id,
            connection_version=binding.connection_version,
            task_id=task_id,
            revision=1,
            run_id=f"{task_id}-run",
            purpose="agent_inference",
        )
        response = await broker.relay(
            grant_token=grant.token,
            protocol_path="chat/completions",
            method="POST",
            headers={"content-type": "application/json"},
            body=json.dumps(
                {"model": binding.model, "messages": []}
            ).encode("utf-8"),
        )
        return b"".join([chunk async for chunk in response.iter_bytes()])

    assert b'"content":"OK"' in asyncio.run(
        relay_once(live_broker, "g4-live-after-rotation")
    )
    with pytest.raises(ConnectionError, match="凭证密文无法解密"):
        asyncio.run(relay_once(old_backup_broker, "g4-old-backup"))


def test_vault_rotation_rejects_concurrent_maintenance(tmp_path, monkeypatch):
    database = tmp_path / "webui.db"
    key_path = tmp_path / "webui.db.model-connections.key"
    repository = ModelConnectionRepository(str(database))
    FernetCredentialVault.from_key_file(key_path)
    entered = threading.Event()
    release = threading.Event()
    first_call = [True]
    original = ModelConnectionRepository.reencrypt_all_secrets

    def slow_reencrypt(self, transform):
        if first_call[0]:
            first_call[0] = False
            entered.set()
            assert release.wait(timeout=10)
        return original(self, transform)

    monkeypatch.setattr(
        ModelConnectionRepository,
        "reencrypt_all_secrets",
        slow_reencrypt,
    )
    first_result: list[object] = []

    def first_rotation() -> None:
        try:
            first_result.append(
                prepare_vault_rotation(
                    db_path=database,
                    key_path=key_path,
                    backend_stopped_check=lambda: True,
                )
            )
        except BaseException as exc:
            first_result.append(exc)

    worker = threading.Thread(target=first_rotation)
    worker.start()
    assert entered.wait(timeout=10)
    try:
        with pytest.raises(QualificationError, match="已有密钥轮换正在执行"):
            prepare_vault_rotation(
                db_path=database,
                key_path=key_path,
                backend_stopped_check=lambda: True,
            )
    finally:
        release.set()
        worker.join(timeout=10)

    assert first_result == [0]
    assert not worker.is_alive()


def test_vault_rotation_prepare_refuses_while_backend_is_running(tmp_path):
    database = tmp_path / "webui.db"
    key_path = tmp_path / "webui.db.model-connections.key"
    ModelConnectionRepository(str(database))
    FernetCredentialVault.from_key_file(key_path)
    key_before = key_path.read_bytes()

    with pytest.raises(QualificationError, match="8088.*停服"):
        prepare_vault_rotation(
            db_path=database,
            key_path=key_path,
            backend_stopped_check=lambda: False,
        )

    assert key_path.read_bytes() == key_before


def test_vault_rotation_finalization_refuses_while_backend_is_running(tmp_path):
    database = tmp_path / "webui.db"
    key_path = tmp_path / "webui.db.model-connections.key"
    ModelConnectionRepository(str(database))
    FernetCredentialVault.from_key_file(key_path)
    prepare_vault_rotation(
        db_path=database,
        key_path=key_path,
        backend_stopped_check=lambda: True,
    )
    key_backup_root = tmp_path / "backups"
    key_backup_root.mkdir()

    with pytest.raises(QualificationError, match="8088.*停服"):
        finalize_vault_rotation(
            db_path=database,
            key_path=key_path,
            backend_stopped_check=lambda: False,
            key_backup_roots=[key_backup_root],
            database_backup_paths=[],
        )

    assert FernetCredentialVault.from_key_file(key_path).has_inactive_keys is True


def test_vault_rotation_finalization_rejects_old_keyring_backup(tmp_path):
    database = tmp_path / "webui.db"
    key_path = tmp_path / "webui.db.model-connections.key"
    key_backup_root = tmp_path / "backups"
    key_backup_root.mkdir()
    ModelConnectionRepository(str(database))
    FernetCredentialVault.from_key_file(key_path)
    prepare_vault_rotation(
        db_path=database,
        key_path=key_path,
        backend_stopped_check=lambda: True,
    )
    shutil.copy2(key_path, key_backup_root / "old-keyring.bak")

    with pytest.raises(QualificationError, match="仍含旧 key/keyring"):
        finalize_vault_rotation(
            db_path=database,
            key_path=key_path,
            backend_stopped_check=lambda: True,
            key_backup_roots=[key_backup_root],
            database_backup_paths=[],
        )

    assert FernetCredentialVault.from_key_file(key_path).has_inactive_keys is True


def test_vault_rotation_streams_large_old_keyring_backup(tmp_path):
    database = tmp_path / "webui.db"
    key_path = tmp_path / "webui.db.model-connections.key"
    key_backup_root = tmp_path / "backups"
    key_backup_root.mkdir()
    ModelConnectionRepository(str(database))
    FernetCredentialVault.from_key_file(key_path)
    prepare_vault_rotation(
        db_path=database,
        key_path=key_path,
        backend_stopped_check=lambda: True,
    )
    large_backup = key_backup_root / "large-old-keyring.bak"
    with large_backup.open("wb") as handle:
        handle.seek(65 * 1024 * 1024 - 17)
        handle.write(key_path.read_bytes())

    with pytest.raises(QualificationError, match="仍含旧 key/keyring"):
        finalize_vault_rotation(
            db_path=database,
            key_path=key_path,
            backend_stopped_check=lambda: True,
            key_backup_roots=[key_backup_root],
            database_backup_paths=[],
        )

    assert large_backup.stat().st_size > 64 * 1024 * 1024
    assert FernetCredentialVault.from_key_file(key_path).has_inactive_keys is True


def test_keyring_atomic_replace_failure_removes_plaintext_temporary_file(
    tmp_path,
    monkeypatch,
):
    key_path = tmp_path / "webui.db.model-connections.key"
    vault = FernetCredentialVault.from_key_file(key_path)
    original_replace = Path.replace

    def fail_keyring_replace(path: Path, target: Path):
        if target == key_path and path.suffix == ".tmp":
            raise OSError("synthetic replace failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_keyring_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        vault.begin_rotation()

    assert list(tmp_path.glob(f".{key_path.name}.*.tmp")) == []


def test_rotate_vault_cli_requires_irreversible_maintenance_confirmation(
    tmp_path,
):
    database = tmp_path / "webui.db"
    key_path = tmp_path / "webui.db.model-connections.key"
    report = tmp_path / "rotation-report.json"
    ModelConnectionRepository(str(database))
    FernetCredentialVault.from_key_file(key_path)
    key_before = key_path.read_bytes()

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "rotate-vault",
            "--phase",
            "finalize",
            "--db-path",
            str(database),
            "--key-path",
            str(key_path),
            "--expected-key-sha256",
            "0" * 64,
            "--expected-commit",
            "0" * 40,
            "--output",
            str(report),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 2
    assert "维护窗口" in completed.stderr
    assert key_path.read_bytes() == key_before
    assert not report.exists()
