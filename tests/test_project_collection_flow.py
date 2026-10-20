"""项目任务必须按有原文依据的独立项目计数。"""
import json
from datetime import date, datetime, timezone

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.conductor.nodes import collect
from src.conductor.task_spec import ProjectCollectionScope, SearchQuery, TaskSpec


def spec():
    return TaskSpec(intent="官网近90天智能巡检项目，目标8个，不足如实说明", max_items=8,
        site_domains=["official.example"], project_collection=ProjectCollectionScope(
            target_count=8, topics=["智能巡检"], allow_shortfall=True,
            publication_from=date(2026, 7, 17), publication_to=date(2026, 10, 15),
            queries=[SearchQuery(name="巡检", keyword="智能巡检 项目")]))


def proposal(name="A智能巡检项目", number="A-01", amount="100"):
    return {"project_name": name, "project_number": number, "owner": "甲公司", "region": None,
        "construction_content": "建设智能巡检系统", "amount": amount, "amount_unit": "万元",
        "publication_date": "2026-10-01", "current_stage": "招标",
        "evidence": [{"field": field, "quote": quote} for field, quote in {
            "project_name": name, "project_number": number, "owner": "甲公司",
            "construction_content": "建设智能巡检系统", "amount": "100万元",
            "amount_unit": "100万元", "publication_date": "发布时间：2026-10-01",
            "current_stage": "招标"}.items()]}


def item(url="https://official.example/a", extra=""):
    return CollectedItem(url=url, title="A智能巡检项目招标公告",
        content="A智能巡检项目，编号A-01，业主甲公司，建设智能巡检系统，预算100万元，发布时间：2026-10-01，招标。" + extra,
        metadata={"body_status": "complete"})


@pytest.mark.parametrize("amount,unit,text", [
    ("10", "万元", "100万元"), ("100", "元", "100万元"),
    ("100", "万元", "1000万元"), ("100", "万元", "-100万元"),
    ("100", "亿元", "预算100万元；另一项目200亿元"),
])
def test_money_cannot_bind_to_partial_number_unit_or_another_amount(amount, unit, text):
    from src.conductor.project_collection import _validate
    record = proposal(amount=amount)
    record["amount_unit"] = unit
    for evidence in record["evidence"]:
        if evidence["field"] in ("amount", "amount_unit"):
            evidence["quote"] = text
    source = item().to_dict()
    source.update(source_id=1, content=source["content"].replace("100万元", text))
    assert _validate(record, source, spec().project_collection)[0] is None


def test_repeated_publication_date_needs_article_specific_quote():
    from src.conductor.project_collection import _validate
    source = item(extra="\n另一项目，发布时间：2026-10-01").to_dict()
    source["source_id"] = 1
    assert _validate(proposal(), source, spec().project_collection)[0] is None
    record = proposal()
    for evidence in record["evidence"]:
        if evidence["field"] == "publication_date":
            evidence["quote"] = "预算100万元，发布时间：2026-10-01，招标。"
    assert _validate(record, source, spec().project_collection)[0] is not None


def test_whitespace_only_quote_is_restored_to_contiguous_original_span():
    from src.conductor.project_collection import _validate
    source = {**item().to_dict(), "source_id": 1}
    source["content"] = source["content"].replace("业主甲公司", "采购\n人为\u00a0甲公司")
    record = proposal()
    owner_evidence = next(entry for entry in record["evidence"] if entry["field"] == "owner")
    owner_evidence["quote"] = "采购人为甲公司"
    clean, reason = _validate(record, source, spec().project_collection)
    assert clean, reason
    restored = next(entry["quote"] for entry in clean["evidence"] if entry["field"] == "owner")
    assert restored == "采购\n人为\u00a0甲公司"
    assert restored in source["content"]
    assert owner_evidence["quote"] == "采购人为甲公司"
    assert "实际需求或使用单位" in clean["field_review_notes"]["owner"]


def test_announcement_region_keeps_its_original_scope_in_review_notes():
    from src.conductor.project_collection import _validate
    source = {**item(extra="招标项目所在地区：广州").to_dict(), "source_id": 1}
    record = proposal()
    record["region"] = "广州"
    record["evidence"].append({"field": "region", "quote": "招标项目所在地区：广州"})
    clean, reason = _validate(record, source, spec().project_collection)
    assert clean, reason
    assert clean["region"] == "广州"
    assert "不代表已确认具体建设或研发实施场地" in clean["field_review_notes"]["region"]


@pytest.mark.parametrize("value,original,quote", [
    ("2026-10-01", "发布时间：2026 年\n10 月 01 日", "发布时间：2026年10月01日"),
    ("2026-08-21", "采购公告\n发布时间：\u00a02026-08-21 17:51:54 \u00a0 来源：\n本站原创",
        "采购公告\n发布时间： 2026-08-21 17:51:54  来源：\n本站原创"),
])
def test_whitespace_in_publication_date_keeps_original_span_and_iso_value(value, original, quote):
    from src.conductor.project_collection import _references, _validate
    source = {**item().to_dict(), "source_id": 1}
    source["content"] = source["content"].replace("发布时间：2026-10-01", original)
    record = proposal()
    record["publication_date"] = value
    next(entry for entry in record["evidence"] if entry["field"] == "publication_date")["quote"] = quote
    clean, reason = _validate(record, source, spec().project_collection)
    assert clean, reason
    assert clean["publication_date"] == value
    assert next(entry["quote"] for entry in clean["evidence"] if entry["field"] == "publication_date") == original
    assert _references(clean, {1: source}, spec().project_collection)


@pytest.mark.parametrize("mode", ["ambiguous", "different_character", "separate_parts", "repeated_date"])
def test_whitespace_quote_repair_never_guesses_or_joins_source_parts(mode):
    from src.conductor.project_collection import _validate
    source = {**item().to_dict(), "source_id": 1}
    record = proposal()
    field, quote = "owner", "采购人为甲公司"
    if mode == "ambiguous":
        source["content"] = source["content"].replace("业主甲公司", "采购\n人为甲公司；采购人\n为甲公司")
    elif mode == "different_character":
        source["content"] = source["content"].replace("业主甲公司", "采购\n人为乙公司")
    elif mode == "separate_parts":
        source["title"], source["content"] = "采购人", "为甲公司；" + source["content"]
    else:
        field, quote = "publication_date", "发布时间：2026年10月01日"
        source["content"] = source["content"].replace("2026-10-01", "2026 年 10 月 01 日") + "；发布时间：2026年 10月 01日"
    next(entry for entry in record["evidence"] if entry["field"] == field)["quote"] = quote
    assert _validate(record, source, spec().project_collection)[0] is None


@pytest.mark.asyncio
async def test_named_deployment_news_and_research_project_are_not_restricted_to_announcements(monkeypatch):
    from src.conductor import project_collection as module
    for prompt in (module._EXTRACT, module._CHECK):
        assert "新闻" in prompt and "侧记" in prompt and "研发" in prompt
        assert "公告" in prompt and "编号" in prompt and "金额" in prompt
    record = proposal(number=None, amount=None)
    record.update(owner=None, amount_unit=None, current_stage=None)
    record["evidence"] = [entry for entry in record["evidence"] if record[entry["field"]] is not None]
    calls = []
    async def review(messages, **kwargs):
        calls.append(messages[0]["content"])
        if messages[0]["content"] == module._CHECK:
            payload = json.loads(messages[-1]["content"])
            assert payload["project"]["owner"] is None
            return json.dumps({"passed": True, "reason": "具名系统有部署建设事实，业主不明确保持空值"})
        return json.dumps({"projects": [record], "reason": "具名系统已部署，作为候选独立核验"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", review)
    source = item().to_dict()
    source["title"] = "智能巡检系统建设部署侧记"
    result = await module.review_project_sources({"task_spec": spec()}, [source])
    assert len(result["projects"]) == 1
    assert result["projects"][0]["owner"] is None
    assert calls == [module._EXTRACT, module._CHECK]


def test_same_date_in_separate_announcements_keeps_individual_source_binding():
    from src.conductor import project_collection as module
    sources = {sid: {**item(f"https://official.example/{sid}").to_dict(), "source_id": sid} for sid in (1, 2)}
    projects = []
    for source in sources.values():
        candidate, _ = module._validate(proposal(), source, spec().project_collection)
        module._merge(projects, candidate)
    assert module._references(projects[0], sources, spec().project_collection)


@pytest.mark.parametrize("amount,unit,quote", [("100", "USD", "预算USD 100"),
    ("100", "$", "预算$100"), ("100", "万元", "预算100，单位：万元"),
    ("1,000.50", "万元", "预算1,000.50万元")])
def test_complete_money_keeps_currency_prefix_table_unit_and_decimal(amount, unit, quote):
    from src.conductor.project_collection import _validate
    record = proposal(amount=amount)
    record["amount_unit"] = unit
    for evidence in record["evidence"]:
        if evidence["field"] in ("amount", "amount_unit"):
            evidence["quote"] = quote
    source = item().to_dict()
    source.update(source_id=1, content=source["content"].replace("100万元", quote))
    assert _validate(record, source, spec().project_collection)[0] is not None


def test_table_number_interference_keeps_project_with_unverified_amount_empty():
    from src.conductor import project_collection as module
    quote = "标的\n概算金额（万元）\n标包编码\n标包金额（万元）\n1\nCG20261001001\n智能巡检系统配套设备\n43.8728"
    source = {**item().to_dict(), "source_id": 1}
    source["content"] = source["content"].replace("100万元", quote)
    record = proposal(amount="43.8728")
    for entry in record["evidence"]:
        if entry["field"] in ("amount", "amount_unit"):
            entry["quote"] = quote
    clean, reason = module._validate(record, source, spec().project_collection)
    assert clean, reason
    assert clean["amount"] is None and clean["amount_unit"] == "万元"
    assert "amount" in clean["missing_fields"]
    assert "对应关系未核实" in clean["amount_scope_note"]
    assert not any(entry["field"] == "amount" for entry in clean["evidence"])
    assert next(entry["quote"] for entry in clean["evidence"] if entry["field"] == "amount_unit") == quote
    assert record["amount"] == "43.8728"
    assert module._references(clean, {1: source}, spec().project_collection)


@pytest.mark.parametrize("amount_text", ["100万元", "USD 100"])
def test_table_header_cannot_preserve_unit_from_another_explicit_amount(amount_text):
    from src.conductor.project_collection import _validate
    quote = f"预算{amount_text}；另一项目金额（亿元）200"
    source = {**item().to_dict(), "source_id": 1}
    source["content"] = source["content"].replace("100万元", quote)
    record = proposal()
    record["amount_unit"] = "亿元"
    for entry in record["evidence"]:
        if entry["field"] in ("amount", "amount_unit"):
            entry["quote"] = quote
    assert _validate(record, source, spec().project_collection)[0] is None


@pytest.mark.parametrize("stage", [None, "招标", "中标"])
def test_expired_bid_is_current_unknown_and_package_unit_does_not_invent_total(stage):
    from src.conductor import project_collection as module
    source = {**item().to_dict(), "source_id": 1}
    source["content"] = source["content"].replace("预算100万元，", "") + (
        "\n投标文件递交截止时间：2026年09月18日09时00分\n预计需求金额（万元）\n519.88\n标包金额\n（万元）\n203.55\n中标")
    record = proposal(amount=None)
    record.update(amount_unit=None, current_stage=stage)
    record["evidence"] = [entry for entry in record["evidence"] if entry["field"] not in ("amount", "amount_unit", "current_stage")]
    if stage:
        record["evidence"].append({"field": "current_stage", "quote": stage})
    scope = spec().project_collection.model_copy(update={"frozen_at": datetime(2026, 10, 15, tzinfo=timezone.utc)})
    clean, reason = module._validate(record, source, scope)
    assert clean, reason
    assert clean["amount"] is None and clean["amount_unit"] == "万元"
    assert clean["current_stage"] == ("中标" if stage == "中标" else None)
    assert "stage_note" in clean
    if stage == "中标":
        assert "原文时点" in clean["stage_note"] and "2026-10-01" in clean["stage_note"]
    assert "自行加总" in clean["amount_scope_note"]
    projects = []
    module._merge(projects, clean)
    content = json.loads(module.project_records({"projects": projects, "sources": [source]})[0]["content"])
    assert content["announcement_count"] == 1
    assert content["field_evidence"] and content["amount_scope_note"]
    assert module._references(projects[0], {1: source}, scope)


@pytest.mark.parametrize("header", ["标包金额（含税）", "标包金额（不含税）"])
def test_tax_description_is_not_automatically_used_as_currency(header):
    from src.conductor.project_collection import _validate
    source = {**item().to_dict(), "source_id": 1}
    source["content"] += "\n" + header + "\n519.88"
    record = proposal(amount=None)
    record["amount_unit"] = None
    record["evidence"] = [entry for entry in record["evidence"] if entry["field"] not in ("amount", "amount_unit")]
    clean, _ = _validate(record, source, spec().project_collection)
    assert clean and clean["amount_unit"] is None


@pytest.mark.asyncio
async def test_project_path_counts_independent_projects_and_keeps_excluded_sources(monkeypatch):
    class Search:
        async def collect(self, _spec):
            return CollectResult(True, "search", items=[item(), item("https://official.example/b"),
                CollectedItem(url="https://official.example/jobs", title="招聘公告", content="招聘毕业生" * 100, metadata={"body_status":"complete"})])
    monkeypatch.setattr(collect, "get_registry", lambda: {"search": Search()})
    from src.conductor import project_collection as module
    monkeypatch.setattr(module, "get_registry", lambda: {"search": Search()})
    async def extract(messages, **kwargs):
        if messages[0]["content"] == module._CHECK:
            return json.dumps({"passed": True, "reason": "项目引用明确"})
        if "招聘毕业生" in messages[-1]["content"]:
            return json.dumps({"projects": [], "reason": "招聘不是项目公告"})
        return json.dumps({"projects": [proposal()], "reason": "项目原文明确"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", extract)
    result = await collect.collect_node({"task_spec": spec(), "collector_candidates": ["search"]})
    snapshot = result["project_result"]
    assert len(snapshot["projects"]) == 1
    assert snapshot["projects"][0]["source_ids"] == [1, 2]
    assert snapshot["coverage"]["shortfall"] == 7
    assert snapshot["coverage"]["target_met"] is False
    assert len(snapshot["sources"]) == 3
    assert snapshot["excluded"][0]["source_id"] == 3
    assert "search_stop_reason" in snapshot["coverage"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invented_quote", "invented_amount", "outside", "old", "future", "missing_date"])
async def test_project_record_cannot_enter_without_source_bound_fields(monkeypatch, mode):
    from src.conductor import project_collection as module
    source = item("https://outside.example/a" if mode == "outside" else "https://official.example/a")
    record = proposal()
    if mode == "invented_quote":
        record["evidence"][0]["quote"] = "来源中没有这段话"
    if mode == "invented_amount":
        record["amount"] = "999"
    if mode in ("old", "future"):
        value = "2025-10-01" if mode == "old" else "2027-10-01"
        source.content = source.content.replace("2026-10-01", value)
        record["publication_date"] = value
        record["evidence"][-2]["quote"] = "发布时间：" + value
    if mode == "missing_date":
        record["publication_date"] = None
        record["evidence"] = [e for e in record["evidence"] if e["field"] != "publication_date"]
    async def extract(*args, **kwargs):
        return json.dumps({"projects": [record], "reason": "模型声称相关"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", extract)
    result = await module.review_project_sources({"task_spec": spec()}, [source.to_dict()])
    assert result["projects"] == []
    assert result["excluded"]


@pytest.mark.asyncio
async def test_source_tail_is_reviewed_and_unknown_is_not_retried(monkeypatch):
    from src.conductor import project_collection as module
    calls = []
    source = item(extra="甲" * 15000 + "末尾独有依据")
    async def extract(messages, **kwargs):
        calls.append(messages[-1]["content"])
        return json.dumps({"projects": [], "reason": "无项目"})
    monkeypatch.setattr(module, "achat", extract)
    await module.review_project_sources({"task_spec": spec()}, [source.to_dict()])
    assert any("末尾独有依据" in call for call in calls)
    calls.clear()
    async def unknown(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("结果未知")
    monkeypatch.setattr(module, "achat", unknown)
    result = await module.review_project_sources({"task_spec": spec()}, [source.to_dict(), item().to_dict()])
    assert len(calls) == 1
    assert result["coverage"]["stop_reason"] == "review_unknown"
    assert result["coverage"]["pending_sources"] == 2


@pytest.mark.asyncio
async def test_project_structured_judgments_disable_unbounded_thinking_without_skipping_check(monkeypatch):
    from src.conductor import project_collection as module
    calls = []

    async def answer(messages, **kwargs):
        calls.append((messages[0]["content"], kwargs))
        if messages[0]["content"] == module._EXTRACT:
            return json.dumps({"projects": [proposal()], "reason": "原文具名项目"})
        return json.dumps({"passed": True, "reason": "原文依据明确"})

    monkeypatch.setattr(module, "achat", answer)
    result = await module.review_project_sources({"task_spec": spec()}, [item().to_dict()])
    assert len(result["projects"]) == 1
    assert len(calls) == 2
    assert calls[0][1].get("thinking") is False
    assert calls[1][1].get("thinking") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("region", "江苏省"), ("owner", "推测业主"),
                                         ("project_number", "无依据编号"), ("current_stage", "推测进展")])
async def test_unbound_optional_text_is_null_before_check_but_final_tampering_still_fails(monkeypatch, field, value):
    from src.conductor import project_collection as module
    source = item().to_dict()
    record = proposal()
    record[field] = value
    if not any(entry["field"] == field for entry in record["evidence"]):
        record["evidence"].append({"field": field, "quote": "甲公司"})
    calls = []

    async def answer(messages, **kwargs):
        if messages[0]["content"] == module._EXTRACT:
            return json.dumps({"projects": [record], "reason": "具名项目"})
        payload = json.loads(messages[-1]["content"])
        assert payload["project"][field] is None
        assert payload["project"]["field_review_notes"]
        calls.append(payload)
        return json.dumps({"passed": True, "reason": "其余字段有原文依据，未证实字段留空"})

    monkeypatch.setattr(module, "achat", answer)
    result = await module.review_project_sources({"task_spec": spec()}, [source])
    assert len(calls) == 1 and len(result["projects"]) == 1
    project = result["projects"][0]
    assert project[field] is None
    assert field in project["field_review_notes"]
    assert all(entry["field"] != field for entry in project["evidence"])
    originals = {1: {**source, "source_id": 1}}
    assert module._references(project, originals, spec().project_collection)
    project[field] = value
    assert module._references(project, originals, spec().project_collection) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid", "unknown", "time_budget"])
async def test_completed_first_fragment_project_is_kept_when_later_fragment_is_invalid(monkeypatch, failure):
    from src.conductor import project_collection as module
    from types import SimpleNamespace
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 60)
    counts = {"extract": 0, "check": 0}
    source = item(extra="甲" * 9000).to_dict()
    original = {**source, "source_id": 1}
    state = {"task_spec": spec()}
    assert module._validate(proposal(), original, state["task_spec"].project_collection)[0]

    async def completed_then_invalid(messages, **kwargs):
        if messages[0]["content"] == module._CHECK:
            counts["check"] += 1
            if failure == "time_budget":
                clock[0] = 61.0
            return json.dumps({"passed": True, "reason": "名称、业主、日期及建设内容均有逐字原文依据"})
        counts["extract"] += 1
        payload = json.loads(messages[-1]["content"])
        if counts["extract"] == 1:
            assert payload["chunks"] == 2
            assert all(entry["quote"] in payload["content"] for entry in proposal()["evidence"])
            return json.dumps({"projects": [proposal()], "reason": "首片完整返回有原文依据的项目"}, ensure_ascii=False)
        if failure == "unknown":
            raise RuntimeError("后片请求结果未知")
        return '{"projects":'

    monkeypatch.setattr(module, "achat", completed_then_invalid)
    result = await module.review_project_sources(state, [source])
    # 后片未核完不代表首片已独立核验的项目应丢失，也不能把整个来源标成核验完成。
    assert len(result["projects"]) == 1
    assert result["projects"][0]["source_ids"] == [1]
    assert module._references(result["projects"][0], {1: original}, state["task_spec"].project_collection)
    assert counts == {"extract": 1 if failure == "time_budget" else 2, "check": 1}
    assert result["coverage"]["stop_reason"] == "review_" + failure
    assert result["coverage"]["reviewed_sources"] == 0
    assert result["coverage"]["pending_sources"] == 1


@pytest.mark.asyncio
async def test_overlapping_fragment_does_not_recheck_identical_completed_proposal(monkeypatch):
    from src.conductor import project_collection as module
    counts = {"extract": 0, "check": 0}
    source = item().to_dict()
    source["content"] = "甲" * 7900 + source["content"] + "乙" * 1000
    async def review(messages, **kwargs):
        if messages[0]["content"] == module._CHECK:
            counts["check"] += 1
            return json.dumps({"passed": True, "reason": "完整原文依据已核对"})
        counts["extract"] += 1
        assert all(entry["quote"] in json.loads(messages[-1]["content"])["content"] for entry in proposal()["evidence"])
        return json.dumps({"projects": [proposal()], "reason": "重叠片段中的相同完整提案"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", review)
    result = await module.review_project_sources({"task_spec": spec()}, [source])
    assert len(result["projects"]) == 1
    assert counts == {"extract": 2, "check": 1}
    assert result["coverage"]["reviewed_sources"] == 1


@pytest.mark.asyncio
async def test_expired_review_budget_does_not_issue_candidate_check(monkeypatch):
    from src.conductor import project_collection as module
    from types import SimpleNamespace
    clock, calls = [0.0], []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module.settings, "collect_timeout_seconds", 60)
    async def review(messages, **kwargs):
        calls.append(messages[0]["content"])
        assert messages[0]["content"] == module._EXTRACT
        clock[0] = 61.0
        return json.dumps({"projects": [proposal()], "reason": "抽取完整返回，但核验预算已耗尽"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", review)
    result = await module.review_project_sources({"task_spec": spec()}, [item().to_dict()])
    assert result["projects"] == []
    assert calls == [module._EXTRACT]
    assert result["coverage"]["stop_reason"] == "review_time_budget"
    assert result["coverage"]["pending_sources"] == 1


@pytest.mark.asyncio
async def test_numeric_pagination_does_not_consume_model_calls_or_hide_real_tail(monkeypatch):
    from src.conductor import project_collection as module
    calls = []
    async def extract(messages, **kwargs):
        calls.append(messages)
        if messages[0]["content"] == module._CHECK:
            return json.dumps({"passed": True, "reason": "完整字段原文已核对"})
        return json.dumps({"projects": [proposal()], "reason": "末尾项目正文"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", extract)
    source = item().to_dict()
    source["content"] = "0123456789\n" * 1700 + source["content"]
    result = await module.review_project_sources({"task_spec": spec()}, [source])
    assert len(result["projects"]) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unknown", "list_excerpt"])
async def test_search_snippet_without_original_body_never_counts_as_verified_project(monkeypatch, status):
    from src.conductor import project_collection as module
    async def forbidden(*args, **kwargs):
        raise AssertionError("不能把未知原件交给模型补造")
    monkeypatch.setattr(module, "achat", forbidden)
    source = item().to_dict()
    source["metadata"]["body_status"] = status
    result = await module.review_project_sources({"task_spec": spec()}, [source])
    assert result["projects"] == []
    assert result["coverage"]["pending_sources"] == 1
    assert result["excluded"][0]["review_status"] == "pending"


@pytest.mark.asyncio
async def test_rejected_candidate_does_not_count_toward_target(monkeypatch):
    from src.conductor import project_collection as module
    calls = []
    async def extract(messages, **kwargs):
        if messages[0]["content"] == module._CHECK:
            calls.append(1)
            return json.dumps({"passed": len(calls) > 1, "reason": "先排除错误项目再继续筛选"})
        return json.dumps({"projects": [proposal()], "reason": "候选项目"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", extract)
    result = await module.review_project_sources({"task_spec": spec()},
        [item().to_dict(), item("https://official.example/second").to_dict()])
    assert len(result["projects"]) == 1
    assert result["projects"][0]["source_ids"] == [2]
    assert result["excluded"][0]["source_id"] == 1


def test_unknown_owner_and_different_regions_do_not_merge():
    from src.conductor.project_collection import _merge
    projects = []
    for region in ("江苏", "广东"):
        _merge(projects, {**proposal(), "owner": None, "project_number": None, "region": region,
            "source_ids": [len(projects) + 1], "missing_fields": ["owner"]})
    assert len(projects) == 2
    assert len({p["project_id"] for p in projects}) == 2


def test_announcement_amount_conflicts_remain_explicit_and_latest_stage_is_evidenced():
    from src.conductor.project_collection import _merge
    projects = []
    _merge(projects, {**proposal(), "source_ids": [1], "missing_fields": []})
    _merge(projects, {**proposal(amount="90"), "publication_date": "2026-10-05", "current_stage": "中标",
        "source_ids": [2], "missing_fields": []})
    assert len(projects) == 1
    assert projects[0]["amount"] is None
    assert projects[0]["field_variants"]["amount"] == ["100", "90"]
    assert projects[0]["current_stage"] == "中标"
    assert len(projects[0]["announcements"]) == 2
    _merge(projects, {**proposal(amount="100"), "publication_date": "2026-10-06", "source_ids": [3], "missing_fields": []})
    assert projects[0]["amount"] is None
    assert "amount" in projects[0]["missing_fields"]


def test_same_procurement_batch_different_projects_are_not_merged():
    from src.conductor.project_collection import _merge
    projects = []
    for name, region in (("南京智能巡检工程", "南京"), ("广州智能巡检工程", "广州")):
        _merge(projects, {**proposal(name=name), "region": region, "source_ids": [len(projects) + 1], "missing_fields": []})
    assert len(projects) == 2


def test_same_source_overlapping_chunk_without_owner_counts_once():
    from src.conductor.project_collection import _merge
    projects = []
    for _ in range(2):
        _merge(projects, {**proposal(), "owner": None, "source_ids": [1], "missing_fields": ["owner"]})
    assert len(projects) == 1


def test_missing_owner_same_name_on_different_pages_does_not_inflate_independent_count():
    from src.conductor.project_collection import _merge
    projects = []
    first = {**proposal(), "owner": None, "project_number": None, "source_ids": [1], "missing_fields": ["owner"]}
    assert _merge(projects, first)
    assert _merge(projects, {**first, "source_ids": [2]}) is False
    assert len(projects) == 1


def test_older_announcement_cannot_resolve_same_day_latest_stage_conflict():
    from src.conductor.project_collection import _merge
    projects = []
    for index, (published, stage) in enumerate((("2026-10-05", "中标"), ("2026-10-05", "取消"), ("2026-10-01", "招标")), 1):
        _merge(projects, {**proposal(), "publication_date": published, "current_stage": stage,
            "source_ids": [index], "missing_fields": []})
    assert projects[0]["publication_date"] == "2026-10-05"
    assert projects[0]["current_stage"] is None


@pytest.mark.asyncio
async def test_project_clean_keeps_evidence_bound_json_bytes():
    from src.conductor.nodes.clean import clean_node
    content = json.dumps({"project_name": "含<标记>工程", "construction_content": "验证码检测系统"}, ensure_ascii=False)
    raw = [{"url": "https://official.example/a", "content": content}]
    result = await clean_node({"task_spec": spec(), "project_result": {"projects": [{}]}, "raw_dataset": raw})
    assert result["cleaned_dataset"] == raw


@pytest.mark.asyncio
async def test_final_checker_rejects_changed_field_or_unread_body_without_model(monkeypatch):
    from src.conductor import project_collection as module
    async def forbidden(*args, **kwargs):
        raise AssertionError("失去确定性证据绑定后不能用模型放行")
    monkeypatch.setattr(module, "achat", forbidden)
    monkeypatch.setattr(module.settings, "checker_enabled", True)
    source = {**item().to_dict(), "source_id": 1}
    candidate, _ = module._validate(proposal(), source, spec().project_collection)
    projects = []
    module._merge(projects, candidate)
    for mode in ("changed_amount", "unread_body"):
        data = json.loads(json.dumps({"projects": projects, "sources": [source], "coverage": {}}, ensure_ascii=False))
        if mode == "changed_amount":
            data["projects"][0]["amount"] = "999"
        else:
            data["sources"][0]["metadata"]["body_status"] = "unknown"
        result = await module.check_project_result({"task_spec": spec(), "project_result": data, "analysis": "项目报告"})
        assert result["quality"]["passed"] is False
        assert result["quality"]["review_complete"] is False


@pytest.mark.asyncio
async def test_project_profile_cannot_turn_exact_url_into_broader_search(monkeypatch):
    from src.conductor import project_collection as module
    def forbidden():
        raise AssertionError("显式URL不能改走关键词发现")
    monkeypatch.setattr(module, "get_registry", forbidden)
    request = spec().model_copy(update={"urls": ["https://official.example/exact"]})
    result = await module.collect_projects({"task_spec": request})
    assert result["project_result"]["coverage"]["stop_reason"] == "explicit_url_profile_unsupported"
    assert result["raw_dataset"] == []


@pytest.mark.parametrize("passed", [True, False])
def test_api_project_failure_cannot_be_persisted_as_successful_output(passed):
    from src.api.routes.chat import _build_result
    state = {"task_spec": spec(), "task_id": "project_api_test", "project_result": {"projects": [{}] if passed else []},
        "quality": {"passed": passed, "review_complete": passed}, "analysis": "项目报告",
        "outputs": {"json": "data.json", "report_md": "report.md", "sources_file": "sources.json"}}
    result = _build_result("owner", "conv", state, "结果", None, None, "原项目任务")
    assert result["kind"] == ("output" if passed else "error")
    assert result["quality"]["passed"] is passed
    assert "sources.json" in {item["name"] for item in result["files"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("analysis", [
    "本轮取得并完成核验的项目不足8个；实际取得1个，缺少7个。不能推断官网只有1个符合条件的项目，也不能证明已穷尽官网项目。未披露字段不推断。",
    "本轮已核验全部取得的候选，实际确认1个，缺7个；不能据此推断官网项目总量。",
    "已核验全部19条已取得来源，确认1个项目，缺7个；本轮覆盖不代表官网全量。",
    "本轮已核验1个，缺7个；这并不意味着官网只有1个项目。",
    "本轮已核验1个，缺7个；这不意味着官网只有1个项目。",
    "并不是官网只有1个项目。",
    "本轮确认1个，缺7个；已取得的官网没有披露项目金额，金额暂不采用。",
    "官网没有提供项目总金额，因此金额留空。",
])
async def test_valid_partial_project_result_can_pass_complete_source_and_report_review(monkeypatch, analysis):
    from src.conductor import project_collection as module
    calls = []
    async def judge(messages, **kwargs):
        calls.append(messages)
        return json.dumps({"passed": True, "reason": "原文字段准确，数量不足已明确披露"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", judge)
    monkeypatch.setattr(module.settings, "checker_enabled", True)
    source = {**item().to_dict(), "source_id": 1}
    candidate, _ = module._validate(proposal(), source, spec().project_collection)
    projects = []
    module._merge(projects, candidate)
    result = await module.check_project_result({"task_spec": spec(), "analysis": analysis,
        "project_result": {"projects": projects, "sources": [source], "coverage": {"shortfall": 7}}})
    assert result["quality"]["passed"] is True
    assert result["quality"]["review_complete"] is True
    assert result["quality"]["source_checks"][0]["source_id"] == 1
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "不能据本次检索判断官网没有相关项目。", "这并不意味着官网只有1个项目。"])
async def test_report_cannot_attribute_sample_shortfall_to_public_channel_even_when_model_passes(monkeypatch, suffix):
    from src.conductor import project_collection as module
    async def judge(*args, **kwargs):
        return json.dumps({"passed": True, "reason": "模型认为报告忠于字段"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", judge)
    monkeypatch.setattr(module.settings, "checker_enabled", True)
    source = {**item().to_dict(), "source_id": 1}
    candidate, _ = module._validate(proposal(), source, spec().project_collection)
    projects = []
    module._merge(projects, candidate)
    result = await module.check_project_result({"task_spec": spec(),
        "analysis": "实际核验1个，缺7个；缺口属公开渠道近90天内可核项目不足，非对相关项目不存在的判断。" + suffix,
        "project_result": {"projects": projects, "sources": [source],
            "coverage": {"shortfall": 7, "pending_sources": 5, "search_stop_reason": "time_budget"}}})
    assert result["quality"]["passed"] is False
    assert any("本轮" in issue for issue in result["quality"]["issues"])


@pytest.mark.asyncio
async def test_negation_before_contrast_does_not_allow_affirmative_global_project_count(monkeypatch):
    from src.conductor import project_collection as module
    async def judge(*args, **kwargs):
        return json.dumps({"passed": True, "reason": "模型错误放行"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", judge)
    monkeypatch.setattr(module.settings, "checker_enabled", True)
    source = {**item().to_dict(), "source_id": 1}
    candidate, _ = module._validate(proposal(), source, spec().project_collection)
    projects = []
    module._merge(projects, candidate)
    result = await module.check_project_result({"task_spec": spec(), "analysis": "不能凑数但官网只有1个项目。",
        "project_result": {"projects": projects, "sources": [source], "coverage": {"shortfall": 7}}})
    assert result["quality"]["passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("published", ["2026-07-13", "2026-10-16"])
async def test_unique_explicit_outside_publication_is_excluded_before_model(monkeypatch, published):
    from src.conductor import project_collection as module
    async def forbidden(*args, **kwargs):
        raise AssertionError("明确唯一发布时间在窗口外，无须模型重新判断日期")
    monkeypatch.setattr(module, "achat", forbidden)
    source = CollectedItem(url="https://official.example/article", title="技术成果报道",
        content=f"## 技术成果报道\n###### **发布时间：{published}**\n建设智能巡检系统，其他业务日期2026-10-01。",
        metadata={"body_status": "provider_raw"})
    result = await module.review_project_sources({"task_spec": spec()}, [source.to_dict()])
    assert result["projects"] == [] and result["coverage"]["reviewed_source_ids"] == [1]
    assert result["coverage"]["review_fragments"] == 0
    assert published in result["excluded"][0]["reason"] and "冻结范围" in result["excluded"][0]["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "发布时间：2026-07-13\n其他资料发布时间：2026-10-01\n建设智能巡检系统。",
    "2026-07-13\n2026-10-01\n建设智能巡检系统。",
    "发布时间：2026-13-40\n建设智能巡检系统。",
    "发布时间：2026-10-01\n建设智能巡检系统。",
])
async def test_ambiguous_unlabelled_or_in_window_date_keeps_business_review(monkeypatch, content):
    from src.conductor import project_collection as module
    calls = []
    async def review(*args, **kwargs):
        calls.append(1)
        return json.dumps({"projects": [], "reason": "原有业务判断：主题证据不足"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", review)
    source = CollectedItem(url="https://official.example/2026-07-13/article", title="日期待核报道",
        content=content, metadata={"body_status": "complete"})
    result = await module.review_project_sources({"task_spec": spec()}, [source.to_dict()])
    assert calls == [1] and result["excluded"][0]["reason"] == "原有业务判断：主题证据不足"


@pytest.mark.asyncio
async def test_historical_stage_keeps_bound_value_and_records_source_as_of_date(monkeypatch):
    from src.conductor import project_collection as module
    source = item()
    source.content = source.content.replace("2026-10-01", "2026-08-19").replace("招标", "试运行")
    record = proposal()
    record.update(publication_date="2026-08-19", current_stage="试运行")
    for entry in record["evidence"]:
        entry["quote"] = entry["quote"].replace("2026-10-01", "2026-08-19").replace("招标", "试运行")
    async def review(messages, **kwargs):
        return json.dumps({"passed": True, "reason": "原文字段有据"} if messages[0]["content"] == module._CHECK
            else {"projects": [record], "reason": "有明确部署事实"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", review)
    request = spec()
    request.project_collection.frozen_at = datetime(2026, 10, 15, tzinfo=timezone.utc)
    result = await module.review_project_sources({"task_spec": request}, [source.to_dict()])
    project = result["projects"][0]
    assert project["current_stage"] == "试运行"
    assert "2026-08-19" in project["stage_note"] and "2026-10-15" in project["stage_note"]
    assert "原文时点" in project["stage_note"] and "未另行确认" in project["stage_note"]
    assert module._references(project, {1: result["sources"][0]}, request.project_collection)


def test_merging_keeps_latest_nonempty_historical_stage_note():
    from src.conductor import project_collection as module
    scope = spec().project_collection.model_copy(update={"frozen_at": datetime(2026, 10, 15, tzinfo=timezone.utc)})
    projects = []
    for source_id, published in ((1, "2026-10-01"), (2, "2026-10-02")):
        source = {**item(f"https://official.example/{source_id}").to_dict(), "source_id": source_id}
        source["content"] = source["content"].replace("2026-10-01", published)
        record = proposal()
        record["publication_date"] = published
        for entry in record["evidence"]:
            entry["quote"] = entry["quote"].replace("2026-10-01", published)
        candidate, reason = module._validate(record, source, scope)
        assert candidate, reason
        module._merge(projects, candidate)
    assert len(projects) == 1 and projects[0]["current_stage"] == "招标"
    assert "2026-10-02" in projects[0]["stage_note"] and "原文时点" in projects[0]["stage_note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("report_passed", [True, False])
async def test_report_checker_receives_only_counts_and_phase_stops_without_changing_stored_coverage(monkeypatch, report_passed):
    from src.conductor import project_collection as module
    coverage = {"target_count": 8, "verified_projects": 1, "target_met": False, "shortfall": 7,
        "candidates": 35, "reviewed_sources": 14, "pending_sources": 21,
        "search_stop_reason": "time_budget", "extraction_stop_reason": "candidates_exhausted",
        "stop_reason": "candidates_exhausted", "review_fragments": 7,
        "discovery": {"attempts": [{"diagnostic": "internal-attempt-log"}]}}
    original = json.dumps(coverage, ensure_ascii=False)
    seen = []
    async def judge(messages, **kwargs):
        if messages[0]["content"] != module._CHECK:
            projected = json.loads(messages[-1]["content"])["coverage"]
            assert projected == {"target_count": 8, "verified_projects": 1, "target_met": False, "shortfall": 7,
                "candidates": 35, "reviewed_sources": 14, "pending_sources": 21,
                "search_stop_reason": "time_budget", "extraction_stop_reason": "candidates_exhausted"}
            assert "internal-attempt-log" not in json.dumps(messages)
            assert "待核" in messages[0]["content"] and "动作数" in messages[0]["content"]
            for rule in ("null只代表未采集到或未确认", "不能据此断言全文未披露", "历史阶段原值",
                         "announcement_count=1", "来源数不等于独立项目数", "owner/region为空",
                         "真实错误原句", "相邻限定"):
                assert rule in messages[0]["content"]
            seen.append(projected)
            return json.dumps({"passed": report_passed, "reason": "报告仅描述本轮覆盖" if report_passed
                else "报告原句‘金额原文未披露’没有全文披露情况的证据"}, ensure_ascii=False)
        return json.dumps({"passed": True, "reason": "报告仅描述本轮覆盖"}, ensure_ascii=False)
    monkeypatch.setattr(module, "achat", judge)
    monkeypatch.setattr(module.settings, "checker_enabled", True)
    source = {**item().to_dict(), "source_id": 1}
    candidate, _ = module._validate(proposal(), source, spec().project_collection)
    projects = []
    module._merge(projects, candidate)
    result = await module.check_project_result({"task_spec": spec(),
        "analysis": "本轮确认1个项目，缺7个；检索预算耗尽，仍有21条来源待核。" + ("" if report_passed else "金额原文未披露。"),
        "project_result": {"projects": projects, "sources": [source], "coverage": coverage}})
    assert result["quality"]["passed"] is report_passed and len(seen) == 1
    if not report_passed:
        assert any("金额原文未披露" in issue for issue in result["quality"]["issues"])
    assert json.dumps(coverage, ensure_ascii=False) == original
