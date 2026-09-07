import asyncio
import pytest
import yaml
from src.memory import templates, lessons


def test_private_library_isolated_before_recall_and_legacy_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path)
    templates._templates_cache.invalidate()
    legacy = tmp_path / 'legacy.md'
    legacy.write_text('---\ntitle: 历史秘密\nkeywords: [秘密]\n---\n历史正文\n', encoding='utf-8')
    original = legacy.read_bytes()
    async def create():
        return await templates.save_template('A的秘密', 'generic', ['秘密'], 'A私有正文', owner_id='alice')
    monkeypatch.setattr(templates, 'curate_template', lambda *args, **kwargs: _new())
    slug = asyncio.run(create())
    assert len(templates.load_templates(owner_id='alice')) == 1
    assert templates.load_templates(owner_id='bob') == []
    assert templates.load_templates() == []
    assert not templates.delete_template(slug, owner_id='bob')
    assert legacy.read_bytes() == original


async def _new():
    return {'decision': 'new'}

def test_shared_copy_freezes_content_and_keeps_quality_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path)
    monkeypatch.setattr(templates, 'curate_template', lambda *args, **kwargs: _new())
    slug = asyncio.run(templates.save_template('私有标题', 'generic', ['词'], '私有正文', owner_id='alice'))
    source = templates.load_templates(owner_id='alice')[0]
    from src.memory._library_scope import content_digest
    fields = dict(title='通用标题', keywords=['通用'], body='通用结构', data_type='generic')
    shared = templates.share_template(slug, owner_id='alice', **fields,
        expected_source_digest=source['content_digest'], expected_content_digest=content_digest(fields), confirmed=True)
    assert templates.load_templates(owner_id='bob') == [shared]
    assert templates.record_template_use(shared['slug'], 90, owner_id='bob') == 'draft'
    assert templates.load_templates(owner_id='alice', scope='owner')[0]['uses'] == 0
    assert templates.load_templates(owner_id='bob')[0]['uses'] == 1
    path = tmp_path / (shared['slug'] + '.md')
    path.write_text(path.read_text(encoding='utf-8').replace('通用结构', '私密注入'), encoding='utf-8')
    assert templates.load_templates(owner_id='bob') == []
    assert not templates.record_template_use(shared['slug'], 90, owner_id='bob')

def test_recall_and_curator_use_only_correct_owner_scope(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.conductor.task_spec import TaskSpec
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path)
    monkeypatch.setattr(settings, 'embedding_enabled', False)
    a = asyncio.run(templates.save_template('A秘密', 'generic', ['相同'], 'A正文', owner_id='alice'))
    b = asyncio.run(templates.save_template('B秘密', 'generic', ['相同'], 'B正文', owner_id='bob'))
    assert a != b
    spec = TaskSpec(intent='相同', keywords=['相同'], data_type='generic')
    assert templates.match_template(spec, owner_id='bob')['body'] == 'B正文'
    assert templates.match_template(spec) is None
    assert templates.find_duplicate('generic', ['相同'], owner_id='bob')['slug'] == b
    assert templates.record_template_use(a, 99, owner_id='bob') is None


def test_lessons_private_failure_and_share_active_copy(tmp_path, monkeypatch):
    from src.config.settings import settings
    from src.memory._library_scope import content_digest
    monkeypatch.setattr(lessons, 'LESSONS_DIR', tmp_path)
    monkeypatch.setattr(settings, 'embedding_enabled', False)
    async def distill(*args, **kwargs):
        return dict(title='故障', keywords=['相同'], body='本人教训')
    monkeypatch.setattr(lessons, 'distill_lesson', distill)
    asyncio.run(lessons.record_failure('任务', 'generic', ['相同'], '失败', owner_id='alice'))
    assert lessons.load_lessons(owner_id='bob') == []
    asyncio.run(lessons.record_failure('任务', 'generic', ['相同'], '失败', owner_id='bob'))
    assert len(lessons.load_lessons(owner_id='alice')) == 1
    a = lessons.load_lessons(owner_id='alice')[0]
    assert not lessons.record_lesson_helped(a['slug'], owner_id='bob')
    asyncio.run(lessons.record_failure('任务', 'generic', ['相同'], '失败', owner_id='alice'))
    assert lessons.record_lesson_helped(a['slug'], owner_id='alice')
    a = lessons.load_lessons(owner_id='alice')[0]
    assert a['status'] == 'active'
    fields = dict(title='通用故障', keywords=['相同'], body='通用教训', data_type='generic')
    shared = lessons.share_lesson(a['slug'], owner_id='alice', **fields,
        expected_source_digest=a['content_digest'], expected_content_digest=content_digest(fields), confirmed=True)
    assert shared['status'] == 'active'
    assert shared['occurrences'] == shared['helped_avoid'] == 0
    recalled, _ = lessons.find_active_lessons('generic', ['相同'], '任务', owner_id='bob')
    assert [t['body'] for t in recalled] == ['通用教训']

def test_patrol_cannot_mix_owner_or_modify_platform_and_detects_stale_pair(tmp_path, monkeypatch):
    from src.memory._library_scope import content_digest
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path)
    monkeypatch.setattr(templates, 'curate_template', lambda *args, **kwargs: _new())
    for who in ('alice', 'bob'):
        asyncio.run(templates.save_template(who, 'generic', ['词'], who, owner_id=who))
    a = templates.load_templates(owner_id='alice')[0]
    b = templates.load_templates(owner_id='bob')[0]
    called = []
    async def llm(*args, **kwargs):
        called.append(args)
        return '{"title":"融合","keywords":["词"],"body":"融合正文"}'
    monkeypatch.setattr(templates, 'achat', llm)
    assert asyncio.run(templates.merge_template_pair(a, b, owner_id='alice')) is None
    assert called == []
    assert not templates.apply_patrol_merge(a['slug'], dict(title='替换', keywords=[], body='越权'), owner_id='bob', expected_source_digest=content_digest(a))
    assert templates.load_templates(owner_id='alice')[0]['body'] == 'alice'
    with pytest.raises(ValueError):
        templates.delete_template('../outside', owner_id='alice')

def test_direct_distillation_rejects_missing_owner_before_model(tmp_path, monkeypatch):
    calls = []
    async def model(*args, **kwargs):
        calls.append(args)
        return '{"body":"生成正文"}'
    monkeypatch.setattr(templates, 'achat', model)
    monkeypatch.setattr(lessons, 'achat', model)
    with pytest.raises(PermissionError):
        asyncio.run(templates.distill_template('任务', 'generic', '正文'))
    with pytest.raises(PermissionError):
        asyncio.run(lessons.record_failure('任务', 'generic', [], '失败'))
    assert calls == []

def test_embedding_rerank_curator_and_lesson_model_inputs_are_owner_scoped(tmp_path, monkeypatch):
    import json
    from src.config.settings import settings
    from src.memory import embeddings
    from src.memory._library_scope import content_digest
    from src.conductor.task_spec import TaskSpec
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path / 'templates')
    monkeypatch.setattr(lessons, 'LESSONS_DIR', tmp_path / 'lessons')
    monkeypatch.setattr(settings, 'embedding_enabled', False)
    a_slug = asyncio.run(templates.save_template('A_SECRET_TITLE', 'generic', ['common'], 'A_SECRET_BODY', owner_id='alice'))
    b_slug = asyncio.run(templates.save_template('B_TITLE', 'generic', ['common'], 'B_BODY', owner_id='bob'))
    a = templates.load_templates(owner_id='alice')[0]
    fields = dict(title='PUBLIC_TITLE', data_type='generic', keywords=['common'], body='PUBLIC_BODY')
    shared = templates.share_template(a_slug, owner_id='alice', **fields, expected_source_digest=a['content_digest'], expected_content_digest=content_digest(fields), confirmed=True)
    lessons.LESSONS_DIR.mkdir()
    for who, title in [('alice', 'A_SECRET_LESSON'), ('bob', 'B_LESSON')]:
        meta = dict(owner_id=who, scope='owner', title=title, keywords=['common'], data_type='generic', status='active', occurrences=2, helped_avoid=1)
        (lessons.LESSONS_DIR / (who + '.md')).write_text('---\n' + yaml.safe_dump(meta) + '---\n' + title, encoding='utf-8')
    seen = []
    def embed(texts):
        seen.extend(texts)
        return 'fake', [[1., 0.] for _ in texts]
    def rerank(query, docs, **kwargs):
        seen.extend([query, *docs])
        return [.95] * len(docs)
    async def curate(messages, **kwargs):
        seen.extend(m['content'] for m in messages)
        return json.dumps(dict(decision='merge', slug=b_slug, title='B_UPDATED', keywords=['common'], body='B_UPDATED_BODY'))
    monkeypatch.setattr(settings, 'embedding_enabled', True)
    monkeypatch.setattr(embeddings, 'embed_texts_with_model', embed)
    monkeypatch.setattr(embeddings, 'is_rerank_configured', lambda: True)
    monkeypatch.setattr(embeddings, 'rerank_scores', rerank)
    monkeypatch.setattr(templates, 'achat', curate)
    spec = TaskSpec(intent='common', keywords=['common'], data_type='generic')
    assert templates.match_template(spec, owner_id='bob')
    assert lessons.find_active_lessons('generic', ['common'], 'common', owner_id='bob')[0]
    assert asyncio.run(templates.save_template('B_NEW', 'generic', ['common'], 'B_NEW_BODY', owner_id='bob')) == b_slug
    assert 'B_BODY' in '\n'.join(seen)
    assert 'A_SECRET' not in '\n'.join(seen)
    assert templates.load_templates(owner_id='bob', scope='platform')[0]['body'] == shared['body']
    assert templates.load_templates(owner_id='alice', scope='owner')[0]['body'] == 'A_SECRET_BODY'


def test_late_curator_result_and_share_retries_are_safe(tmp_path, monkeypatch):
    from src.memory._library_scope import content_digest
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path)
    monkeypatch.setattr(templates, 'curate_template', lambda *args, **kwargs: _new())
    slug = asyncio.run(templates.save_template('原件', 'generic', ['词'], '正文', owner_id='alice'))
    source = templates.load_templates(owner_id='alice')[0]
    fields = dict(title='通用', keywords=['词'], data_type='generic', body='通用正文')
    kwargs = dict(owner_id='alice', **fields, expected_source_digest=source['content_digest'], expected_content_digest=content_digest(fields), confirmed=True)
    shared = templates.share_template(slug, **kwargs)
    templates.record_template_use(shared['slug'], 90, owner_id='bob')
    assert templates.share_template(slug, **kwargs)['uses'] == 1
    assert len(templates.load_templates(owner_id='bob')) == 1
    async def late(*args, **kwargs):
        assert templates.apply_patrol_merge(slug, dict(title='新版本', keywords=['新词'], body='新正文'), owner_id='alice', expected_source_digest=source['content_digest'])
        return dict(decision='merge', slug=slug, source_digest=source['content_digest'], title='迟到', keywords=['词'], body='迟到正文')
    monkeypatch.setattr(templates, 'curate_template', late)
    assert asyncio.run(templates.save_template('新增', 'generic', ['词'], '新增正文', owner_id='alice')) is None
    assert templates.load_templates(owner_id='alice', scope='owner')[0]['body'] == '新正文'
    with pytest.raises(ValueError):
        templates.share_template(slug, **kwargs)
    assert not templates.delete_template(shared['slug'], owner_id='bob')
    assert templates.delete_template(shared['slug'], owner_id='alice')
    assert templates.load_templates(owner_id='alice', scope='owner')[0]['body'] == '新正文'

def test_platform_lesson_failure_counter_retires_without_changing_private_source(tmp_path, monkeypatch):
    from src.memory._library_scope import content_digest
    monkeypatch.setattr(lessons, 'LESSONS_DIR', tmp_path)
    meta = dict(owner_id='alice', scope='owner', title='来源', keywords=['词'], data_type='generic', status='active', occurrences=2, helped_avoid=1)
    (tmp_path / 'source.md').write_text('---\n' + yaml.safe_dump(meta) + '---\n本人教训', encoding='utf-8')
    source = lessons.load_lessons(owner_id='alice')[0]
    fields = dict(title='通用', keywords=['词'], data_type='generic', body='通用正文')
    shared = lessons.share_lesson('source', owner_id='alice', **fields, expected_source_digest=source['content_digest'], expected_content_digest=content_digest(fields), confirmed=True)
    for _ in range(10):
        assert lessons.record_lesson_failure(shared['slug'], owner_id='bob')
    final = lessons.load_lessons(owner_id='bob')[0]
    assert final['status'] == 'retired' and final['occurrences'] == 10
    assert final['content_digest'] == shared['content_digest']
    assert lessons.load_lessons(owner_id='alice', scope='owner')[0] == source

def test_real_consumers_use_execution_owner_and_private_draft_can_be_verified(tmp_path, monkeypatch):
    import importlib
    import json
    from src.account_execution import ExecutionAuthorization, execution_context
    from src.api.execution import execution_validation
    from src.config.settings import settings
    from src.conductor.task_spec import TaskSpec
    analyze = importlib.import_module('src.conductor.nodes.analyze')
    checker = importlib.import_module('src.conductor.nodes.checker')
    planner = importlib.import_module('src.conductor.nodes.planner')
    monkeypatch.setattr('src.api.auth.get_store', lambda: None)
    monkeypatch.setattr(settings, 'embedding_enabled', False)
    monkeypatch.setattr(settings, 'checker_enabled', True)
    monkeypatch.setattr(settings, 'checker_rerun_enabled', False)
    monkeypatch.setattr(settings, 'template_learning_enabled', True)
    monkeypatch.setattr(settings, 'lesson_learning_enabled', True)
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path / 'templates')
    monkeypatch.setattr(lessons, 'LESSONS_DIR', tmp_path / 'lessons')
    spec = TaskSpec(intent='common', keywords=['common'], data_type='generic', analysis_type='summary')
    calls = []
    async def lesson_model(messages, **kwargs):
        calls.append(('lesson', messages))
        return json.dumps(dict(title='B_LESSON', keywords=['common'], body='B_LESSON_BODY'))
    async def template_model(messages, **kwargs):
        calls.append(('template', messages))
        return json.dumps(dict(title='B_TEMPLATE', keywords=['common'], body='B_TEMPLATE_BODY'))
    async def check_model(messages, **kwargs):
        return json.dumps(dict(score=90, issues=[], summary='通过'))
    async def consumer_model(messages, **kwargs):
        calls.append(('consumer', messages))
        return json.dumps(dict(intent='common', keywords=['common'], data_type='generic', analysis_type='summary'))
    async def route(*args, **kwargs):
        return None
    monkeypatch.setattr(lessons, 'achat', lesson_model)
    monkeypatch.setattr(templates, 'achat', template_model)
    monkeypatch.setattr(checker, 'achat', check_model)
    monkeypatch.setattr(planner, 'achat', consumer_model)
    monkeypatch.setattr(analyze, 'achat', consumer_model)
    monkeypatch.setattr(analyze, '_classify_route_llm', route)
    healthy = [{'title': '数据', 'content': '有效内容'}] * 30
    state = dict(task_spec=spec, analysis='正常报告', analysis_source='fallback', cleaned_dataset=healthy, owner_user_id='alice')
    with execution_context(ExecutionAuthorization('bob', 0)), execution_validation(lambda auth: None):
        assert asyncio.run(checker.checker_node(state))['template_saved']
        for _ in range(2):
            asyncio.run(checker.checker_node({**state, 'cleaned_dataset': []}))
        draft = lessons.load_lessons(owner_id='bob')[0]
        assert draft['status'] == 'draft' and draft['occurrences'] == 2
        asyncio.run(planner.planner_node(dict(user_input='common', owner_user_id='alice')))
        analyzed = asyncio.run(analyze.analyze_node(state))
        assert analyzed.get('active_lesson_slug') == draft['slug']
        asyncio.run(checker.checker_node({**state, **analyzed, 'analysis': '正常报告'}))
        assert lessons.load_lessons(owner_id='bob')[0]['status'] == 'active'
    assert templates.load_templates(owner_id='alice') == []
    assert lessons.load_lessons(owner_id='alice') == []
    assert any('B_LESSON_BODY' in json.dumps(messages) for kind, messages in calls if kind == 'consumer')
    calls.clear()
    assert 'template_saved' not in asyncio.run(checker.checker_node(state))
    unowned = asyncio.run(analyze.analyze_node(state))
    asyncio.run(planner.planner_node(dict(user_input='common', owner_user_id='bob')))
    assert not unowned.get('active_lesson_slug') and not unowned.get('template_slug')
    assert not any(kind in {'lesson', 'template'} for kind, _ in calls)
    assert 'B_LESSON_BODY' not in json.dumps(calls) and 'B_TEMPLATE_BODY' not in json.dumps(calls)

def test_share_does_not_overwrite_unknown_file_at_stable_copy_path(tmp_path, monkeypatch):
    from src.memory._library_scope import content_digest
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path)
    monkeypatch.setattr(templates, 'curate_template', lambda *args, **kwargs: _new())
    slug = asyncio.run(templates.save_template('来源', 'generic', ['词'], '正文', owner_id='alice'))
    source = templates.load_templates(owner_id='alice')[0]
    fields = dict(title='通用', keywords=['词'], data_type='generic', body='通用正文')
    digest = content_digest(fields)
    shared = templates.share_template(slug, owner_id='alice', **fields, expected_source_digest=source['content_digest'], expected_content_digest=digest, confirmed=True)
    path = tmp_path / (shared['slug'] + '.md')
    path.write_text('历史无归属原件', encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        templates.share_template(slug, owner_id='alice', **fields, expected_source_digest=source['content_digest'], expected_content_digest=digest, confirmed=True)
    assert path.read_bytes() == before

@pytest.mark.parametrize('module,delete', [(templates, templates.delete_template), (lessons, lessons.delete_lesson)])
@pytest.mark.parametrize('slug', ['../outside', 'a/b', 'a\\b', 'a:b', '.', 'CON', 'NUL', 'COM1'])
def test_library_public_delete_rejects_unsafe_paths(module, delete, slug, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'TEMPLATES_DIR' if module is templates else 'LESSONS_DIR', tmp_path)
    with pytest.raises(ValueError):
        delete(slug, owner_id='alice')


def test_library_does_not_follow_symlink(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    library.mkdir()
    external = tmp_path / 'external.md'
    external.write_text('---\nowner_id: alice\nscope: owner\ntitle: 秘密\n---\n外部秘密', encoding='utf-8')
    link = library / 'linked.md'
    try:
        link.symlink_to(external)
    except OSError as exc:
        pytest.skip(f'当前 Windows 无创建符号链接权限：{exc}')
    before = external.read_bytes()
    for module, load, delete in [(templates, templates.load_templates, templates.delete_template), (lessons, lessons.load_lessons, lessons.delete_lesson)]:
        monkeypatch.setattr(module, 'TEMPLATES_DIR' if module is templates else 'LESSONS_DIR', library)
        assert load(owner_id='alice') == []
        with pytest.raises(ValueError):
            delete('linked', owner_id='alice')
    assert external.read_bytes() == before

def test_patrol_ignores_unknown_originals_and_platform_body(tmp_path, monkeypatch):
    import json
    from src.api.library_dedup_scanner import LibraryDedupScanner
    from src.config.settings import settings
    from src.memory._library_scope import content_digest
    monkeypatch.setattr(templates, 'TEMPLATES_DIR', tmp_path / 'templates')
    monkeypatch.setattr(lessons, 'LESSONS_DIR', tmp_path / 'lessons')
    monkeypatch.setattr(settings, 'embedding_enabled', False)
    slug = asyncio.run(templates.save_template('本人原件', 'generic', ['词'], '本人正文', owner_id='alice'))
    source = templates.load_templates(owner_id='alice')[0]
    fields = dict(title='平台标题', keywords=['词'], data_type='generic', body='平台正文')
    shared = templates.share_template(slug, owner_id='alice', **fields, expected_source_digest=source['content_digest'], expected_content_digest=content_digest(fields), confirmed=True)
    originals = {}
    for directory in (templates.TEMPLATES_DIR, lessons.LESSONS_DIR):
        directory.mkdir(exist_ok=True)
        path = directory / 'legacy.md'
        path.write_text('---\ntitle: UNKNOWN_SECRET\nstatus: draft\ncreated_at: 2000-01-01T00:00:00\n---\nUNKNOWN_BODY', encoding='utf-8')
        originals[path] = path.read_bytes()
    async def forbidden(*args, **kwargs):
        raise AssertionError('平台正文和未知历史不得进入融合')
    monkeypatch.setattr(templates, 'achat', forbidden)
    monkeypatch.setattr(lessons, 'achat', forbidden)
    scan = LibraryDedupScanner()
    tpl_stats = asyncio.run(scan._dedup_pass_templates())
    lsn_stats = asyncio.run(scan._dedup_pass_lessons())
    assert tpl_stats[1] == lsn_stats[1] == 0
    assert scan._stale_pass_templates()[0] == scan._stale_pass_lessons()[0] == 0
    assert 'UNKNOWN' not in json.dumps([tpl_stats, lsn_stats])
    assert templates.load_templates(owner_id='bob')[0]['body'] == shared['body']
    for path, before in originals.items():
        assert path.read_bytes() == before

@pytest.mark.parametrize('scope', ['owner', 'platform'])
def test_checker_high_score_collection_failure_does_not_credit_lesson(scope, tmp_path, monkeypatch):
    import importlib
    import json
    from src.account_execution import ExecutionAuthorization, execution_context
    from src.api.execution import execution_validation
    from src.config.settings import settings
    from src.conductor.task_spec import TaskSpec
    from src.memory._library_scope import content_digest
    checker = importlib.import_module('src.conductor.nodes.checker')
    monkeypatch.setattr(lessons, 'LESSONS_DIR', tmp_path)
    monkeypatch.setattr(settings, 'checker_enabled', True)
    monkeypatch.setattr(settings, 'lesson_learning_enabled', True)
    monkeypatch.setattr(settings, 'template_learning_enabled', True)
    monkeypatch.setattr(settings, 'embedding_enabled', False)
    fields = dict(title='故障教训', keywords=['故障'], body='应对建议', data_type='generic')
    meta = {**fields, 'owner_id': 'bob', 'scope': 'owner', 'status': 'draft' if scope == 'owner' else 'active', 'occurrences': 2, 'helped_avoid': 0 if scope == 'owner' else 1}
    body = meta.pop('body')
    (tmp_path / 'source.md').write_text('---\n' + yaml.safe_dump(meta, allow_unicode=True) + '---\n' + body, encoding='utf-8')
    source = lessons.load_lessons(owner_id='bob')[0]
    if scope == 'platform':
        entry = lessons.share_lesson('source', owner_id='bob', **fields, expected_source_digest=source['content_digest'], expected_content_digest=content_digest(fields), confirmed=True)
    else:
        entry = source
    async def quality_model(*args, **kwargs):
        return json.dumps(dict(score=95, issues=[], summary='报告结构完整'))
    async def lesson_model(*args, **kwargs):
        return json.dumps(fields, ensure_ascii=False)
    monkeypatch.setattr(checker, 'achat', quality_model)
    monkeypatch.setattr(lessons, 'achat', lesson_model)
    spec = TaskSpec(intent='故障', keywords=['故障'], data_type='generic', analysis_type='summary')
    state = dict(task_spec=spec, analysis='采集失败但报告格式完整', analysis_source='fallback', cleaned_dataset=[], active_lesson_slug=entry['slug'])
    with execution_context(ExecutionAuthorization('bob', 0)), execution_validation(lambda auth: None):
        for _ in range(10 if scope == 'platform' else 1):
            result = asyncio.run(checker.checker_node(state))
            assert result['quality']['passed'] is True
        final = next(t for t in lessons.load_lessons(owner_id='bob') if t['slug'] == entry['slug'])
    assert final['helped_avoid'] == 0
    assert final['status'] == ('retired' if scope == 'platform' else 'draft')
    assert final['occurrences'] == (10 if scope == 'platform' else 3)
    assert final['content_digest'] == entry['content_digest']
