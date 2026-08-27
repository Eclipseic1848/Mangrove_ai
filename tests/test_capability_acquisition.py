# -*- coding: utf-8 -*-
"""AC-05：只通过 acquire/cancel Interface 验证获取状态机。"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
from pydantic import ValidationError

from src.capability_acquisition import (
    AcquisitionCandidate,
    AcquisitionRequest,
    AcquisitionSourceKind,
    CapabilityAcquisition,
    DockerBuildkitAcquisitionEnvironment,
    InMemoryAcquisitionRepository,
    PreparedCapability,
    ResolvedCandidate,
    SourcePolicy,
    SqliteAcquisitionRepository,
    migrate_capability_acquisition,
)
from src.conversation_steering import AcquisitionBudget, AcquisitionStatus


def _budget(**changes) -> AcquisitionBudget:
    values = {
        "max_duration_seconds": 10,
        "max_download_bytes": 1_000_000,
        "max_unpacked_bytes": 2_000_000,
        "max_candidates": 3,
        "max_retries_per_source": 1,
        "max_concurrency": 1,
    }
    values.update(changes)
    return AcquisitionBudget(**values)


def _request(
    *,
    acquisition_id: str = "acq-1",
    source_uri: str = "https://files.pythonhosted.org/pkg/demo.whl",
    kind: AcquisitionSourceKind = AcquisitionSourceKind.PYPI,
    permission_grant_id: str | None = None,
    budget: AcquisitionBudget | None = None,
) -> AcquisitionRequest:
    return AcquisitionRequest(
        acquisition_id=acquisition_id,
        owner_id="user-a",
        need_summary="准备固定版本的 PDF 表格工具",
        candidates=(
            AcquisitionCandidate(
                candidate_id="candidate-1",
                kind=kind,
                source_uri=source_uri,
                version="1.0.0",
                expected_sha256="sha256:" + "a" * 64,
                permission_grant_id=permission_grant_id,
            ),
        ),
        budget=budget or _budget(),
    )


def _migrate_test_database(tmp_path, filename: str):
    database = tmp_path / filename
    with sqlite3.connect(database):
        pass
    migrate_capability_acquisition(
        database,
        tmp_path / f"{database.stem}-before-ac05.db",
    )
    return database


def test_explicit_migration_backs_up_before_adding_acquisition_schema(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-ac05.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE existing_governance "
            "(event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO existing_governance VALUES (?, ?)",
            ("event-1", '{"status":"kept"}'),
        )

    migrated_backup = migrate_capability_acquisition(database, backup)

    assert migrated_backup == backup.resolve()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT payload_json FROM existing_governance"
        ).fetchone()[0] == '{"status":"kept"}'
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='capability_acquisition_runs'"
        ).fetchone() is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT payload_json FROM existing_governance"
        ).fetchone()[0] == '{"status":"kept"}'
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='capability_acquisition_runs'"
        ).fetchone() == (1,)


def test_sqlite_repository_refuses_database_without_explicit_migration(
    tmp_path,
) -> None:
    database = tmp_path / "not-migrated.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="请先执行显式迁移"):
        SqliteAcquisitionRepository(database)


def test_sqlite_repository_connections_enforce_integrity_and_lock_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    database = _migrate_test_database(tmp_path, "connection-policy.db")
    repository = SqliteAcquisitionRepository(database)
    real_connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    def tracked_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(
        "src.capability_acquisition.sqlite_repository.sqlite3.connect",
        tracked_connect,
    )

    assert repository.get("missing-acquisition") is None
    connection = connections[-1]
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        connection.close()


def test_sqlite_repository_rejects_malformed_acquisition_schema(
    tmp_path,
) -> None:
    database = tmp_path / "malformed.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE capability_acquisition_runs "
            "(acquisition_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "CREATE INDEX idx_capability_acquisition_owner_status "
            "ON capability_acquisition_runs(acquisition_id)"
        )

    with pytest.raises(RuntimeError, match="请先执行显式迁移"):
        SqliteAcquisitionRepository(database)


def test_sqlite_repository_rejects_schema_with_changed_identity_collation(
    tmp_path,
) -> None:
    database = tmp_path / "changed-collation.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE capability_acquisition_runs ("
            "acquisition_id TEXT COLLATE NOCASE PRIMARY KEY, "
            "owner_id TEXT NOT NULL, status TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX idx_capability_acquisition_owner_status "
            "ON capability_acquisition_runs(owner_id, status)"
        )

    with pytest.raises(RuntimeError, match="请先执行显式迁移"):
        SqliteAcquisitionRepository(database)


def test_explicit_migration_replay_preserves_first_recovery_point(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-ac05.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('kept')")

    first = migrate_capability_acquisition(database, backup)
    backup_before_replay = backup.read_bytes()
    second = migrate_capability_acquisition(database, backup)

    assert first == second == backup.resolve()
    assert backup.read_bytes() == backup_before_replay


def test_migration_replay_allows_normal_business_writes_after_migration(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-ac05.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('before')")
    migrate_capability_acquisition(database, backup)
    backup_before_replay = backup.read_bytes()
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO existing_data VALUES ('after')")

    replayed = migrate_capability_acquisition(database, backup)

    assert replayed == backup.resolve()
    assert backup.read_bytes() == backup_before_replay


def test_current_database_can_create_a_new_explicit_recovery_point(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    first_backup = tmp_path / "production-before-ac05.db"
    different_backup = tmp_path / "different-recovery-point.db"
    with sqlite3.connect(database):
        pass
    migrate_capability_acquisition(database, first_backup)
    first_before = first_backup.read_bytes()

    migrated = migrate_capability_acquisition(database, different_backup)

    assert migrated == different_backup.resolve()
    assert different_backup.is_file()
    assert first_backup.read_bytes() == first_before
    assert SqliteAcquisitionRepository(database).get("missing") is None


def test_migration_rejects_corrupt_source_without_creating_backup(
    tmp_path,
) -> None:
    database = tmp_path / "corrupt.db"
    backup = tmp_path / "corrupt-before-ac05.db"
    database.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(RuntimeError, match="源数据库不可读取"):
        migrate_capability_acquisition(database, backup)

    assert not backup.exists()


def test_migration_bootstraps_a_missing_database_explicitly(
    tmp_path,
) -> None:
    database = tmp_path / "missing.db"
    backup = tmp_path / "missing-before-ac05.db"

    migrated = migrate_capability_acquisition(database, backup)

    assert migrated == backup.resolve()
    assert database.is_file()
    assert backup.is_file()
    assert SqliteAcquisitionRepository(database).get("missing") is None


def test_migration_rejects_backup_that_overwrites_source(tmp_path) -> None:
    database = tmp_path / "production.db"
    with sqlite3.connect(database):
        pass

    with pytest.raises(ValueError, match="备份不能覆盖源数据库"):
        migrate_capability_acquisition(database, database)


def test_migration_rejects_unrelated_existing_backup_before_schema_exists(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "occupied.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE source_data (value TEXT NOT NULL)")
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE unrelated_data (value TEXT NOT NULL)")
    backup_before = backup.read_bytes()

    with pytest.raises(FileExistsError, match="备份已存在"):
        migrate_capability_acquisition(database, backup)

    assert backup.read_bytes() == backup_before


def test_migration_replay_rejects_unrelated_healthy_database_as_backup(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    real_backup = tmp_path / "real-recovery-point.db"
    unrelated_backup = tmp_path / "unrelated.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('production')")
    migrate_capability_acquisition(database, real_backup)
    with sqlite3.connect(unrelated_backup) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('unrelated')")

    with pytest.raises(FileExistsError, match="备份已存在"):
        migrate_capability_acquisition(database, unrelated_backup)


def test_migration_rejects_unbound_backup_completed_before_ddl(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-ac05.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('kept')")
        connection.commit()
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)

    backup_before = backup.read_bytes()

    with pytest.raises(FileExistsError, match="备份已存在"):
        migrate_capability_acquisition(database, backup)

    assert backup.read_bytes() == backup_before
    with sqlite3.connect(backup) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='capability_acquisition_runs'"
        ).fetchone() is None


def test_concurrent_explicit_migrations_share_one_recovery_point(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-ac05.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (payload BLOB NOT NULL)")
        connection.execute(
            "INSERT INTO existing_data VALUES (zeroblob(33554432))"
        )
    gate = tmp_path / "start-migration"
    command = (
        "from pathlib import Path; import sys,time; "
        "from src.capability_acquisition import migrate_capability_acquisition; "
        "gate=Path(sys.argv[3]); Path(sys.argv[4]).touch(); "
        "exec('while not gate.exists():\\n time.sleep(0.01)'); "
        "print(migrate_capability_acquisition(sys.argv[1], sys.argv[2]))"
    )
    ready_paths = [tmp_path / "ready-1", tmp_path / "ready-2"]
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-c",
                command,
                str(database),
                str(backup),
                str(gate),
                str(ready),
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for ready in ready_paths
    ]
    deadline = time.monotonic() + 30
    while not all(path.exists() for path in ready_paths):
        if time.monotonic() >= deadline:
            raise TimeoutError("并发迁移子进程未按时就绪")
        time.sleep(0.01)
    gate.touch()
    completed = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], completed
    assert all(str(backup.resolve()) in stdout for stdout, _stderr in completed)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='capability_acquisition_runs'"
        ).fetchone() is None


def test_business_write_cannot_enter_between_backup_and_ddl(tmp_path) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-ac05.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('before')")
        connection.execute("CREATE TABLE padding (payload BLOB NOT NULL)")
        connection.execute("INSERT INTO padding VALUES (zeroblob(33554432))")
    observed_schema: list[bool] = []

    def write_when_backup_starts() -> None:
        deadline = time.monotonic() + 30
        while not list(tmp_path.glob(f".{backup.name}.*.tmp")):
            if time.monotonic() >= deadline:
                raise TimeoutError("迁移临时备份未按时出现")
            time.sleep(0.005)
        with sqlite3.connect(database, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            observed_schema.append(
                connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='capability_acquisition_migrations'"
                ).fetchone()
                == (1,)
            )
            connection.execute("INSERT INTO existing_data VALUES ('during')")

    writer = threading.Thread(target=write_when_backup_starts)
    writer.start()
    migrate_capability_acquisition(database, backup)
    writer.join(timeout=30)

    assert not writer.is_alive()
    assert observed_schema == [True]
    with sqlite3.connect(backup) as connection:
        assert connection.execute(
            "SELECT value FROM existing_data ORDER BY value"
        ).fetchall() == [("before",)]


class FakeEnvironment:
    def __init__(self) -> None:
        self.started = 0
        self.prepared = 0
        self.cleaned = 0
        self.cancelled = 0
        self.final_uri = "https://files.pythonhosted.org/pkg/demo.whl"
        self.download_bytes = 10
        self.unpacked_bytes = 20
        self.block_prepare = False
        self.fail_prepare_attempts = 0
        self.fail_cleanup = False
        self.block_cleanup = False
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self._release = asyncio.Event()
        self._cached: PreparedCapability | None = None
        self.recovered = 0
        self.allowed_domains: tuple[str, ...] = ()

    async def claim_execution(self, request, cancel_event):
        return object()

    async def release_execution(self, claim):
        return None

    async def recover(self, request):
        self.recovered += 1

    async def start(self, request, allowed_domains):
        self.started += 1
        self.allowed_domains = allowed_domains
        assert "source_path" not in request.model_fields
        assert "provider_key" not in request.model_fields
        return "lease-1"

    async def resolve(self, lease, candidate):
        return ResolvedCandidate(
            candidate=candidate,
            final_uri=self.final_uri,
            redirect_chain=(candidate.source_uri, self.final_uri),
        )

    async def lookup(self, resolved):
        return self._cached

    async def prepare(self, lease, resolved, budget, cancel_event):
        self.prepared += 1
        if self.prepared <= self.fail_prepare_attempts:
            raise RuntimeError("temporary download error")
        if self.block_prepare:
            release = asyncio.create_task(self._release.wait())
            cancelled = asyncio.create_task(cancel_event.wait())
            done, pending = await asyncio.wait(
                (release, cancelled),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if cancelled in done:
                raise asyncio.CancelledError
        artifact = PreparedCapability(
            pack_id="pdf-table-tool",
            version="1.0.0",
            digest="sha256:" + "a" * 64,
            oci_reference="oci-layout://local/pdf-table-tool@sha256:" + "a" * 64,
            source_uri=resolved.candidate.source_uri,
            final_uri=resolved.final_uri,
            download_bytes=self.download_bytes,
            unpacked_bytes=self.unpacked_bytes,
            reused=False,
        )
        self._cached = artifact.model_copy(update={"reused": True})
        return artifact

    async def cancel(self, acquisition_id):
        self.cancelled += 1

    async def cleanup(self, lease):
        self.cleaned += 1
        self.cleanup_started.set()
        if self.block_cleanup:
            await self.release_cleanup.wait()
        if self.fail_cleanup:
            raise RuntimeError("cleanup failed")


@pytest.mark.asyncio
async def test_acquire_is_owner_scoped_ready_and_second_run_reuses_digest() -> None:
    environment = FakeEnvironment()
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
    )
    events = []

    first = await acquisition.acquire(_request(), events.append)
    second = await acquisition.acquire(
        _request(acquisition_id="acq-2"),
        events.append,
    )

    assert first.status is AcquisitionStatus.READY
    assert first.pack_ref is not None
    assert first.reused is False
    assert second.status is AcquisitionStatus.READY
    assert second.reused is True
    assert environment.prepared == 1
    assert environment.cleaned == 2
    assert [event.status for event in events[:5]] == [
        AcquisitionStatus.DISCOVERING,
        AcquisitionStatus.ACQUIRING,
        AcquisitionStatus.BUILDING,
        AcquisitionStatus.VALIDATING,
        AcquisitionStatus.READY,
    ]


def test_request_rejects_host_path_provider_key_and_too_many_candidates() -> None:
    candidate = AcquisitionCandidate(
        candidate_id="candidate",
        kind=AcquisitionSourceKind.PYPI,
        source_uri="https://pypi.org/project/demo/",
        version="1.0.0",
        expected_sha256="sha256:" + "a" * 64,
    )
    with pytest.raises(ValidationError):
        _request().model_copy(
            update={"need_summary": r"读取 C:\secret\source.pdf"},
        ).model_validate(_request().model_dump() | {"need_summary": r"读取 C:\secret\source.pdf"})
    with pytest.raises(ValidationError):
        AcquisitionRequest(
            acquisition_id="bad-key",
            owner_id="user-a",
            need_summary="使用 api_key=sk-secret 安装工具",
            candidates=(candidate,),
            budget=_budget(),
        )
    with pytest.raises(ValidationError):
        AcquisitionRequest(
            acquisition_id="too-many",
            owner_id="user-a",
            need_summary="候选过多",
            candidates=(candidate, candidate),
            budget=_budget(max_candidates=1),
        )


@pytest.mark.asyncio
async def test_unknown_url_waits_for_permission_without_starting_environment() -> None:
    environment = FakeEnvironment()
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
    )

    result = await acquisition.acquire(
        _request(
            kind=AcquisitionSourceKind.UNKNOWN_URL,
            source_uri="https://unknown.example/tool.zip",
        ),
        lambda _event: None,
    )

    assert result.status is AcquisitionStatus.AWAITING_PERMISSION
    assert result.failure_code == "SOURCE_PERMISSION_REQUIRED"
    assert environment.started == 0


@pytest.mark.asyncio
async def test_unknown_url_requires_owner_bound_permission_grant() -> None:
    environment = FakeEnvironment()
    environment.final_uri = "https://unknown.example/tool.zip"
    policy = SourcePolicy(
        lambda owner_id, grant_id, uri: (
            owner_id == "user-a"
            and grant_id == "grant-1"
            and uri == "https://unknown.example/tool.zip"
        )
    )
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        policy,
    )

    result = await acquisition.acquire(
        _request(
            kind=AcquisitionSourceKind.UNKNOWN_URL,
            source_uri="https://unknown.example/tool.zip",
            permission_grant_id="grant-1",
        ),
        lambda _event: None,
    )

    assert result.status is AcquisitionStatus.READY
    assert environment.started == 1
    assert environment.allowed_domains == ("unknown.example",)


@pytest.mark.asyncio
async def test_registered_source_uses_trusted_registration_not_request_hosts() -> None:
    environment = FakeEnvironment()
    environment.final_uri = "https://mcp.example/tool.zip"
    policy = SourcePolicy(
        registered_source_resolver=lambda owner_id, registration_id, uri: (
            ("mcp.example",)
            if (owner_id, registration_id, uri)
            == ("user-a", "registry-1", "https://mcp.example/tool.zip")
            else None
        )
    )
    request = AcquisitionRequest(
        acquisition_id="registered",
        owner_id="user-a",
        need_summary="准备登记 MCP",
        candidates=(
            AcquisitionCandidate(
                candidate_id="registered-candidate",
                kind=AcquisitionSourceKind.REGISTERED_MCP,
                source_uri="https://mcp.example/tool.zip",
                version="1.0.0",
                expected_sha256="sha256:" + "a" * 64,
                source_registration_id="registry-1",
            ),
        ),
        budget=_budget(),
    )

    result = await CapabilityAcquisition(
        InMemoryAcquisitionRepository(), environment, policy
    ).acquire(request, lambda _event: None)

    assert result.status is AcquisitionStatus.READY
    assert environment.allowed_domains == ("mcp.example",)


@pytest.mark.asyncio
async def test_waiting_acquisition_resumes_after_same_grant_is_approved() -> None:
    environment = FakeEnvironment()
    environment.final_uri = "https://unknown.example/tool.zip"
    approved = False

    def check_permission(owner_id, grant_id, uri):
        return approved and owner_id == "user-a" and grant_id == "grant-pending"

    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(check_permission),
    )
    request = _request(
        kind=AcquisitionSourceKind.UNKNOWN_URL,
        source_uri="https://unknown.example/tool.zip",
        permission_grant_id="grant-pending",
    )

    waiting = await acquisition.acquire(request, lambda _event: None)
    approved = True
    resumed = await acquisition.acquire(request, lambda _event: None)

    assert waiting.status is AcquisitionStatus.AWAITING_PERMISSION
    assert resumed.status is AcquisitionStatus.READY
    assert environment.started == 1


@pytest.mark.asyncio
async def test_redirect_final_host_is_rechecked_before_download() -> None:
    environment = FakeEnvironment()
    environment.final_uri = "https://evil.example/payload.whl"
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
    )

    result = await acquisition.acquire(_request(), lambda _event: None)

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure_code == "FINAL_SOURCE_NOT_ALLOWED"
    assert environment.prepared == 0
    assert environment.cleaned == 1


@pytest.mark.asyncio
async def test_budget_failure_closes_and_cleans_without_ready_pack() -> None:
    environment = FakeEnvironment()
    environment.download_bytes = 101
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
    )

    result = await acquisition.acquire(
        _request(budget=_budget(max_download_bytes=100)),
        lambda _event: None,
    )

    assert result.status is AcquisitionStatus.FAILED
    assert result.failure_code == "DOWNLOAD_BUDGET_EXCEEDED"
    assert result.pack_ref is None
    assert environment.cleaned == 1


@pytest.mark.asyncio
async def test_owner_can_cancel_and_other_owner_cannot() -> None:
    environment = FakeEnvironment()
    environment.block_prepare = True
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
    )
    running = asyncio.create_task(
        acquisition.acquire(_request(), lambda _event: None)
    )
    for _attempt in range(100):
        if environment.prepared:
            break
        await asyncio.sleep(0.01)

    with pytest.raises(PermissionError):
        await acquisition.cancel("acq-1", "user-b")
    await acquisition.cancel("acq-1", "user-a")
    result = await running

    assert result.status is AcquisitionStatus.CANCELLED
    assert result.pack_ref is None
    assert environment.cancelled == 1
    assert environment.cleaned == 1


@pytest.mark.asyncio
async def test_cross_instance_cancel_is_persisted_and_classified_cancelled() -> None:
    repository = InMemoryAcquisitionRepository()
    running_environment = FakeEnvironment()
    running_environment.block_prepare = True
    running_service = CapabilityAcquisition(
        repository,
        running_environment,
        SourcePolicy(),
    )
    cancelling_service = CapabilityAcquisition(
        repository,
        FakeEnvironment(),
        SourcePolicy(),
    )
    running = asyncio.create_task(
        running_service.acquire(_request(), lambda _event: None)
    )
    for _attempt in range(100):
        if running_environment.prepared:
            break
        await asyncio.sleep(0.01)

    await cancelling_service.cancel("acq-1", "user-a")
    running_environment._release.set()
    result = await running

    assert result.status is AcquisitionStatus.CANCELLED
    assert repository.get("acq-1").status is AcquisitionStatus.CANCELLED


@pytest.mark.asyncio
async def test_terminal_result_survives_repository_reopen(tmp_path) -> None:
    db_path = _migrate_test_database(tmp_path, "acquisition.db")
    request = _request(acquisition_id="acq-restart")
    first_environment = FakeEnvironment()
    first = CapabilityAcquisition(
        SqliteAcquisitionRepository(db_path),
        first_environment,
        SourcePolicy(),
    )
    expected = await first.acquire(request, lambda _event: None)

    reopened_environment = FakeEnvironment()
    reopened = CapabilityAcquisition(
        SqliteAcquisitionRepository(db_path),
        reopened_environment,
        SourcePolicy(),
    )
    actual = await reopened.acquire(request, lambda _event: None)

    assert actual == expected
    assert reopened_environment.started == 0


@pytest.mark.asyncio
async def test_nonterminal_record_recovers_before_restart() -> None:
    repository = InMemoryAcquisitionRepository()
    request = _request(acquisition_id="acq-recover")
    record = repository.create(request)
    repository.save(record.model_copy(update={"status": AcquisitionStatus.BUILDING}))
    environment = FakeEnvironment()

    result = await CapabilityAcquisition(
        repository, environment, SourcePolicy()
    ).acquire(request, lambda _event: None)

    assert result.status is AcquisitionStatus.READY
    assert environment.recovered == 1


def test_sqlite_ready_finalize_cannot_overwrite_cross_instance_cancel(tmp_path) -> None:
    db_path = _migrate_test_database(tmp_path, "acquisition-cas.db")
    request = _request(acquisition_id="acq-cas")
    first = SqliteAcquisitionRepository(db_path)
    second = SqliteAcquisitionRepository(db_path)
    record = first.create(request)
    second.save(record.model_copy(update={"cancel_requested": True}))

    finalized = first.finalize_ready(
        record.model_copy(update={"status": AcquisitionStatus.READY})
    )

    assert finalized is None
    assert first.get("acq-cas").cancel_requested is True


def test_sqlite_stale_cancel_cannot_overwrite_cross_instance_ready(tmp_path) -> None:
    db_path = _migrate_test_database(tmp_path, "acquisition-cas-ready.db")
    request = _request(acquisition_id="acq-cas-ready")
    first = SqliteAcquisitionRepository(db_path)
    second = SqliteAcquisitionRepository(db_path)
    record = first.create(request)
    ready = record.model_copy(update={"status": AcquisitionStatus.READY})
    assert first.finalize_ready(ready) is not None

    after_cancel = second.request_cancel("acq-cas-ready", "user-a")

    assert after_cancel.status is AcquisitionStatus.READY
    assert first.get("acq-cas-ready").status is AcquisitionStatus.READY


def test_sqlite_finalize_ready_cannot_overwrite_failed_terminal(tmp_path) -> None:
    db_path = _migrate_test_database(tmp_path, "acquisition-terminal.db")
    request = _request(acquisition_id="acq-terminal")
    repository = SqliteAcquisitionRepository(db_path)
    record = repository.create(request)
    failed = record.model_copy(update={"status": AcquisitionStatus.FAILED})
    repository.save(failed)

    assert repository.finalize_ready(
        record.model_copy(update={"status": AcquisitionStatus.READY})
    ) is None
    assert repository.get("acq-terminal").status is AcquisitionStatus.FAILED


@pytest.mark.asyncio
async def test_retry_budget_is_bounded_per_source() -> None:
    environment = FakeEnvironment()
    environment.fail_prepare_attempts = 1
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
    )

    events = []
    result = await acquisition.acquire(
        _request(budget=_budget(max_retries_per_source=1)),
        events.append,
    )

    assert result.status is AcquisitionStatus.READY
    assert environment.prepared == 2
    assert any("第 1 次尝试失败" in event.summary for event in events)


@pytest.mark.asyncio
async def test_concurrent_same_candidate_is_prepared_once() -> None:
    environment = FakeEnvironment()
    acquisition = CapabilityAcquisition(
        InMemoryAcquisitionRepository(),
        environment,
        SourcePolicy(),
        platform_max_concurrency=2,
    )

    first, second = await asyncio.gather(
        acquisition.acquire(
            _request(acquisition_id="acq-concurrent-1"),
            lambda _event: None,
        ),
        acquisition.acquire(
            _request(acquisition_id="acq-concurrent-2"),
            lambda _event: None,
        ),
    )

    assert first.digest == second.digest
    assert environment.prepared == 1
    assert {first.reused, second.reused} == {False, True}


@pytest.mark.asyncio
async def test_execution_claim_fences_same_acquisition_across_instances(tmp_path) -> None:
    def environment():
        return DockerBuildkitAcquisitionEnvironment(
            workspace_root=tmp_path / "active",
            cache_root=tmp_path / "cache",
            model_base_url="http://192.168.1.2:6012/v1",
            downloader_image="unused",
            egress_controller=object(),
            artifact_store=object(),
        )

    first_environment = environment()
    second_environment = environment()
    request = _request(acquisition_id="acq-fenced")
    first_claim = await first_environment.claim_execution(
        request,
        asyncio.Event(),
    )
    waiting = asyncio.create_task(
        second_environment.claim_execution(request, asyncio.Event())
    )
    await asyncio.sleep(0.1)

    assert not waiting.done()
    await first_environment.release_execution(first_claim)
    second_claim = await asyncio.wait_for(waiting, timeout=2)
    await second_environment.release_execution(second_claim)


@pytest.mark.asyncio
async def test_failed_first_candidate_falls_back_to_second() -> None:
    environment = FakeEnvironment()
    first = _request().candidates[0].model_copy(
        update={"candidate_id": "blocked", "source_uri": "https://evil.example/a.whl"}
    )
    second = _request().candidates[0].model_copy(update={"candidate_id": "allowed"})
    request = _request().model_copy(update={"candidates": (first, second)})

    result = await CapabilityAcquisition(
        InMemoryAcquisitionRepository(), environment, SourcePolicy()
    ).acquire(request, lambda _event: None)

    assert result.status is AcquisitionStatus.READY
    assert environment.started == 1


@pytest.mark.asyncio
async def test_ready_event_is_emitted_only_after_cleanup() -> None:
    environment = FakeEnvironment()
    observed_cleanups: list[int] = []

    def observe(event):
        if event.status is AcquisitionStatus.READY:
            observed_cleanups.append(environment.cleaned)

    result = await CapabilityAcquisition(
        InMemoryAcquisitionRepository(), environment, SourcePolicy()
    ).acquire(_request(), observe)

    assert result.status is AcquisitionStatus.READY
    assert observed_cleanups == [1]


@pytest.mark.asyncio
async def test_ready_sink_failure_does_not_reverse_persisted_result() -> None:
    repository = InMemoryAcquisitionRepository()

    def broken_sink(event):
        if event.status is AcquisitionStatus.READY:
            raise RuntimeError("client disconnected")

    result = await CapabilityAcquisition(
        repository, FakeEnvironment(), SourcePolicy()
    ).acquire(_request(), broken_sink)

    assert result.status is AcquisitionStatus.READY
    assert repository.get("acq-1").status is AcquisitionStatus.READY


@pytest.mark.asyncio
async def test_cleanup_failure_never_persists_ready() -> None:
    environment = FakeEnvironment()
    environment.fail_cleanup = True
    repository = InMemoryAcquisitionRepository()
    events = []

    result = await CapabilityAcquisition(
        repository, environment, SourcePolicy()
    ).acquire(_request(), events.append)

    assert result.status is AcquisitionStatus.FAILED
    assert AcquisitionStatus.READY not in [event.status for event in events]
    assert repository.get("acq-1").status is AcquisitionStatus.FAILED


@pytest.mark.asyncio
async def test_cancel_between_cleanup_and_ready_cannot_be_overwritten() -> None:
    environment = FakeEnvironment()
    environment.block_cleanup = True
    repository = InMemoryAcquisitionRepository()
    acquisition = CapabilityAcquisition(repository, environment, SourcePolicy())
    running = asyncio.create_task(
        acquisition.acquire(_request(), lambda _event: None)
    )
    await environment.cleanup_started.wait()

    await acquisition.cancel("acq-1", "user-a")
    environment.release_cleanup.set()
    result = await running

    assert result.status is AcquisitionStatus.CANCELLED
    assert repository.get("acq-1").status is AcquisitionStatus.CANCELLED
