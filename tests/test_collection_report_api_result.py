"""报告任务的最终 API 状态必须服从报告核验，不能按快照有数据标成功。"""
import pytest

from src.api.routes.chat import _build_result, _PERSISTED_RESULT_FIELDS
from tests.test_collection_report_flow import report_state


@pytest.mark.parametrize('passed,has_report,error,expected', [
    (True, True, None, 'output'),
    (False, True, None, 'error'),
    (None, False, None, 'error'),
    (True, True, '报告核验未完成', 'error'),
])
def test_report_api_cannot_promote_collection_snapshot(tmp_path, passed, has_report, error, expected):
    state = report_state(tmp_path)
    state['outputs'] = {'json': str(tmp_path/'data.json')}
    if has_report:
        state['outputs']['report_md'] = str(tmp_path/'report.md')
    state.update(analysis='光大银行私人银行权益分析。', quality={'passed': passed, 'review_complete': True}, error=error)
    result = _build_result('owner', 'conversation', state, '任务结果', 'bound', 'test', state['task_spec'].intent)
    assert result['kind'] == expected
    assert result.get('quality') == state['quality']
    assert any(file['name']=='data.json' for file in result['files'])


@pytest.mark.parametrize('complete', [None, False])
def test_report_api_requires_explicit_complete_review(tmp_path, complete):
    state = report_state(tmp_path)
    state.update(outputs={'json':str(tmp_path/'data.json'),'report_md':str(tmp_path/'report.md')},
                 analysis='报告正文',quality={'passed':True,'review_complete':complete})
    result = _build_result('owner','conversation',state,'资料已保存','bound','test',state['task_spec'].intent)
    assert result['kind']=='error'


def test_snapshot_only_api_keeps_existing_review_kind(tmp_path):
    state = report_state(tmp_path, reports=False)
    state.update(outputs={'json':str(tmp_path/'data.json')}, quality={'passed':False})
    result = _build_result('owner','conversation',state,'资料已保存','bound','test',state['task_spec'].intent)
    assert result['kind']=='output'


@pytest.mark.parametrize('reports,passed,complete,count,expected', [
    (True, True, True, 2, 'completed'),
    (True, False, True, 2, 'failed'),
    (True, True, False, 2, 'failed'),
    (True, True, True, 1, 'failed'),
    (False, True, True, 2, 'needs_input'),
])
def test_persisted_report_history_keeps_verified_completion(tmp_path, reports, passed, complete, count, expected):
    from src.api.store import WebUIStore
    from src.conductor.collection_results import collection_outcome
    from tests.database_migration_helpers import migrated_webui_database
    store = WebUIStore(str(migrated_webui_database(tmp_path/'webui.db')))
    owner = store.create_user('report-owner', 'unused')
    conversation = store.create_conversation(owner['user_id'], '权益报告')
    state = report_state(tmp_path, reports=reports, count=count)
    state.update(collection_outcome=collection_outcome(state['bank_collection']),
                 outputs={'json':str(tmp_path/'data.json')}, analysis='权益报告正文',
                 quality={'passed':passed,'review_complete':complete})
    if reports:
        state['outputs']['report_md'] = str(tmp_path/'report.md')
    result = _build_result(owner['user_id'],conversation['conv_id'],state,'结果已保存','bound','test',state['task_spec'].intent)
    metadata = {key:result.get(key) for key in _PERSISTED_RESULT_FIELDS if result.get(key) not in (None, [], '')}
    assert 'analysis' not in metadata
    store.add_message(conversation['conv_id'],'assistant','结果已保存',task_id=state['task_id'],meta=metadata)
    history = store.list_chat_history(owner['user_id'],include_activity_time=True)[0]
    assert history['status']==expected
    assert (history['completed_at'] is not None)==(expected=='completed')
