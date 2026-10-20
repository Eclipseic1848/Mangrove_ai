"""金额和日期只兼容受限Markdown转义，原始证据不改写。"""
from datetime import date, datetime, timezone

import pytest

from src.conductor.project_collection import _outside_publication_reason, _validate
from src.conductor.task_spec import ProjectCollectionScope


def validate_money(quote, amount='520.19', unit='万元', *, published='发布时间：2026-10-01',
                   publication_date='2026-10-01', extra='', stage=None):
    name='甲公司智能巡检系统建设项目'
    construction='建设智能巡检系统并部署巡检终端'
    source={'source_id':1,'url':'https://www.sgcc.com.cn/detail','title':name,
        'content':'\n'.join((name,construction,published,quote,extra)),
        'metadata':{'body_status':'provider_raw','source_representation':'provider_markdown'}}
    project={'project_name':name,'construction_content':construction,'publication_date':publication_date,
        'amount':amount,'amount_unit':unit,'evidence':[
            {'field':'project_name','quote':name},{'field':'construction_content','quote':construction},
            {'field':'publication_date','quote':published},{'field':'amount','quote':quote},
            {'field':'amount_unit','quote':quote}]}
    if stage is not None:
        project['current_stage']=stage
        project['evidence'].append({'field':'current_stage','quote':stage})
    scope=ProjectCollectionScope(target_count=1,topics=['智能巡检'],
        publication_from=date(2026,9,1),publication_to=date(2026,10,19),
        frozen_at=datetime(2026,10,19,tzinfo=timezone.utc))
    return _validate(project,source,scope),source,project


def test_decimal_escape_binds_exact_amount_and_keeps_original_evidence():
    quote=r'项目预算520\.19万元'
    (clean,reason),source,proposal=validate_money(quote)
    assert clean is not None,reason
    assert clean['amount']=='520.19' and clean['amount_unit']=='万元'
    assert next(entry['quote'] for entry in clean['evidence'] if entry['field']=='amount')==quote
    assert quote in source['content'] and proposal['amount']=='520.19'


def test_escaped_proposal_amount_is_canonical_but_original_quote_is_unchanged():
    quote=r'项目预算520\.19万元'
    (clean,reason),source,proposal=validate_money(quote,amount=r'520\.19')
    assert clean is not None,reason
    assert clean['amount']=='520.19'
    assert proposal['amount']==r'520\.19'
    assert all(entry['quote'] in source['content'] for entry in clean['evidence'])


def test_table_header_amount_with_decimal_escape_keeps_verified_amount():
    (clean,reason),_,_=validate_money(r'项目预算金额（万元）520\.19')
    assert clean is not None,reason
    assert clean['amount']=='520.19' and clean['amount_unit']=='万元'


@pytest.mark.parametrize('quote,unit',[
    (r'项目预算520\.19元；另有批次金额（万元）','万元'),
    (r'项目预算520\.19万元；另有批次金额（元）','元'),
])
def test_other_header_never_overrides_amount_adjacent_unit(quote,unit):
    (clean,_),source,_=validate_money(quote,unit=unit)
    assert clean is None
    assert quote in source['content']


@pytest.mark.parametrize('quote,amount,unit',[
    (r'项目预算520\.18万元','520.19','万元'),
    (r'项目预算1520\.19万元','520.19','万元'),
    (r'项目预算520\.190万元','520.19','万元'),
    (r'项目预算520\.19万元','520','万元'),
    (r'项目预算-520\.19万元','520.19','万元'),
    (r'项目预算520\.19万元','520.19','元'),
    (r'项目预算520\.19元；另一项目100万元','520.19','万元'),
    (r'项目预算520\,19万元','520.19','万元'),
    (r'项目预算520\_19万元','520.19','万元'),
    (r'项目预算520\\.19万元','520.19','万元'),
])
def test_decimal_escape_never_accepts_wrong_number_boundary_unit_or_other_escape(quote,amount,unit):
    (clean,_),_,_=validate_money(quote,amount,unit)
    assert clean is None


@pytest.mark.parametrize('published',[r'发布时间：2026\-09\-30',r'发布日期：2026\.09\.30'])
def test_escaped_publication_date_keeps_complete_amount_unit_and_raw_quote(published):
    (clean,reason),source,_=validate_money(r'项目预算520\.19万元',published=published,publication_date='2026-09-30')
    assert clean is not None,reason
    assert clean['publication_date']=='2026-09-30'
    assert (clean['amount'],clean['amount_unit'])==('520.19','万元')
    assert next(entry['quote'] for entry in clean['evidence'] if entry['field']=='publication_date')==published
    assert all(entry['quote'] in source['content'] for entry in clean['evidence'])


@pytest.mark.parametrize('published,value',[
    (r'发布时间：2026\-09\-30','2026-09-29'),
    (r'发布时间：2026\\-09\\-30','2026-09-30'),
    (r'发布时间：2026\/09\/30','2026-09-30'),
    (r'发布时间：2026\-09\-300','2026-09-30'),
])
def test_escaped_date_never_accepts_wrong_day_other_escape_or_extra_digit(published,value):
    (clean,_),_,_=validate_money('项目预算520.19万元',published=published,publication_date=value)
    assert clean is None


def test_escaped_publication_outside_frozen_window_still_rejected():
    (clean,reason),_,_=validate_money('项目预算520.19万元',
        published=r'发布时间：2026\-08\-31',publication_date='2026-08-31')
    assert clean is None and reason=='发布时间不在冻结范围内'


@pytest.mark.parametrize('published',[r'发布时间：2026\-08\-31',r'发布日期：2026\.08\.31'])
def test_escaped_explicit_publication_is_excluded_before_model_review(published):
    scope=ProjectCollectionScope(target_count=1,publication_from=date(2026,9,1),publication_to=date(2026,10,19))
    reason=_outside_publication_reason({'content':published},scope)
    assert reason and '2026-08-31' in reason


def test_double_escaped_publication_is_not_used_for_early_date_exclusion():
    scope=ProjectCollectionScope(target_count=1,publication_from=date(2026,9,1))
    assert _outside_publication_reason({'content':r'发布时间：2026\\-08\\-31'},scope) is None


@pytest.mark.parametrize('cutoff',[r'投标截止时间：2026\-10\-05',r'投标截止日期：2026\.10\.05'])
def test_escaped_expired_cutoff_keeps_current_stage_unknown_and_raw_evidence(cutoff):
    (clean,reason),source,_=validate_money('项目预算520.19万元',extra='当前阶段：招标\n'+cutoff,stage='招标')
    assert clean is not None,reason
    assert clean['current_stage'] is None and '截止日已过' in clean['stage_note']
    assert any(entry['field']=='current_stage' and entry['quote']==cutoff for entry in clean['evidence'])
    assert cutoff in source['content']
