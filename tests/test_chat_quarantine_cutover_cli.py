"""补充隔离工具复用真实临时双库，维护期复核失败必须回滚登记。"""
import hashlib
import json
import sqlite3

import pytest

from scripts import quarantine_chat_cutover as tool
from src.deployment_quarantine import EMPTY_MANIFEST, load_chat_quarantine
from tests.test_supplemental_chat_quarantine import databases, templates, manifest


def arguments(tmp_path, databases):
    webui, scheduler = databases
    with sqlite3.connect(webui) as connection:
        value = manifest(connection)
    path = tmp_path / 'two-chats.json'
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    path.write_bytes(raw)
    return ['--database', str(webui), '--scheduler-database', str(scheduler),
            '--manifest', str(path), '--manifest-sha256', hashlib.sha256(raw).hexdigest()]


def test_plan_keeps_both_database_bytes_and_unknown_rows(databases, tmp_path, capsys):
    args = arguments(tmp_path, databases)
    before = [p.read_bytes() for p in databases]
    assert tool.main(['plan', *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['pin_state'] == 'empty' and result['stop_confirmed'] is False
    assert result['targets_count'] == result['records_count'] == 2
    assert [p.read_bytes() for p in databases] == before


def test_wrong_manifest_digest_does_not_register(databases, tmp_path, capsys):
    args = arguments(tmp_path, databases)
    args[-1] = '0' * 64
    assert tool.main(['plan', *args]) == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    with sqlite3.connect(databases[0]) as connection:
        assert load_chat_quarantine(connection) == EMPTY_MANIFEST


@pytest.mark.parametrize('fail_final_check', [False, True])
def test_apply_rechecks_maintenance_under_lock_and_rolls_back_on_failure(databases, tmp_path, monkeypatch, capsys, fail_final_check):
    args = arguments(tmp_path, databases)
    calls = []

    def maintenance(*values):
        calls.append(values)
        if fail_final_check and len(calls) == 2:
            raise ValueError('合成维护状态改变')

    monkeypatch.setattr(tool.sys, 'platform', 'linux')
    monkeypatch.setattr(tool.os, 'geteuid', lambda: 1000, raising=False)
    monkeypatch.setattr(tool, '_database_identity', lambda path, uid: (str(path), uid))
    monkeypatch.setattr(tool, '_maintenance_check', maintenance)
    result = tool.main(['apply', *args, '--service', 'mangrove.service', '--runtime-root', str(tmp_path)])
    assert result == (1 if fail_final_check else 0)
    assert len(calls) == 2
    assert json.loads(capsys.readouterr().out)['ok'] is (not fail_final_check)
    with sqlite3.connect(databases[0]) as connection:
        assert (load_chat_quarantine(connection) == EMPTY_MANIFEST) is fail_final_check
        assert {r[0] for r in connection.execute('SELECT state FROM account_execution_bindings')} == {'cleanup_failed'}


def test_apply_refuses_windows_without_maintenance_calls(databases, tmp_path, monkeypatch, capsys):
    args = arguments(tmp_path, databases)
    monkeypatch.setattr(tool.sys, 'platform', 'win32')
    monkeypatch.setattr(tool, '_maintenance_check', lambda *args: pytest.fail('平台检查前不得核维护状态'))
    assert tool.main(['apply', *args, '--service', 'mangrove.service', '--runtime-root', str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)['error_type'] == 'PermissionError'
