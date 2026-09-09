"""混合来源冻结与范围验证合同，不调用真实模型。"""
from types import SimpleNamespace
import hashlib
from src.agentic_runtime.models import PiRuntimeRequest, SourceInput
from src.agentic_runtime.candidate_verifier import _complete_scope_review_evidence


def test_scope_verifier_reviews_web_only_without_relaxing_limits(tmp_path):
    sources=[]
    for id,content in [('A','文件业务内容'*2000),('B','<html><body>网页事实</body></html>')]:
        path=tmp_path/(id+'.html');path.write_text(content,encoding='utf-8')
        sources.append(SourceInput(upload_id=id,original_name=id+'.html',host_path=path,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),media_type='text/html'))
    request=PiRuntimeRequest(model='synthetic',base_url='https://synthetic.invalid',api_key='synthetic-placeholder',user_id='owner',task_id='task',revision=1,objective_text='核网页范围',requested_output_formats=('json',),sources=tuple(sources),source_coverage={'status':'scope_complete','web_artifact_ids':['B']})
    manifest=SimpleNamespace(result_search_complete=True,qualified_omissions=[])
    evidence=_complete_scope_review_evidence(request,manifest,())
    assert evidence is not None and any('B.html' in item for item in evidence)
    assert not any('A.html' in item for item in evidence)
    bad=request.model_copy(update={'source_coverage':{'status':'scope_complete','web_artifact_ids':['missing']}})
    assert _complete_scope_review_evidence(bad,manifest,()) is None
    unknown=request.model_copy(update={'source_coverage':{'status':'coverage_unknown','web_artifact_ids':['B']}})
    assert _complete_scope_review_evidence(unknown,manifest,()) is None


def test_revision_change_preserves_old_upload_refs_and_goal(tmp_path):
    from tests.database_migration_helpers import migrated_webui_database
    from tests.account_execution_helpers import seed_execution_owner
    from src.account_execution import execution_context
    from src.api.store import WebUIStore
    database=migrated_webui_database(tmp_path/'history.db');auth=seed_execution_owner(database);store=WebUIStore(str(database))
    old={'schema_version':1,'goal_contract':{'objective':'保持目标'},'web_sources':[{'source_snapshot_id':'B'}]}
    new={**old,'web_sources':[]}
    refs=[{'upload_id':'A','sha256':'a'},{'kind':'web_artifact','snapshot_id':'B','artifact_id':'b','sha256':'b'}]
    with execution_context(auth):
        store.create_semantic_workspace_task('owner-a',task_id='task',title='合成',objective_text='目标',upload_ids=['A'],output_formats=['json'],provider='local',model=None,external_api_confirmed=False,source_refs=refs,source_contract=old)
        store.create_semantic_workspace_revision('owner-a','task',objective_text='目标',output_formats=['json'],change_summary='明确替换',source_refs=[{'upload_id':'A2','sha256':'a2'}],source_contract=new,expected_revision=2)
    assert store.get_semantic_workspace_revision('owner-a','task',1)['source_refs']==refs
    assert store.get_source_contract('owner-a','task',1)==old
    assert store.get_source_contract('owner-a','task',2)==new
    assert store.get_semantic_workspace_task('owner-a','task')['upload_ids']==['A2']
    assert store.find_web_task_revision_by_snapshot('owner-a','task','B')==1
    import pytest
    with execution_context(auth):
        with pytest.raises(RuntimeError,match='停止'):
            store.create_semantic_workspace_revision('owner-a','task',objective_text='迟到',output_formats=['json'],change_summary='迟到',expected_revision=3,expected_cancel_generation=1)
        with pytest.raises(RuntimeError,match='活动版本'):
            store.create_semantic_workspace_revision('owner-a','task',objective_text='过期',output_formats=['json'],change_summary='过期',expected_revision=2)
    assert store.get_semantic_workspace_revision('owner-a','task',3) is None


def test_removed_web_groups_do_not_discard_strict_result_goal():
    from src.agentic_runtime.coverage import assess_web_candidate, ResultItem
    facts = dict(result_items=(ResultItem(result_id="one",evidence_refs=("file-evidence",)),),scope_complete=False,failed_page_count=0,coverage_unknown=True,observed_page_count=0)
    assert not assess_web_candidate(**facts,target_result_count=10,strict=True).formal_delivery_eligible
    assert not assess_web_candidate(**facts,target_result_count=None,strict=True,require_all=True).formal_delivery_eligible
    assert assess_web_candidate(**facts,target_result_count=None,strict=False).formal_delivery_eligible
