"""复用列表批量读取；真实临时库，不读取业务正文或绕过选用校验。"""
import hashlib
import json
import sqlite3

import pytest

from src.config.settings import settings
from src.source_acquisition import reuse
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    from src.api import auth

    database = migrated_webui_database(tmp_path / "history.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(settings, "data_prep_upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(auth, "_store", None)
    reuse._store()
    return database


def seed_inventory(database, count, *, owner="owner-a", start=0):
    with sqlite3.connect(database) as connection:
        for index in range(start, start + count):
            identity = f"{owner}-{index:04d}"
            connection.execute("INSERT INTO source_acquisition_attempts(attempt_id,owner_id,idempotency_key,request_hash,request_url,normalized_url,allowed_scope_json,purpose,status,started_at) VALUES (?,?,?,?,?,?,?,'合成','succeeded','now')", (identity, owner, identity, "hash", "url", "url", '{}'))
            connection.execute("INSERT INTO source_snapshots(snapshot_id,owner_id,attempt_id,allowed_scope_json,valid_page_count,failed_page_count,created_at) VALUES (?,?,?,'{}',1,0,'now')", (identity, owner, identity))
            raw = b"source"
            connection.execute("INSERT INTO source_artifacts VALUES (?,?,?,'url','url','now',?,'text/html',?,'标题','不应读取的预览',?)", (identity, owner, identity, hashlib.sha256(raw).hexdigest(), len(raw), raw))
            outputs = [dict(output_id=identity, sha256="a" * 64, size_bytes=6, qa=dict(openable=True, sha256="a" * 64, size_bytes=6))]
            manifest = json.dumps(dict(status="succeeded", run_id=identity, outputs=outputs))
            connection.execute("INSERT INTO formal_delivery_runs(delivery_id,publication_key,run_id,owner_id,task_id,task_revision,candidate_set_hash,verification_report_id,verification_report_hash,delivery_spec_hash,status,manifest_json,output_dir,created_at) VALUES (?,?,?,?,?,1,'hash','report','hash','hash','succeeded',?,'directory','now')", (identity, identity, identity, owner, "task-" + identity, manifest))
            connection.execute("INSERT INTO formal_delivery_outputs VALUES (?,?,?,?,'json','result.json','application/json',?,6,?,?, 'now')", (identity, identity, identity, owner, "a" * 64, f'managed:v1/{identity}/result.json', json.dumps(outputs[0]['qa'])))


def traced_history(monkeypatch, owner):
    original = sqlite3.connect
    queries = []
    connections = []
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        connection.set_trace_callback(lambda sql: queries.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
        return connection
    with monkeypatch.context() as trace:
        trace.setattr(sqlite3, "connect", connect)
        items = reuse.history(owner)
    return items, queries, len(connections)


def test_history_query_count_is_constant_and_page_token_covers_full_inventory(inventory, monkeypatch):
    seed_inventory(inventory, 1)
    small, small_queries, small_connections = traced_history(monkeypatch, "owner-a")
    seed_inventory(inventory, 47, start=1)
    seed_inventory(inventory, 2, owner="owner-b")
    large, large_queries, large_connections = traced_history(monkeypatch, "owner-a")
    assert len(small) == 2 and len(large) == 96
    assert large_connections == small_connections == 1
    assert len(large_queries) == len(small_queries) <= 7
    assert not any("content_blob" in sql or "text_preview" in sql or "source_page_failures" in sql for sql in large_queries)
    assert all("owner-b" not in item["source_key"] and item["availability"] == "available" for item in large)
    assert large == sorted(large, key=lambda item: item["source_key"])
    first = reuse.page(large, limit=7)
    second = reuse.page(reuse.history("owner-a"), cursor=first["next_cursor"], limit=7, snapshot_token=first["snapshot_token"])
    assert second["items"] == large[7:14]
    with sqlite3.connect(inventory) as connection:
        connection.execute("INSERT INTO source_deletion_operations VALUES ('delete','owner-a','task','key','hash','{}','completed',NULL,'now','now')")
        connection.execute("INSERT INTO source_deletions VALUES ('owner-a','delivery_output:owner-a-0047','delete',?,NULL,'deleted','now')", ("a" * 64,))
    changed = reuse.history("owner-a")
    assert changed[-1]["source_key"] == large[-1]["source_key"]
    deleted = next(item for item in changed if item["source_key"] == 'delivery_output:owner-a-0047')
    assert deleted["availability"] == "unavailable"
    with pytest.raises(ValueError, match="references_changed"):
        reuse.page(changed, cursor=first["next_cursor"], limit=7, snapshot_token=first["snapshot_token"])
    with pytest.raises(ValueError, match="source_deleted"):
        reuse.resolve_frozen_source("owner-a", dict(kind="delivery_output", output_id="owner-a-0047"))
    with pytest.raises(PermissionError):
        reuse.resolve_frozen_source("owner-b", dict(kind="delivery_output", output_id="owner-a-0000"))


def test_history_keeps_formal_quality_and_selection_byte_recheck(inventory):
    from pathlib import Path

    seed_inventory(inventory, 1)
    output_id = 'owner-a-0000'
    item = next(item for item in reuse.history('owner-a') if item.get('output_id') == output_id)
    assert item['availability'] == 'available'
    # 列表只承诺登记可用；本体缺失或被改写必须在选用时拒绝。
    with pytest.raises(ValueError, match='完整性'):
        reuse.resolve_frozen_source('owner-a', dict(kind='delivery_output', output_id=output_id))
    path = Path(settings.semantic_execution_root) / output_id / 'result.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'wrong!')
    with pytest.raises(ValueError, match='完整性'):
        reuse.resolve_frozen_source('owner-a', dict(kind='delivery_output', output_id=output_id))
    with sqlite3.connect(inventory) as connection:
        # 仅隔离库模拟登记的发布证据失配，原件选用的安全门保持。
        manifest = json.loads(connection.execute('SELECT manifest_json FROM formal_delivery_runs WHERE delivery_id=?', (output_id,)).fetchone()[0])
        manifest['outputs'][0]['qa']['openable'] = False
        connection.execute('UPDATE formal_delivery_runs SET manifest_json=? WHERE delivery_id=?', (json.dumps(manifest), output_id))
    invalid = next(item for item in reuse.history('owner-a') if item.get('output_id') == output_id)
    assert invalid['availability'] == 'unavailable' and invalid['reason_code'] == 'formal_evidence_invalid'


@pytest.mark.parametrize('mode,available', [('array',False),('bad_tail',True),('bad_prefix',False),('other_id_type',True)])
def test_history_isolates_malformed_manifest_without_invalidating_prior_match(inventory, mode, available):
    seed_inventory(inventory, 2)
    identity='owner-a-0000'
    with sqlite3.connect(inventory) as connection:
        manifest=json.loads(connection.execute('SELECT manifest_json FROM formal_delivery_runs WHERE delivery_id=?',(identity,)).fetchone()[0])
        good=manifest['outputs'][0]
        if mode=='array':
            manifest=[]
        elif mode=='bad_tail':
            manifest['outputs']=[good,{}]
        elif mode=='bad_prefix':
            manifest['outputs']=[{},good]
        else:
            manifest['outputs']=[{'output_id':[]},good]
        connection.execute('UPDATE formal_delivery_runs SET manifest_json=? WHERE delivery_id=?',(json.dumps(manifest),identity))
    items={item['output_id']:item for item in reuse.history('owner-a') if item['kind']=='delivery_output'}
    assert items[identity]['availability']==('available' if available else 'unavailable')
    assert items['owner-a-0001']['availability']=='available'


def test_history_preserves_multiple_outputs_and_legacy_precedence(inventory):
    seed_inventory(inventory, 2)
    first, second='owner-a-0000','owner-a-0001'
    with sqlite3.connect(inventory) as connection:
        manifest=json.loads(connection.execute('SELECT manifest_json FROM formal_delivery_runs WHERE delivery_id=?',(first,)).fetchone()[0])
        manifest['outputs'].append({**manifest['outputs'][0],'output_id':second})
        connection.execute('UPDATE formal_delivery_runs SET manifest_json=? WHERE delivery_id=?',(json.dumps(manifest),first))
        connection.execute('UPDATE formal_delivery_outputs SET delivery_id=?,run_id=? WHERE output_id=?',(first,first,second))
    items={item['output_id']:item for item in reuse.history('owner-a') if item['kind']=='delivery_output'}
    assert items[first]['availability']==items[second]['availability']=='available'
    with sqlite3.connect(inventory) as connection:
        # 双表同标识沿用旧读取的 Legacy 优先级，不把两份登记或证据互相拼接。
        qa=dict(openable=True,sha256='b'*64,size_bytes=8)
        legacy=dict(status='succeeded',run_id=first,outputs=[dict(output_id=first,sha256='b'*64,size_bytes=8,qa=qa),manifest['outputs'][1]])
        connection.execute("INSERT INTO semantic_delivery_runs VALUES (?, ?,'owner-a','succeeded',?,'directory','now')",(first,first,json.dumps(legacy)))
        connection.execute("INSERT INTO semantic_delivery_outputs VALUES (?,?,?,'owner-a','json','legacy.json','application/json',?,8,?,?,'now')",(first,first,first,'b'*64,f'managed:v1/{first}/legacy.json',json.dumps(qa)))
    items={item['output_id']:item for item in reuse.history('owner-a') if item['kind']=='delivery_output'}
    assert items[first]['availability']=='available' and items[first]['label']=='legacy.json' and items[first]['sha256']=='b'*64
    assert items[second]['availability']=='available'
    for identity in (first,second):
        try:
            reuse.verified_output_metadata('owner-a',identity)
            availability='available'
        except ValueError:
            availability='unavailable'
        assert items[identity]['availability']==availability
