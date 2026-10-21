"""首次切换 CLI 只登记固定隔离；命令替身不代表真实维护窗验收。"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from src.deployment_quarantine import ACCEPTED_RISK, EMPTY_MANIFEST, SETTING_KEY, load_quarantine, snapshot_record
from tests.database_migration_helpers import migrated_profile_database, migrated_webui_database


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("quarantine-cli-template")
    return (migrated_webui_database(root / "webui.db"),
            migrated_profile_database(root / "scheduler.db", profile="scheduler"))


@pytest.fixture
def packet(tmp_path, template):
    root = tmp_path / "app"
    (root / "data").mkdir(parents=True)
    database, scheduler = root / "data/webui.db", root / "data/scheduler.db"
    for original, target in zip(template, (database, scheduler)):
        shutil.copyfile(original, target)
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) VALUES ('approver','synthetic-approver','synthetic','super_admin','2026-01-01')")
        conn.execute("INSERT INTO source_read_uses VALUES ('old-use','owner-a',NULL,NULL,'preview','[]','unknown','2026-01-01',NULL)")
        conn.commit()
        record = snapshot_record(conn, "webui", "source_read_uses", {"use_id": "old-use"})
    manifest = {"version": 1, "approval_reference": "issue-244-synthetic-approved-risk", "actor_id": "approver",
                "accepted_risk": ACCEPTED_RISK,
                "targets": [{"owner_id": "owner-a", "kind": "source_read", "resource_id": "old-use", "generation": None}],
                "records": [record]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return SimpleNamespace(root=root, database=database, scheduler=scheduler, path=path, manifest=manifest)


@pytest.fixture
def cli():
    return importlib.import_module("scripts.quarantine_first_cutover")


def _diagnose_rejection(cli, monkeypatch):
    original_dumps = cli.json.dumps
    def diagnose(value, *args, **kwargs):
        if isinstance(value, dict) and value.get("ok") is False and "error_type" in value:
            raise AssertionError("合成成功路径被核验门拒绝") from cli.sys.exception()
        return original_dumps(value, *args, **kwargs)
    monkeypatch.setattr(cli.json, "dumps", diagnose)


def _args(packet, command="plan"):
    values = [command, "--database", str(packet.database), "--scheduler-database", str(packet.scheduler),
              "--manifest", str(packet.path), "--manifest-sha256", hashlib.sha256(packet.path.read_bytes()).hexdigest()]
    if command == "apply":
        values += ["--service", "mangrove.service", "--runtime-root", str(packet.root)]
    return values


def _rewrite_manifest(packet, change):
    value = deepcopy(packet.manifest)
    change(value)
    packet.path.write_text(json.dumps(value), encoding="utf-8")


def _current_pin(packet):
    with closing(sqlite3.connect(packet.database)) as conn:
        return load_quarantine(conn)


def _stopped_machine(cli, packet, monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(cli, "_service_user_uid", lambda user: 1000 if user == "maintenance" else 2000)
    original_lstat = Path.lstat
    def lstat(path):
        result = original_lstat(path)
        if path in {packet.database, packet.scheduler} or any(str(path) == str(database) + suffix for database in (packet.database, packet.scheduler) for suffix in ("-wal", "-shm")):
            return SimpleNamespace(**{name: getattr(result, name) for name in dir(result) if name.startswith("st_") and name != "st_uid"}, st_uid=1000)
        return result
    monkeypatch.setattr(Path, "lstat", lstat)
    cgroups = packet.root.parent / "cgroup"
    cgroups.mkdir()
    monkeypatch.setattr(cli, "CGROUP_ROOT", cgroups)
    state = {"MainPID": "0", "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
             "User": "maintenance", "WorkingDirectory": str(packet.root), "NeedDaemonReload": "no", "ControlGroup": ""}
    observed = []
    responses = []
    def run(command, **kwargs):
        observed.append(command)
        assert kwargs.get("shell", False) is False
        assert kwargs["timeout"] <= 10
        if command[0] == "/usr/bin/systemctl":
            assert command[-2:] == ["--", "mangrove.service"]
            return SimpleNamespace(returncode=0, stdout="\n".join(f"{key}={value}" for key, value in state.items()) + "\n", stderr="")
        assert command[:6] == ["/usr/bin/sudo", "-n", "/usr/bin/lsof", "-nP", "-t", "--"]
        assert str(packet.database) in command and str(packet.scheduler) in command
        return responses.pop(0) if responses else SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(cli.subprocess, "run", run)
    return state, observed, responses


def test_plan_reads_original_metadata_without_pin_or_writes(cli, packet, monkeypatch, capsys):
    _diagnose_rejection(cli, monkeypatch)
    with closing(sqlite3.connect(packet.database)) as conn:
        for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='app_settings'").fetchall():
            conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        conn.execute("DELETE FROM app_settings WHERE key=?", (SETTING_KEY,))
        conn.execute("UPDATE alembic_version SET version_num='webui_0023'")
        conn.commit()
    original = packet.database.read_bytes()
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: pytest.fail("plan 不能运行服务命令"))
    assert cli.main(_args(packet)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["command"] == "plan"
    assert result["stop_confirmed"] is False and result["scope_complete"] is False
    assert result["targets_count"] == result["records_count"] == 1
    assert "old-use" not in json.dumps(result) and "owner-a" not in json.dumps(result)
    assert packet.database.read_bytes() == original


@pytest.mark.parametrize("change", [lambda value: value["records"][0].update(sha256="a" * 64),
                                        lambda value: value.update(actor_id="missing")])
def test_plan_rejects_stale_record_or_invalid_actor(cli, packet, change, capsys):
    _rewrite_manifest(packet, change)
    assert cli.main(_args(packet)) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert _current_pin(packet) == EMPTY_MANIFEST


@pytest.mark.parametrize("failure", ["digest", "oversized", "duplicate", "symlink"])
def test_manifest_is_bounded_digest_pinned_and_not_a_symlink(cli, packet, monkeypatch, failure):
    arguments = _args(packet)
    if failure == "digest":
        arguments[-1] = "0" * 64
    elif failure == "oversized":
        packet.path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    elif failure == "duplicate":
        packet.path.write_text('{"version":1,"version":1}', encoding="utf-8")
        arguments = _args(packet)
    else:
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == packet.path)
        import stat
        original_lstat = Path.lstat
        def lstat(path):
            result = original_lstat(path)
            if path == packet.path:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
            return result
        monkeypatch.setattr(Path, "lstat", lstat)
    assert cli.main(arguments) == 1
    assert _current_pin(packet) == EMPTY_MANIFEST


def test_manifest_checks_stat_streams_without_equating_windows_ctime(cli, packet, monkeypatch):
    original_fstat = cli.os.fstat
    def fstat(fd):
        info = original_fstat(fd)
        return SimpleNamespace(**{name: getattr(info, name) for name in dir(info) if name.startswith("st_") and name != "st_ctime_ns"}, st_ctime_ns=info.st_mtime_ns + 1000000)
    monkeypatch.setattr(cli.os, "fstat", fstat)
    assert cli._read_manifest(packet.path, _args(packet)[-1]) == packet.manifest


@pytest.mark.parametrize("mutation", ["inode", "fd_time", "path_time"])
def test_manifest_rejects_different_inode_or_change_in_either_stat_stream(cli, packet, monkeypatch, mutation):
    original_fstat, original_lstat = cli.os.fstat, Path.lstat
    calls = {"fd": 0, "path": 0}
    def altered(info, field):
        return SimpleNamespace(**{name: getattr(info, name) for name in dir(info) if name.startswith("st_") and name != field}, **{field: getattr(info, field) + 1})
    def fstat(fd):
        info = original_fstat(fd)
        calls["fd"] += 1
        if mutation == "inode":
            return altered(info, "st_ino")
        return altered(info, "st_ctime_ns") if mutation == "fd_time" and calls["fd"] > 1 else info
    def lstat(path):
        info = original_lstat(path)
        if path == packet.path:
            calls["path"] += 1
            if mutation == "path_time" and calls["path"] > 1:
                return altered(info, "st_ctime_ns")
        return info
    monkeypatch.setattr(cli.os, "fstat", fstat)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError):
        cli._read_manifest(packet.path, _args(packet)[-1])


def test_apply_checks_maintenance_twice_and_only_registers_pin(cli, packet, monkeypatch, capsys):
    _diagnose_rejection(cli, monkeypatch)
    state, commands, responses = _stopped_machine(cli, packet, monkeypatch)
    with closing(sqlite3.connect(packet.database)) as conn:
        original = conn.execute("SELECT * FROM source_read_uses").fetchall()
    assert cli.main(_args(packet, "apply")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == "apply" and result["stop_confirmed"] is False
    assert sum(command[0] == "/usr/bin/systemctl" for command in commands) == 2
    assert sum(command[0] == "/usr/bin/sudo" for command in commands) == 2
    assert _current_pin(packet) == packet.manifest
    with closing(sqlite3.connect(packet.database)) as conn:
        assert conn.execute("SELECT * FROM source_read_uses").fetchall() == original
    commands.clear()
    assert cli.main(_args(packet, "apply")) == 0
    assert _current_pin(packet) == packet.manifest


@pytest.mark.parametrize("field,value", [("MainPID", "42"), ("LoadState", "not-found"), ("ActiveState", "active"),
                                        ("SubState", "failed"), ("User", "other"), ("WorkingDirectory", "/other"), ("NeedDaemonReload", "yes")])
def test_apply_refuses_unconfirmed_unit(cli, packet, monkeypatch, field, value):
    state, commands, responses = _stopped_machine(cli, packet, monkeypatch)
    state[field] = value
    assert cli.main(_args(packet, "apply")) == 1
    assert _current_pin(packet) == EMPTY_MANIFEST


@pytest.mark.parametrize("response", [SimpleNamespace(returncode=0, stdout="99999\n", stderr=""),
                                    SimpleNamespace(returncode=0, stdout="not-pid\n", stderr=""),
                                    SimpleNamespace(returncode=2, stdout="", stderr=""),
                                    SimpleNamespace(returncode=1, stdout="", stderr="lsof failed")])
def test_apply_refuses_external_or_unknown_database_holders(cli, packet, monkeypatch, response):
    state, commands, responses = _stopped_machine(cli, packet, monkeypatch)
    responses.append(response)
    assert cli.main(_args(packet, "apply")) == 1
    assert _current_pin(packet) == EMPTY_MANIFEST


def test_apply_allows_only_its_own_database_handles(cli, packet, monkeypatch):
    state, commands, responses = _stopped_machine(cli, packet, monkeypatch)
    responses.extend([SimpleNamespace(returncode=0, stdout=str(cli.os.getpid()) + "\n", stderr="") for _ in range(2)])
    _diagnose_rejection(cli, monkeypatch)
    assert cli.main(_args(packet, "apply")) == 0


def test_apply_rolls_back_when_second_maintenance_check_changes(cli, packet, monkeypatch):
    state, commands, responses = _stopped_machine(cli, packet, monkeypatch)
    run = cli.subprocess.run
    def second_check(command, **kwargs):
        if command[0] == "/usr/bin/systemctl" and any(item[0] == "/usr/bin/sudo" for item in commands):
            state["MainPID"] = "42"
        return run(command, **kwargs)
    monkeypatch.setattr(cli.subprocess, "run", second_check)
    assert cli.main(_args(packet, "apply")) == 1
    assert _current_pin(packet) == EMPTY_MANIFEST


def test_apply_refuses_processes_in_nested_original_cgroup(cli, packet, monkeypatch):
    state, commands, responses = _stopped_machine(cli, packet, monkeypatch)
    nested = cli.CGROUP_ROOT / "system.slice/mangrove.service/worker"
    nested.mkdir(parents=True)
    (nested.parent / "cgroup.procs").write_text("", encoding="utf-8")
    (nested / "cgroup.procs").write_text("123\n", encoding="utf-8")
    assert cli.main(_args(packet, "apply")) == 1
    assert _current_pin(packet) == EMPTY_MANIFEST


@pytest.mark.parametrize("failure", ["root", "platform", "copy", "schema"])
def test_apply_refuses_wrong_identity_copy_or_unmigrated_schema(cli, packet, monkeypatch, failure):
    state, commands, responses = _stopped_machine(cli, packet, monkeypatch)
    if failure == "root":
        monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    elif failure == "platform":
        monkeypatch.setattr(cli.sys, "platform", "win32")
    elif failure == "copy":
        copy = packet.database.with_name("copy.db")
        shutil.copyfile(packet.database, copy)
        packet.database = copy
    else:
        with closing(sqlite3.connect(packet.database)) as conn:
            conn.execute("UPDATE alembic_version SET version_num='webui_0023'")
            conn.commit()
    assert cli.main(_args(packet, "apply")) == 1
    assert _current_pin(packet) == EMPTY_MANIFEST
