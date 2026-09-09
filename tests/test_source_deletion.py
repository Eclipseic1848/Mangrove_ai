"""关联清理必须使用明确确认，隔离库不得提前丢失历史。"""
from datetime import datetime, timedelta
from tests.test_workspace_canvas import canvas, execution_owner


def test_expired_recycle_retains_history_until_explicit_deletion(canvas):
    from src.api.auth import get_store
    client, _, task_id, _, upload = canvas
    store = get_store()
    assert store.soft_delete_semantic_workspace_task("user-a", task_id)
    assert store.purge_expired_semantic_workspace_tasks(now=datetime.now()+timedelta(days=60)) == 0
    assert store.get_semantic_workspace_task("user-a", task_id) is not None
    assert store.get_semantic_workspace_revision("user-a", task_id, 1)["source_refs"]


def test_plan_is_read_only_and_binds_complete_original_identity(canvas):
    from src.source_acquisition.deletion import deletion_plan
    from src.api.auth import get_store
    client, _, task_id, _, upload = canvas
    store=get_store()
    store.soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion_plan('user-a',task_id,'keep_shared')
    source=next(item for item in plan['objects'] if item['source_key']=='upload:'+upload.upload_id)
    assert source['sha256']==upload.sha256
    assert source['disposition']=='delete'
    assert plan['can_execute'] is True
    assert store.get_semantic_workspace_task('user-a',task_id) is not None
    assert __import__('pathlib').Path(upload.storage_path).exists()


def test_deletion_claim_is_owner_scoped_idempotent_and_blocks_reads(canvas):
    from src.source_acquisition.deletion import deletion_plan, begin_operation
    from src.source_acquisition.reuse import resolve_frozen_source
    from src.api.auth import get_store
    import pytest
    _, _, task_id, _, upload=canvas
    get_store().soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion_plan('user-a',task_id)
    operation=begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','same-key')
    assert begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','same-key')['operation_id']==operation['operation_id']
    with pytest.raises(ValueError,match='source_deleting'):
        resolve_frozen_source('user-a',{'upload_id':upload.upload_id,'sha256':upload.sha256})


def _add_consumer(store,upload,identity='new-consumer'):
    return store.create_semantic_workspace_task('user-a',task_id=identity,title='合成保留任务',objective_text='保留用户需求',upload_ids=[upload.upload_id],source_refs=[{'upload_id':upload.upload_id,'sha256':upload.sha256}],output_formats=['csv'],provider='local',model='fixture',external_api_confirmed=False)


def test_new_reference_invalidates_preflight_without_creating_operation(canvas):
    from src.source_acquisition import deletion
    from src.api.auth import get_store
    import pytest
    _,_,task_id,_,upload=canvas
    store=get_store();store.soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id)
    _add_consumer(store,upload)
    with pytest.raises(ValueError,match='confirmation_changed'):
        deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','stale')
    assert deletion.operation_by_key('user-a',task_id,'stale') is None


def test_publish_final_commit_cannot_cross_deletion_intent(canvas):
    import sqlite3
    from types import SimpleNamespace
    from src.config.settings import settings
    from src.source_acquisition import deletion
    from src.api.auth import get_store
    from src.delivery_publishing.repository import DeliveryPublishingRepository
    import pytest
    _,_,task_id,_,upload=canvas
    store=get_store();store.soft_delete_semantic_workspace_task('user-a',task_id)
    plan=deletion.deletion_plan('user-a',task_id)
    deletion.begin_operation('user-a',task_id,plan['plan_token'],'keep_shared','delete-gate')
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("INSERT INTO delivery_publish_intents(publication_key,command_hash,owner_id,task_id,task_revision,run_id,status,created_at,updated_at) VALUES ('synthetic-key','hash','user-a',?,1,'run','committing','now','now')",(task_id,))
    command=SimpleNamespace(owner_id='user-a',task_id=task_id,task_revision=1,publication_key='synthetic-key',frozen_hash=lambda:'hash')
    manifest=SimpleNamespace(model_dump=lambda **kwargs:{})
    with pytest.raises(ValueError,match='source_deleting'):
        DeliveryPublishingRepository(settings.webui_db_path).commit_delivery(command,manifest,__import__('pathlib').Path(settings.semantic_execution_root))
    with sqlite3.connect(settings.webui_db_path) as connection:
        assert connection.execute("SELECT 1 FROM formal_delivery_runs WHERE publication_key='synthetic-key'").fetchone() is None


def test_deleted_identity_projection_requires_exact_hash(canvas):
    from src.source_acquisition.deletion import source_integrity
    from src.config.settings import settings
    import sqlite3
    _,_,_,_,upload=canvas
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("INSERT INTO source_deletion_operations VALUES ('identity-op','user-a','old-task','key','hash','{}','completed',NULL,'now','now')")
        connection.execute("INSERT INTO source_deletions VALUES ('user-a',?,'identity-op',?,NULL,'deleted','now')",('upload:'+upload.upload_id,upload.sha256))
    assert source_integrity('user-a',[{'upload_id':upload.upload_id,'sha256':'wrong'}])['state']=='intact'
    assert source_integrity('user-a',[{'upload_id':upload.upload_id}])['state']=='intact'
    assert source_integrity('user-a',[{'upload_id':upload.upload_id,'sha256':upload.sha256}])['state']=='source_deleted'


def test_history_reads_snapshot_metadata_without_preview(canvas,monkeypatch):
    import sqlite3
    from src.config.settings import settings
    from src.source_acquisition import SourceAcquisitionRepository,reuse
    with sqlite3.connect(settings.webui_db_path) as connection:
        connection.execute("INSERT INTO source_acquisition_attempts(attempt_id,owner_id,idempotency_key,request_hash,request_url,normalized_url,allowed_scope_json,purpose,status,started_at) VALUES ('metadata-attempt','user-a','metadata-key','hash','url','url','{}','合成','succeeded','now')")
        connection.execute("INSERT INTO source_snapshots(snapshot_id,owner_id,attempt_id,allowed_scope_json,valid_page_count,failed_page_count,created_at) VALUES ('metadata-snapshot','user-a','metadata-attempt','{}',0,0,'now')")
    original=SourceAcquisitionRepository.get_snapshot;seen=[]
    def read(self,*args,**kwargs):
        seen.append(kwargs.get('include_preview'))
        assert kwargs.get('include_preview') is False
        return original(self,*args,**kwargs)
    monkeypatch.setattr(SourceAcquisitionRepository,'get_snapshot',read)
    assert reuse.history('user-a')
    assert seen==[False]
