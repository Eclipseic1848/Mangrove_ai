from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import shutil
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from src.api.auth import get_current_user
from src.api.routes import config_routes
from src.api.cookie_health_scanner import CookieHealthScanner
from src.api.store import WebUIStore
from src.config import runtime_config
from src.config.secret_refs import (
    RUNTIME_CONFIG_SECRET_KEYS,
    SecretRefResolutionError,
    load_or_create_vault,
    load_vault,
    scan_artifacts_for_plaintext_secrets,
)
from src.config.settings import settings
from src.database_migrations.alembic.versions import webui_0004
from tests.database_migration_helpers import migrated_webui_database


def _store(tmp_path: Path) -> tuple[WebUIStore, Path]:
    database = migrated_webui_database(tmp_path / "webui.db")
    return WebUIStore(str(database)), database


def _upgrade(database: Path, revision: str) -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(
            Path(__file__).parents[1]
            / "src"
            / "database_migrations"
            / "alembic"
        ),
    )
    engine = create_engine(URL.create("sqlite", database=str(database)))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            config.attributes["backup_sha256"] = "a" * 64
            command.upgrade(config, revision)
            connection.commit()
    finally:
        engine.dispose()


def test_webui_0004_freezes_secret_keys_and_does_not_import_runtime_helpers() -> None:
    revision_source = Path(webui_0004.__file__).read_text(encoding="utf-8")

    assert webui_0004._RUNTIME_CONFIG_SECRET_KEYS == RUNTIME_CONFIG_SECRET_KEYS
    assert "src.config.secret_refs" not in revision_source


def test_secret_config_write_persists_only_opaque_ref_and_ciphertext(
    tmp_path: Path,
) -> None:
    store, database = _store(tmp_path)

    store.config_set(
        "user-a",
        "deepseek_api_key",
        "runtime-secret-user-a-7788",
        "user-a",
    )

    assert store.config_all("user-a")["deepseek_api_key"] == (
        "runtime-secret-user-a-7788"
    )
    with sqlite3.connect(database) as connection:
        stored_value = connection.execute(
            "SELECT value FROM runtime_config "
            "WHERE scope='user-a' AND key='deepseek_api_key'"
        ).fetchone()[0]
        secret_row = connection.execute(
            "SELECT owner_scope, ciphertext FROM runtime_config_secrets"
        ).fetchone()
    assert stored_value.startswith("secretref:runtime-config:")
    assert secret_row[0] == "user-a"
    assert "runtime-secret-user-a-7788" not in secret_row[1]
    assert "runtime-secret-user-a-7788" not in database.read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_secret_config_update_atomically_replaces_old_ciphertext(
    tmp_path: Path,
) -> None:
    store, database = _store(tmp_path)
    store.config_set("global", "smtp_password", "first-secret", "admin-a")

    store.config_set("global", "smtp_password", "second-secret", "admin-a")

    assert store.config_all("global")["smtp_password"] == "second-secret"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT owner_scope, ciphertext FROM runtime_config_secrets"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "global"
    assert "first-secret" not in rows[0][1]
    assert "second-secret" not in rows[0][1]


def test_secret_config_update_rejects_old_ref_owned_by_another_scope(
    tmp_path: Path,
) -> None:
    store, database = _store(tmp_path)
    store.config_set("user-a", "deepseek_api_key", "owner-a-secret", "user-a")
    with sqlite3.connect(database) as connection:
        owner_a_ref = connection.execute(
            "SELECT value FROM runtime_config "
            "WHERE scope='user-a' AND key='deepseek_api_key'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO runtime_config "
            "(scope, key, value, updated_at, updated_by) VALUES "
            "('user-b', 'deepseek_api_key', ?, '2026-08-26T00:00:00', 'user-b')",
            (owner_a_ref,),
        )

    with pytest.raises(SecretRefResolutionError, match="SecretRef 无法解析"):
        store.config_set("user-b", "deepseek_api_key", "owner-b-secret", "user-b")

    with sqlite3.connect(database) as connection:
        user_b_value = connection.execute(
            "SELECT value FROM runtime_config "
            "WHERE scope='user-b' AND key='deepseek_api_key'"
        ).fetchone()[0]
        secret_count = connection.execute(
            "SELECT COUNT(*) FROM runtime_config_secrets"
        ).fetchone()[0]
    assert user_b_value == owner_a_ref
    assert secret_count == 1
    assert store.config_all("user-a")["deepseek_api_key"] == "owner-a-secret"


@pytest.mark.parametrize("action", ["update", "delete"])
@pytest.mark.parametrize(
    "corruption",
    ["cross_key", "unknown_ref", "bad_ciphertext", "missing_key"],
)
def test_secret_config_writes_fail_closed_on_untrusted_existing_secret(
    tmp_path: Path,
    action: str,
    corruption: str,
) -> None:
    store, database = _store(tmp_path)
    store.config_set("global", "smtp_password", "original-secret", "admin-a")
    with sqlite3.connect(database) as connection:
        if corruption == "cross_key":
            store.config_set(
                "global", "deepseek_api_key", "other-key-secret", "admin-a"
            )
            other_ref = connection.execute(
                "SELECT value FROM runtime_config "
                "WHERE scope='global' AND key='deepseek_api_key'"
            ).fetchone()[0]
            connection.execute(
                "UPDATE runtime_config SET value=? "
                "WHERE scope='global' AND key='smtp_password'",
                (other_ref,),
            )
        elif corruption == "unknown_ref":
            connection.execute(
                "UPDATE runtime_config SET value=? "
                "WHERE scope='global' AND key='smtp_password'",
                ("secretref:runtime-config:00000000-0000-0000-0000-000000000000",),
            )
        elif corruption == "bad_ciphertext":
            connection.execute(
                "UPDATE runtime_config_secrets SET ciphertext='broken-ciphertext' "
                "WHERE owner_scope='global' AND config_key='smtp_password'"
            )
    if corruption == "missing_key":
        database.with_name(f"{database.name}.model-connections.key").unlink()

    with sqlite3.connect(database) as connection:
        before_value = connection.execute(
            "SELECT value FROM runtime_config "
            "WHERE scope='global' AND key='smtp_password'"
        ).fetchone()[0]
        before_count = connection.execute(
            "SELECT COUNT(*) FROM runtime_config_secrets"
        ).fetchone()[0]

    with pytest.raises(SecretRefResolutionError):
        if action == "update":
            store.config_set("global", "smtp_password", "replacement", "admin-a")
        else:
            store.config_delete("global", "smtp_password")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM runtime_config "
            "WHERE scope='global' AND key='smtp_password'"
        ).fetchone()[0] == before_value
        assert connection.execute(
            "SELECT COUNT(*) FROM runtime_config_secrets"
        ).fetchone()[0] == before_count


def test_secret_ref_cannot_be_resolved_by_another_owner(tmp_path: Path) -> None:
    store, database = _store(tmp_path)
    store.config_set("user-a", "deepseek_api_key", "owner-a-secret", "user-a")
    with sqlite3.connect(database) as connection:
        secret_ref = connection.execute(
            "SELECT value FROM runtime_config "
            "WHERE scope='user-a' AND key='deepseek_api_key'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO runtime_config "
            "(scope, key, value, updated_at, updated_by) VALUES "
            "('user-b', 'deepseek_api_key', ?, '2026-08-26T00:00:00', 'user-b')",
            (secret_ref,),
        )

    with pytest.raises(SecretRefResolutionError, match="SecretRef 无法解析"):
        store.config_all("user-b")


def test_unknown_secret_ref_fails_closed_without_echoing_ref(tmp_path: Path) -> None:
    store, database = _store(tmp_path)
    unknown_ref = "secretref:runtime-config:00000000-0000-0000-0000-000000000000"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO runtime_config "
            "(scope, key, value, updated_at, updated_by) VALUES "
            "('global', 'smtp_password', ?, '2026-08-26T00:00:00', 'admin-a')",
            (unknown_ref,),
        )

    with pytest.raises(SecretRefResolutionError) as failure:
        store.config_all("global")
    assert unknown_ref not in str(failure.value)


def test_unavailable_vault_fails_closed_without_echoing_secret(
    tmp_path: Path,
) -> None:
    store, database = _store(tmp_path)
    store.config_set("global", "smtp_password", "vault-secret-9911", "admin-a")
    key_path = database.with_name(f"{database.name}.model-connections.key")
    key_path.write_text("broken-keyring", encoding="utf-8")

    with pytest.raises(SecretRefResolutionError) as failure:
        store.config_all("global")
    assert "vault-secret-9911" not in str(failure.value)


def test_missing_vault_is_not_silently_recreated_on_read(tmp_path: Path) -> None:
    store, database = _store(tmp_path)
    store.config_set("global", "smtp_password", "lost-vault-secret", "admin-a")
    key_path = database.with_name(f"{database.name}.model-connections.key")
    key_path.unlink()

    with pytest.raises(SecretRefResolutionError, match="Vault 不可用"):
        store.config_all("global")

    assert not key_path.exists()


def test_missing_shared_vault_is_not_recreated_when_model_ciphertext_exists(
    tmp_path: Path,
) -> None:
    store, database = _store(tmp_path)
    vault = load_or_create_vault(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO model_connection_secrets "
            "(secret_id, owner_user_id, ciphertext, created_at) VALUES "
            "('model-secret-a', 'user-a', ?, '2026-08-26T00:00:00')",
            (vault.encrypt("model-secret"),),
        )
    key_path = database.with_name(f"{database.name}.model-connections.key")
    key_path.unlink()

    with pytest.raises(SecretRefResolutionError, match="Vault 不可用"):
        store.config_set("global", "smtp_password", "new-secret", "admin-a")

    assert not key_path.exists()
    assert store.config_all("global") == {}


def test_secret_config_delete_removes_ref_and_ciphertext(tmp_path: Path) -> None:
    store, database = _store(tmp_path)
    store.config_set("global", "slack_webhook_url", "https://secret.invalid", "admin-a")

    store.config_delete("global", "slack_webhook_url")

    assert "slack_webhook_url" not in store.config_all("global")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runtime_config_secrets"
        ).fetchone()[0] == 0


def test_non_secret_config_keeps_plain_runtime_value(tmp_path: Path) -> None:
    store, database = _store(tmp_path)

    store.config_set("global", "smtp_host", "smtp.example.test", "admin-a")

    assert store.config_all("global")["smtp_host"] == "smtp.example.test"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM runtime_config "
            "WHERE scope='global' AND key='smtp_host'"
        ).fetchone()[0] == "smtp.example.test"
        assert connection.execute(
            "SELECT COUNT(*) FROM runtime_config_secrets"
        ).fetchone()[0] == 0


def test_registry_secret_flags_match_frozen_migration_keys() -> None:
    assert {
        key for key, metadata in runtime_config.REGISTRY.items()
        if metadata.get("secret") is True
    } == set(RUNTIME_CONFIG_SECRET_KEYS)


def test_webui_0004_migrates_legacy_plaintext_and_backup_has_no_plaintext(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    _upgrade(database, "webui_0003")
    shared_vault = load_or_create_vault(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO model_connection_secrets "
            "(secret_id, owner_user_id, ciphertext, created_at) VALUES "
            "('model-secret-a', 'user-a', ?, '2026-08-26T00:00:00')",
            (shared_vault.encrypt("synthetic-provider-secret"),),
        )
        connection.executemany(
            "INSERT INTO runtime_config "
            "(scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "global", "smtp_password", "legacy-smtp-secret-4400",
                    "2026-08-26T00:00:00", "admin-a",
                ),
                (
                    "user-a", "deepseek_api_key", "legacy-user-secret-5500",
                    "2026-08-26T00:00:00", "user-a",
                ),
            ],
        )

    legacy_backup = tmp_path / "webui-before-secretref.db"
    shutil.copy2(database, legacy_backup)
    _upgrade(database, "webui_0004")
    backup = tmp_path / "webui-after-secretref.db"
    shutil.copy2(database, backup)
    # 先冻结 webui_0004 专属备份，再升到当前头供 Repository 失败关闭门使用。
    _upgrade(database, "webui_0010")

    store = WebUIStore(str(database))
    assert store.config_all("global")["smtp_password"] == "legacy-smtp-secret-4400"
    assert store.config_all("user-a")["deepseek_api_key"] == (
        "legacy-user-secret-5500"
    )
    for artifact in (database, backup):
        content = artifact.read_bytes().decode("utf-8", errors="ignore")
        assert "legacy-smtp-secret-4400" not in content
        assert "legacy-user-secret-5500" not in content
    scans = scan_artifacts_for_plaintext_secrets(
        database,
        (database, backup, legacy_backup),
    )
    assert [(item.artifact_name, item.exposed_secret_count) for item in scans] == [
        ("webui.db", 0),
        ("webui-after-secretref.db", 0),
        ("webui-before-secretref.db", 2),
    ]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("webui_0010",)
        values = {
            row[0]
            for row in connection.execute(
                "SELECT value FROM runtime_config "
                "WHERE key IN ('smtp_password', 'deepseek_api_key')"
            )
        }
        assert all(value.startswith("secretref:runtime-config:") for value in values)
        runtime_ciphertext = connection.execute(
            "SELECT ciphertext FROM runtime_config_secrets "
            "WHERE owner_scope='user-a' AND config_key='deepseek_api_key'"
        ).fetchone()[0]
        model_ciphertext = connection.execute(
            "SELECT ciphertext FROM model_connection_secrets "
            "WHERE secret_id='model-secret-a'"
        ).fetchone()[0]
    assert shared_vault.decrypt(runtime_ciphertext) == "legacy-user-secret-5500"
    assert shared_vault.decrypt(model_ciphertext) == "synthetic-provider-secret"


def test_webui_0004_secure_delete_removes_long_legacy_payload(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    _upgrade(database, "webui_0003")
    long_secret = "legacy-overflow-secret-" + ("x" * 16_384)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO runtime_config "
            "(scope, key, value, updated_at, updated_by) VALUES "
            "('global', 'smtp_password', ?, '2026-08-26T00:00:00', 'admin-a')",
            (long_secret,),
        )

    _upgrade(database, "webui_0004")

    assert long_secret.encode("utf-8") not in database.read_bytes()


def test_webui_0004_bad_vault_rolls_back_without_partial_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    _upgrade(database, "webui_0003")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO runtime_config "
            "(scope, key, value, updated_at, updated_by) VALUES "
            "('global', 'smtp_password', 'legacy-kept-secret', "
            "'2026-08-26T00:00:00', 'admin-a')"
        )
    database.with_name(f"{database.name}.model-connections.key").write_text(
        "broken-keyring",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Vault 不可用"):
        _upgrade(database, "webui_0004")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("webui_0003",)
        assert connection.execute(
            "SELECT value FROM runtime_config WHERE key='smtp_password'"
        ).fetchone() == ("legacy-kept-secret",)
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='runtime_config_secrets'"
        ).fetchone()
        if table is not None:
            assert connection.execute(
                "SELECT COUNT(*) FROM runtime_config_secrets"
            ).fetchone()[0] == 0


def test_plaintext_scanner_is_read_only_and_fails_closed_on_bad_ciphertext(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(SecretRefResolutionError, match="扫描源不可用"):
        scan_artifacts_for_plaintext_secrets(missing, (missing,))
    assert not missing.exists()

    store, database = _store(tmp_path)
    store.config_set("global", "smtp_password", "scan-secret", "admin-a")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE runtime_config_secrets SET ciphertext='broken-ciphertext'"
        )
    with pytest.raises(SecretRefResolutionError, match="扫描无法解密"):
        scan_artifacts_for_plaintext_secrets(database, (database,))


def test_plaintext_scanner_documented_module_cli_runs_in_isolated_directory(
    tmp_path: Path,
) -> None:
    store, database = _store(tmp_path)
    store.config_set("global", "smtp_password", "scan-cli-secret", "admin-a")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.scan_runtime_config_secrets",
            "--database",
            str(database),
            "--artifact",
            str(database),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "artifacts": [
            {
                "artifact_name": "webui.db",
                "exposed_secret_count": 0,
            }
        ],
        "outcome": "passed",
    }


def test_webui_0004_interrupted_second_write_rolls_back_first_secret(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    _upgrade(database, "webui_0003")
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO runtime_config "
            "(scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
            [
                ("global", "deepseek_api_key", "first-legacy-secret", "now", "a"),
                ("global", "qwen_api_key", "second-legacy-secret", "now", "a"),
            ],
        )
        connection.execute(
            "CREATE TRIGGER fail_second_secret BEFORE UPDATE OF value ON runtime_config "
            "WHEN OLD.key='qwen_api_key' BEGIN "
            "SELECT RAISE(ABORT, 'synthetic interruption'); END"
        )

    with pytest.raises(Exception, match="synthetic interruption"):
        _upgrade(database, "webui_0004")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("webui_0003",)
        assert connection.execute(
            "SELECT key, value FROM runtime_config "
            "WHERE key IN ('deepseek_api_key', 'qwen_api_key') ORDER BY key"
        ).fetchall() == [
            ("deepseek_api_key", "first-legacy-secret"),
            ("qwen_api_key", "second-legacy-secret"),
        ]
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='runtime_config_secrets'"
        ).fetchone()
        if table is not None:
            assert connection.execute(
                "SELECT COUNT(*) FROM runtime_config_secrets"
            ).fetchone()[0] == 0


def test_config_api_masks_secret_and_diagnostic_error(tmp_path: Path, monkeypatch) -> None:
    store, _database = _store(tmp_path)
    store.config_set("global", "smtp_password", "api-secret-6600", "admin-a")
    monkeypatch.setattr(config_routes, "get_store", lambda: store)
    monkeypatch.setattr(settings, "smtp_password", "api-secret-6600")

    async def fail_with_secret(_target: str) -> str:
        raise RuntimeError("SMTP rejected api-secret-6600")

    monkeypatch.setattr(config_routes, "_verify_target", fail_with_secret)
    app = FastAPI()
    app.include_router(config_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": "admin",
    }
    client = TestClient(app)

    listed = client.get("/api/config")
    verified = client.post("/api/config/verify", json={"target": "smtp"})

    assert listed.status_code == 200
    assert "api-secret-6600" not in listed.text
    assert "····6600" in listed.text
    assert verified.status_code == 200
    assert "api-secret-6600" not in verified.text
    assert verified.json() == {"ok": False, "detail": "SMTP rejected [SECRET]"}


def test_runtime_config_log_redacts_secret_from_reload_failure(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    from src.llm import provider

    store, _database = _store(tmp_path)
    monkeypatch.setattr(
        provider,
        "reload_provider",
        lambda: (_ for _ in ()).throw(
            RuntimeError("reload rejected log-secret-7700")
        ),
    )

    with caplog.at_level(logging.WARNING):
        runtime_config.set_global(
            store,
            "deepseek_api_key",
            "log-secret-7700",
            "admin-a",
        )

    assert "log-secret-7700" not in caplog.text
    assert "reload rejected [SECRET]" in caplog.text


def test_cookie_health_outer_loop_log_does_not_echo_exception_secret(
    monkeypatch,
    caplog,
) -> None:
    scanner = CookieHealthScanner()
    synthetic_secret = "outer-loop-secret-9911"
    previous_enabled = settings.cookie_health_scan_enabled

    async def fail_once() -> None:
        scanner._stop.set()
        raise RuntimeError(f"scanner failed with {synthetic_secret}")

    monkeypatch.setattr(scanner, "_run_one_scan", fail_once)
    monkeypatch.setattr(
        runtime_config,
        "redact_sensitive_text",
        lambda value: value.replace(synthetic_secret, "[SECRET]"),
    )
    settings.cookie_health_scan_enabled = True
    try:
        with caplog.at_level(logging.ERROR):
            asyncio.run(scanner._loop())
    finally:
        settings.cookie_health_scan_enabled = previous_enabled

    assert synthetic_secret not in caplog.text
    assert "RuntimeError" in caplog.text
    assert "[SECRET]" in caplog.text
