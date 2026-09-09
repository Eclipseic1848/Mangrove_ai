"""工作台来源使用门只包裹实际原件读取，不延长到模型运行。"""
from contextlib import contextmanager
import pytest
from tests.test_coremind_agent_kernel_adapter import _request
from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter


def test_coremind_source_read_uses_injected_guard_and_rejects_changed_entity(tmp_path):
    request=_request(tmp_path)
    entered=[]
    @contextmanager
    def guard(actual, ids):
        entered.append(ids)
        raise ValueError("原实体已失效")
        yield
    adapter=CoreMindAgentKernelAdapter(execution_root=tmp_path,source_read_context=guard)
    with pytest.raises(ValueError,match="原实体已失效"):
        adapter._read_source(request,{"source_id":request.sources[0].upload_id})
    assert entered==[(request.sources[0].upload_id,)]


def test_workspace_factories_install_same_read_guard_without_standalone_database(monkeypatch,tmp_path):
    from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
    from src.source_acquisition.reuse import workspace_source_read_context
    from src.config.settings import settings
    from tests.database_migration_helpers import migrated_webui_database
    monkeypatch.setattr(settings,'webui_db_path',str(migrated_webui_database(tmp_path/'factory.db')))
    monkeypatch.setattr(settings,'semantic_execution_root',str(tmp_path))
    monkeypatch.setattr(settings,'coremind_runtime_enabled',True)
    monkeypatch.setattr(settings,'pi_capability_host_enabled',True)
    manager=SemanticWorkspaceManager()
    kernels=list(manager._agent_kernels.values())+[manager._capability_agent_kernel]
    assert len(kernels)==3
    for kernel in kernels:
        adapter=kernel._adapter
        runtime=getattr(adapter,'_runtime',adapter)
        assert runtime._source_read_context is workspace_source_read_context
    assert CoreMindAgentKernelAdapter(execution_root=tmp_path)._source_read_context is None


def test_coremind_guard_exits_immediately_after_original_read(tmp_path):
    request=_request(tmp_path)
    events=[]
    @contextmanager
    def guard(actual,ids):
        events.append('enter')
        try: yield
        finally: events.append('exit')
    adapter=CoreMindAgentKernelAdapter(execution_root=tmp_path,source_read_context=guard)
    result=adapter._read_source(request,{'source_id':request.sources[0].upload_id})
    assert result and events==['enter','exit']



def test_pi_copy_and_resume_verify_original_before_using_intact_cache(tmp_path):
    from src.agentic_runtime.pi_runtime import PiRuntime
    request=_request(tmp_path)
    source=request.sources[0]
    events=[]
    @contextmanager
    def guard(actual,ids):
        events.append("enter")
        if not source.host_path.is_file(): raise ValueError("原实体已失效")
        try: yield
        finally: events.append("exit")
    runtime=object.__new__(PiRuntime)
    runtime._source_read_context=guard
    inputs=tmp_path/'staged';inputs.mkdir()
    names=runtime._read_frozen_sources(request,inputs)
    assert (inputs/names[0]).read_bytes()==source.host_path.read_bytes()
    assert events==['enter','exit']
    source.host_path.unlink()
    with pytest.raises(ValueError,match='原实体已失效'):
        runtime._read_frozen_sources(request,inputs,resume=True)
    assert (inputs/names[0]).is_file()
    with pytest.raises(ValueError,match='原实体已失效'):
        runtime._read_frozen_sources(request,inputs)


from tests.test_workspace_canvas import canvas,execution_owner


def test_real_workspace_resolver_guards_pi_cache_and_coremind_original(canvas,tmp_path):
    from pathlib import Path
    from src.source_acquisition.reuse import workspace_source_read_context,uploads
    from src.agentic_runtime.pi_runtime import PiRuntime
    from src.agentic_runtime.models import SourceInput
    _,_,task_id,_,upload=canvas
    request=_request(tmp_path).model_copy(update={'task_id':task_id,'user_id':'user-a','revision':1,'sources':(SourceInput(upload_id=upload.upload_id,original_name=upload.original_name,host_path=Path(upload.storage_path),sha256=upload.sha256),)})
    runtime=object.__new__(PiRuntime);runtime._source_read_context=workspace_source_read_context
    staged=tmp_path/'real-stage';staged.mkdir()
    names=runtime._read_frozen_sources(request,staged)
    adapter=CoreMindAgentKernelAdapter(execution_root=tmp_path,source_read_context=workspace_source_read_context)
    assert adapter._read_source(request,{'source_id':upload.upload_id})
    uploads().delete('user-a',upload.upload_id)
    assert (staged/names[0]).is_file()
    with pytest.raises(PermissionError): runtime._read_frozen_sources(request,staged,resume=True)
    with pytest.raises(PermissionError): adapter._read_source(request,{'source_id':upload.upload_id})


@pytest.mark.parametrize('loss',['owner','task'])
def test_real_workspace_read_rejects_current_owner_or_task_loss(canvas,tmp_path,loss):
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from src.source_acquisition.reuse import workspace_source_read_context
    from src.agentic_runtime.models import SourceInput
    from src.config.settings import settings
    from src import account_execution as execution
    _,_,task_id,_,upload=canvas
    request=_request(tmp_path).model_copy(update={'task_id':task_id,'user_id':'user-a','revision':1,'sources':(SourceInput(upload_id=upload.upload_id,original_name=upload.original_name,host_path=Path(upload.storage_path),sha256=upload.sha256),)})
    with closing(sqlite3.connect(settings.webui_db_path)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        if loss=='owner': execution.update_account_status(connection,'user-a',disabled=True,actor_user_id='synthetic-admin',now=1)
        else: connection.execute("UPDATE semantic_workspace_tasks SET deleted_at='synthetic' WHERE task_id=?",(task_id,))
        connection.commit()
    with pytest.raises((execution.ExecutionDenied,PermissionError)):
        with workspace_source_read_context(request,(upload.upload_id,)): pytest.fail('失权后不得进入读取')
