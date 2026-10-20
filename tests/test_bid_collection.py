"""最新公告按完整原文核对后计数，不把搜索命中当合格公告。"""
import asyncio
import copy
import json
import socket
from datetime import datetime
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.conductor.nodes import collect as collect_node_module
from src.conductor.task_spec import DataType, TaskSpec


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    violations = []
    connect, create_connection, getaddrinfo = socket.socket.connect, socket.create_connection, socket.getaddrinfo
    def check(host):
        if host not in ("127.0.0.1", "::1", "localhost"):
            violations.append(host)
            raise AssertionError("本文件仅允许合成数据，禁止真实网络请求")
    def connect_local(sock, address):
        check(address[0])
        return connect(sock, address)
    def create_local(address, *args, **kwargs):
        check(address[0])
        return create_connection(address, *args, **kwargs)
    def resolve_local(host, *args, **kwargs):
        check(host)
        return getaddrinfo(host, *args, **kwargs)
    monkeypatch.setattr(socket, "create_connection", create_local)
    monkeypatch.setattr(socket, "getaddrinfo", resolve_local)
    monkeypatch.setattr(socket.socket, "connect", connect_local)
    yield
    assert not violations, "合成验证曾尝试真实来源请求，已拦截"


def source(index, published, *, deadline="2026-10-01"):
    content = (f"医疗设备公开招标公告，采购人为第{index}合成医院，现采购医疗设备并邀请符合条件的投标人参加。\n"
               f"发布日期：{published}\n投标截止时间：{deadline}\n正文末端{index}")
    return CollectedItem(url=f"https://hospital.example/notice/{index}", title=f"第{index}医疗设备招标公告",
                         content=content, metadata={"date": published, "body_status": "complete"})


def assessment(index, item, **changes):
    value = {"source_id": index, "notice": True, "relevant": True,
        "publication_date": item.metadata["date"], "publication_quote": f"发布日期：{item.metadata['date']}",
        "notice_quote": item.content.splitlines()[0], "relevance_quote": item.content.splitlines()[0],
        "deadline_date": "2026-10-01", "deadline_quote": "投标截止时间：2026-10-01", "reason": "合成正式公告且主题符合"}
    return {**value, **changes}


def state(**changes):
    spec = TaskSpec(intent="采集最新发布的3条医疗设备招标公告", data_type="bid",
                    keywords=["医疗设备 招标公告"], max_items=3)
    return {"task_id": "synthetic-bid", "user_input": spec.intent, "task_spec": spec,
            "provider": "bound", "model": "deepseek-flash", "collector_candidates": ["search"], **changes}


@pytest.mark.asyncio
async def test_latest_bid_entry_uses_bounded_candidates_before_counting(monkeypatch, tmp_path):
    import src.collectors
    import src.llm
    from src.config.settings import settings

    inputs = []
    items = [source(1, "2026-10-15"), source(2, "2026-10-17"), source(3, "2026-10-16"), source(4, "2015-01-01")]
    original = state()
    before = original["task_spec"].model_dump(mode="json")

    async def search(spec):
        inputs.append(spec.model_dump(mode="json"))
        return CollectResult(True, "search", items=items)

    async def judge(messages, **kwargs):
        return json.dumps({"source_checks": [assessment(i, item, **({"notice": False} if i == 4 else {}))
                                            for i, item in enumerate(items, 1)]}, ensure_ascii=False)

    registry = {"search": SimpleNamespace(collect=search, collect_bid_candidates=search)}
    monkeypatch.setattr(src.collectors, "get_registry", lambda: registry)
    monkeypatch.setattr(collect_node_module, "get_registry", lambda: registry)
    monkeypatch.setattr(src.llm, "achat", judge)
    module = import_module("src.conductor.bid_collection")
    monkeypatch.setattr(module, "get_registry", lambda: registry)
    monkeypatch.setattr(module, "achat", judge)
    monkeypatch.setattr(module, "analysis_context_capacity", lambda _state: 1000000)
    monkeypatch.setattr(module, "beijing_now", lambda: datetime.fromisoformat("2026-10-17T12:00:00+08:00"))
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(settings, "collector_max_items", 30)
    result = await collect_node_module.collect_node(original)
    assert inputs[0]["max_items"] == 30, "目标3条不能缩减平台已有的候选检索预算"
    assert inputs[0]["time_range"] == "最新"
    assert original["task_spec"].model_dump(mode="json") == before
    assert [item["url"] for item in result["raw_dataset"]] == [items[i].url for i in (1, 2, 0)]


@pytest.fixture
def setup_bid(monkeypatch, tmp_path):
    module = import_module("src.conductor.bid_collection")
    items = [source(1, "2026-10-15"), source(2, "2026-10-17"), source(3, "2026-10-16"), source(4, "2015-01-01")]
    search = AsyncMock(return_value=CollectResult(True, "search", items=items))
    checks = [assessment(i, item) for i, item in enumerate(items, 1)]
    calls = []

    async def judge(messages, **kwargs):
        calls.append((messages, kwargs))
        return json.dumps({"source_checks": checks}, ensure_ascii=False)

    monkeypatch.setattr(module, "get_registry", lambda: {"search": SimpleNamespace(collect=search, collect_bid_candidates=search)})
    monkeypatch.setattr(module, "achat", judge)
    monkeypatch.setattr(module, "beijing_now", lambda: datetime.fromisoformat("2026-10-17T12:00:00+08:00"))
    monkeypatch.setattr(module, "analysis_context_capacity", lambda _state: 1000000)
    monkeypatch.setattr(module.settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(module.settings, "collector_max_items", 30)
    return module, items, checks, search, calls, tmp_path


@pytest.mark.asyncio
async def test_full_body_review_sorts_and_marks_expired_without_losing_originals(setup_bid):
    module, items, checks, search, calls, root = setup_bid
    checks[3]["notice"] = False
    before = [copy.deepcopy(item.to_dict()) for item in items]
    result = await module.collect_latest_bid_notices(state())
    assert len(calls) == 1 and calls[0][1] == {"provider": "bound", "model": "deepseek-flash"}
    assert all(f"正文末端{i}" in calls[0][0][-1]["content"] for i in range(1, 5))
    assert "error" not in result
    assert [row["metadata"]["bid_notice"]["publication_date"] for row in result["raw_dataset"]] == ["2026-10-17", "2026-10-16", "2026-10-15"]
    assert all(row["metadata"]["bid_notice"]["deadline_status"] == "已过截止日期" for row in result["raw_dataset"])
    artifact = result["collector_attempts"][0]["candidates_artifact"]
    saved = (root / artifact["storage_path"]).read_bytes()
    import hashlib
    assert hashlib.sha256(saved).hexdigest() == artifact["sha256"]
    assert json.loads(saved)["sources"] == before
    assert [item.to_dict() for item in items] == before
    assert len(result["collector_attempts"][0]["source_checks"]) == 4


@pytest.mark.asyncio
async def test_model_receives_full_evidence_without_discovery_navigation(setup_bid):
    module, items, _, _, calls, root = setup_bid
    items[0].metadata["project_links"] = [{"url": "https://hospital.example/navigation", "text": "导航" * 1000}]
    result = await module.collect_latest_bid_notices(state())
    reviewed = json.loads(calls[0][0][-1]["content"])["sources"]
    assert reviewed == [{"source_id": index, "title": item.title, "url": item.url, "content": item.content}
                        for index, item in enumerate(items, 1)]
    artifact = result["collector_attempts"][0]["candidates_artifact"]
    original = json.loads((root / artifact["storage_path"]).read_text(encoding="utf-8"))
    assert original["sources"][0]["metadata"] == items[0].metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["quote", "deadline_as_publication", "unknown_date", "not_notice", "unrelated", "future_date", "compact_iso", "unknown_deadline", "today_no_time", "utc_deadline", "registration_deadline", "summary_only"])
async def test_unverified_notice_cannot_fill_target(setup_bid, damage):
    module, items, checks, _, _, _ = setup_bid
    checks.pop()
    items.pop()
    row = checks[0]
    if damage == "quote":
        row["publication_quote"] = "发布日期：2026-10-14"
    elif damage == "deadline_as_publication":
        row.update(publication_date="2026-10-01", publication_quote="投标截止时间：2026-10-01")
    elif damage == "unknown_date":
        row.update(publication_date=None, publication_quote=None)
    elif damage == "future_date":
        row.update(publication_date="2099-01-01", publication_quote="发布日期：2099-01-01")
        items[0].content += "\n发布日期：2099-01-01"
    elif damage == "compact_iso":
        row["publication_date"] = "20261015"
    elif damage == "unknown_deadline":
        row.update(deadline_date=None, deadline_quote=None)
    elif damage == "today_no_time":
        row.update(deadline_date="2026-10-17", deadline_quote="投标截止时间：2026-10-17")
        items[0].content += "\n投标截止时间：2026-10-17"
    elif damage == "utc_deadline":
        row.update(deadline_date="2026-10-17", deadline_quote="投标截止时间：2026-10-17 09:00 UTC")
        items[0].content += "\n" + row["deadline_quote"]
    elif damage == "registration_deadline":
        row.update(deadline_date="2026-10-01", deadline_quote="报名截止时间：2026-10-01")
        items[0].content += "\n报名截止时间：2026-10-01\n投标截止时间：2026-10-30"
    elif damage == "summary_only":
        items[0].metadata["body_status"] = "list_excerpt"
    else:
        row["notice" if damage == "not_notice" else "relevant"] = False
    result = await module.collect_latest_bid_notices(state())
    assert not result["raw_dataset"] and result["collector_attempts"][0]["count"] == 2
    assert "2/3" in result["error"] and any("2/3" in note for note in result["collector_notes"])


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong_id", "bool_id", "wrong_flag"])
async def test_incomplete_or_malformed_source_coverage_fails_closed(setup_bid, damage):
    module, _, checks, _, calls, _ = setup_bid
    if damage == "missing":
        checks.pop()
    elif damage == "duplicate":
        checks[1]["source_id"] = 1
    elif damage == "wrong_id":
        checks[1]["source_id"] = 99
    elif damage == "bool_id":
        checks[0]["source_id"] = True
    else:
        checks[0]["notice"] = "true"
    result = await module.collect_latest_bid_notices(state())
    assert not result["raw_dataset"] and result["error"]
    assert len(calls) == 1 and result["collector_attempts"][0]["candidates_artifact"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["search", "unknown", "truncated", "denied", "cancelled"])
async def test_failure_never_retries_or_swallows_cancellation(setup_bid, monkeypatch, failure):
    from src.account_execution import ExecutionDenied
    from src.model_connections.text_protocol import ModelOutputTruncatedError
    module, _, _, search, _, _ = setup_bid
    error = {"search": RuntimeError, "unknown": RuntimeError, "truncated": ModelOutputTruncatedError,
             "denied": ExecutionDenied, "cancelled": asyncio.CancelledError}[failure]
    if failure == "search":
        search.side_effect = error("合成故障")
        judge = AsyncMock()
    else:
        judge = AsyncMock(side_effect=error("合成故障"))
    monkeypatch.setattr(module, "achat", judge)
    if failure in ("truncated", "denied", "cancelled"):
        with pytest.raises(error):
            await module.collect_latest_bid_notices(state())
    else:
        result = await module.collect_latest_bid_notices(state())
        assert not result["raw_dataset"] and result["error"]
    assert search.await_count == 1 and judge.await_count == (failure != "search")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["ordinary", "url", "not_latest", "negated_latest", "mediacrawler", "ecommerce"])
async def test_other_collection_paths_are_unchanged(setup_bid, monkeypatch, change):
    _, items, _, search, calls, _ = setup_bid
    value = state()
    if change == "ordinary":
        value["task_spec"].data_type = DataType.ARTICLE
    elif change == "url":
        value["task_spec"].urls = [items[0].url]
    elif change == "not_latest":
        value["user_input"] = value["task_spec"].intent = "采集医疗设备招标公告"
    elif change == "negated_latest":
        value["user_input"] = "不要求最新，汇总2025年3条医疗设备招标公告"
        value["task_spec"].intent = "汇总2025年3条医疗设备招标公告"
    else:
        value["collector_candidates"] = [change, "search"]
    monkeypatch.setattr(collect_node_module, "get_registry", lambda: {
        name: SimpleNamespace(collect=search) for name in value["collector_candidates"]})
    result = await collect_node_module.collect_node(value)
    assert len(result["raw_dataset"]) == 3 and not calls
    assert search.call_args.args[0] is value["task_spec"]
    if change in {"mediacrawler", "ecommerce"}:
        assert result["collector_used"] == change


@pytest.mark.asyncio
async def test_exact_duplicate_body_cannot_fill_target_even_with_tracking_url(setup_bid):
    module, items, checks, _, _, _ = setup_bid
    items[1] = copy.deepcopy(items[0])
    items[1].url += "?utm_source=another#body"
    checks[1] = assessment(2, items[1])
    checks[3]["notice"] = False
    result = await module.collect_latest_bid_notices(state())
    assert not result["raw_dataset"] and result["collector_attempts"][0]["count"] == 2 and "2/3" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("time,expired", [("09:00", True), ("23:59", False), ("12:00:30", False), ("09点00分", True)])
async def test_today_deadline_needs_exact_time_before_assigning_status(setup_bid, time, expired):
    module, items, checks, _, _, _ = setup_bid
    checks[0].update(deadline_date="2026-10-17", deadline_quote=f"投标截止时间：2026-10-17 {time}")
    items[0].content += "\n" + checks[0]["deadline_quote"]
    result = await module.collect_latest_bid_notices(state())
    notice = next(row for row in result["raw_dataset"] if row["url"] == items[0].url)["metadata"]["bid_notice"]
    assert notice["deadline_expired"] is expired


@pytest.mark.asyncio
async def test_explicit_before_submission_phrase_is_a_deadline(setup_bid):
    module, items, checks, _, _, _ = setup_bid
    quote = "并于 2026年10月20日 09时30分 （北京时间）前递交投标文件。"
    items[0].content += "\n" + quote
    checks[0].update(deadline_date="2026-10-20", deadline_quote=quote)
    result = await module.collect_latest_bid_notices(state())
    row = next(row for row in result["raw_dataset"] if row["url"] == items[0].url)
    assert row["metadata"]["bid_notice"]["deadline_expired"] is False


@pytest.mark.asyncio
async def test_wuhan_short_deadline_binds_unique_same_source_submission_quote(setup_bid):
    module, items, checks, _, _, _ = setup_bid
    short = "2、截止时间：2026年11月04日09点00分（北京时间）"
    binding = "并于2026年11月04日09点00分（北京时间）前递交投标文件"
    items[0].content += ("\n" + binding + "。\n四、提交投标文件截止时间、开标时间和地点\n"
                         "1、开始时间：2026年10月15日00点00分（北京时间）\n" + short)
    checks[0].update(deadline_date="2026-11-04", deadline_quote=short)
    before = copy.deepcopy(items[0].to_dict())
    result = await module.collect_latest_bid_notices(state())
    row = next(row for row in result["raw_dataset"] if row["url"] == items[0].url)
    assert row["metadata"]["bid_notice"]["deadline_quote"] == binding
    assert row["metadata"]["bid_notice"]["deadline_expired"] is False
    audit = result["collector_attempts"][0]["source_checks"][0]
    assert audit["deadline_quote"] == short and audit["deadline_binding_quote"] == binding
    assert items[0].to_dict() == before


@pytest.mark.asyncio
async def test_wuhan_publication_role_binds_without_borrowing_opening_date(setup_bid):
    module, items, checks, _, _, _ = setup_bid
    quote = ("发布日期：2026-10-14 14:22｜发布单位：中科器湖北有限公司｜"
             "项目开标时间：2026-11-04｜项目监管地：武汉市本级|阅读次数：")
    items[0].content = items[0].content.replace("发布日期：2026-10-15", quote)
    checks[0].update(publication_date="2026-10-14", publication_quote=quote)
    before = copy.deepcopy(items[0].to_dict())
    result = await module.collect_latest_bid_notices(state())
    row = next(row for row in result["raw_dataset"] if row["url"] == items[0].url)
    assert row["metadata"]["bid_notice"]["publication_date"] == "2026-10-14"
    assert row["metadata"]["bid_notice"]["publication_quote"] == "发布日期：2026-10-14"
    audit = result["collector_attempts"][0]["source_checks"][0]
    assert audit["publication_quote"] == quote
    assert audit["publication_binding_quote"] == "发布日期：2026-10-14"
    assert items[0].to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["two_publications", "different_publications", "wrong_date", "forged_quote", "deadline_only"])
async def test_publication_role_cannot_bind_forged_or_ambiguous_dates(setup_bid, damage):
    module, items, checks, _, _, _ = setup_bid
    items.pop()
    checks.pop()
    quote = "发布日期：2026-10-14｜开标时间：2026-11-04"
    if damage in {"two_publications", "different_publications"}:
        quote += "｜发布时间：" + ("2026-10-15" if damage == "different_publications" else "2026-10-14")
    if damage == "deadline_only":
        quote = "投标截止时间：2026-10-14｜开标时间：2026-11-04"
    items[0].content = items[0].content.replace("发布日期：2026-10-15", quote)
    checks[0].update(publication_date="2026-10-13" if damage == "wrong_date" else "2026-10-14",
                     publication_quote="伪造的" + quote if damage == "forged_quote" else quote)
    result = await module.collect_latest_bid_notices(state())
    assert not result["raw_dataset"] and result["collector_attempts"][0]["count"] == 2
    audit = result["collector_attempts"][0]["source_checks"][0]
    assert audit["reason_code"] == "publication_unbound" and "publication_binding_quote" not in audit


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["forged_quote", "registration", "wrong_date", "multiple_bindings", "different_time"])
async def test_short_deadline_cannot_bind_forged_or_ambiguous_evidence(setup_bid, damage):
    module, items, checks, _, _, _ = setup_bid
    items.pop()
    checks.pop()
    short = "2、截止时间：2026年11月04日09点00分（北京时间）"
    binding = "并于2026年11月04日09点00分（北京时间）前递交投标文件"
    if damage == "registration":
        short = "报名截止时间：2026年11月04日09点00分（北京时间）"
    if damage == "different_time":
        binding = binding.replace("09点00分", "10点00分")
    items[0].content += "\n" + binding + "。\n" + short
    if damage == "multiple_bindings":
        items[0].content += "\n并于2026年11月05日09点00分（北京时间）前递交投标文件。"
    checks[0].update(deadline_date="2026-11-05" if damage == "wrong_date" else "2026-11-04",
                     deadline_quote="伪造的" + short if damage == "forged_quote" else short)
    result = await module.collect_latest_bid_notices(state())
    assert not result["raw_dataset"] and result["collector_attempts"][0]["count"] == 2
    audit = result["collector_attempts"][0]["source_checks"][0]
    assert audit["reason_code"] == "deadline_unbound" and "deadline_binding_quote" not in audit


@pytest.mark.asyncio
async def test_unlabelled_date_requires_unique_adjacent_announcement_title(setup_bid):
    module, items, checks, _, _, _ = setup_bid
    quote = items[0].title + "\n2026年10月15日"
    items[0].content = quote + "\n" + items[0].content
    checks[0]["publication_quote"] = quote
    result = await module.collect_latest_bid_notices(state())
    assert len(result["raw_dataset"]) == 3 and "error" not in result


@pytest.mark.asyncio
async def test_title_business_words_do_not_turn_publication_into_birth_date(setup_bid):
    module, items, checks, _, _, _ = setup_bid
    items[0].title = "出生缺陷筛查设备采购招标公告"
    quote = items[0].title + "\n2026年10月15日"
    items[0].content = quote + "\n" + items[0].content
    checks[0]["publication_quote"] = quote
    result = await module.collect_latest_bid_notices(state())
    assert len(result["raw_dataset"]) == 3 and "error" not in result


@pytest.mark.asyncio
async def test_existing_window_and_candidate_limit_are_preserved(setup_bid, monkeypatch):
    module, _, _, search, _, _ = setup_bid
    value = state()
    value["task_spec"].time_range = "近90天"
    monkeypatch.setattr(module.settings, "collector_max_items", 7)
    before = value["task_spec"].model_dump(mode="json")
    await module.collect_latest_bid_notices(value)
    assert search.call_args.args[0].time_range == "近90天" and search.call_args.args[0].max_items == 7
    assert value["task_spec"].model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_unknown_context_capacity_does_not_silently_clip_sources(setup_bid, monkeypatch):
    module, _, _, _, calls, _ = setup_bid
    monkeypatch.setattr(module, "analysis_context_capacity", lambda _state: 0)
    monkeypatch.setattr(module.settings, "analyze_max_blob_chars", 100)
    result = await module.collect_latest_bid_notices(state())
    assert result["error"] and not calls and result["collector_attempts"][0]["candidates_artifact"]


@pytest.mark.asyncio
async def test_shortfall_ends_graph_before_analysis_output_or_notification(setup_bid, monkeypatch):
    from src.conductor import graph
    module, _, checks, _, _, _ = setup_bid
    checks[0]["notice"] = checks[3]["notice"] = False
    value = state()
    async def intent(_state):
        return {}
    async def planner(_state):
        return {"task_spec": value["task_spec"]}
    async def router(_state):
        return {"collector_candidates": ["search"]}
    async def unexpected(_state):
        pytest.fail("数量不足不得进入分析、质检或成功产出")
    monkeypatch.setattr(graph, "intent_node", intent)
    monkeypatch.setattr(graph, "planner_node", planner)
    monkeypatch.setattr(graph, "target_resolve_node", intent)
    monkeypatch.setattr(graph, "router_node", router)
    for name in ("clean_node", "analyze_node", "checker_node", "output_node"):
        monkeypatch.setattr(graph, name, unexpected)
    result = await graph.build_graph().ainvoke(value)
    assert result["error"] and not result["raw_dataset"] and not result.get("outputs")
    assert graph._route_after_collect(result) == "end"
    assert result["collector_attempts"][0]["count"] == 2


@pytest.mark.asyncio
async def test_strict_bid_search_never_retries_unknown_post(monkeypatch):
    import httpx
    from src.collectors import search_collector as module
    collector = module.SearchDiscoveryCollector()
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("合成未知响应", request=request)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://search.example")
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    retry = AsyncMock(side_effect=AssertionError("严格候选请求不能自动重试"))
    monkeypatch.setattr(module, "retry_async", retry)
    result = await collector.collect_bid_candidates(state()["task_spec"])
    assert not result.items and len(calls) == 1 and calls[0].method == "POST"
    retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_strict_bid_search_reads_original_even_for_long_summary(monkeypatch):
    import httpx
    from src.collectors import search_collector as module
    collector = module.SearchDiscoveryCollector()
    calls = []
    def respond(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"results": [{"url": "https://hospital.example/notice/1",
                "title": "合成设备招标公告", "content": "这只是很长的搜索摘要。" * 80}]})
        return httpx.Response(200, text="<article><h1>合成设备招标公告</h1><p>" + "原始公告完整正文。" * 80 + "真实原文末端</p></article>")
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://search.example")
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    spec = state()["task_spec"].model_copy(update={"max_items": 1})
    result = await collector.collect_bid_candidates(spec)
    assert [request.method for request in calls] == ["POST", "GET"]
    assert result.items[0].metadata["body_status"] == "complete"
    assert "真实原文末端" in result.items[0].content and "这只是很长的搜索摘要" not in result.items[0].content


@pytest.mark.asyncio
async def test_unscoped_browser_budget_records_unknown_before_later_backend(monkeypatch):
    import sys
    from src.collectors import search_collector as module
    collector = module.SearchDiscoveryCollector()
    calls, failures = [], {}
    url = "https://hospital.example/notice/unknown"
    class FakeCrawler:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            raise TimeoutError("合成浏览器退出异常")
        async def arun(self, *, url):
            calls.append(url)
            await asyncio.Event().wait()
    within = collector._within_budget
    async def bound(operation, deadline):
        if getattr(getattr(operation, "cr_code", None), "co_name", "") == "browse":
            async with asyncio.timeout(0.02):
                return await operation
        return await within(operation, deadline)
    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=FakeCrawler))
    monkeypatch.setattr(module, "crawler_browser_config", lambda: None)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(collector, "_within_budget", bound)
    result = await collector._fetch_all([url], module.time.monotonic() + 60, strict=True,
                                        project_domain="", body_failures=failures)
    assert calls == [url] and failures == {url: "SOURCE_TIMEOUT"}
    assert result[0].metadata["body_status"] == "unknown"
    later = await collector._read_project_pages([CollectedItem(url=url)], "", module.time.monotonic() + 60, failures)
    assert calls == [url] and later[0].metadata["body_status"] == "unknown"


def test_real_government_source_byline_is_publication_evidence():
    from datetime import date
    from src.conductor.bid_collection import _quoted_date
    title = "泸州市人民医院2026年医疗设备第二批招标公告"
    byline = "2026年09月22日 17:11 来源：中国政府采购网四川分网"
    record = {"title": title, "content": "## " + title + "\n" + byline + " 【打印】\n##### 公告概要：正式公告正文"}
    assert _quoted_date("2026-09-22", byline, record, publication=True) == date(2026, 9, 22)
    record["content"] += "\n" + byline
    assert _quoted_date("2026-09-22", byline, record, publication=True) is None


@pytest.mark.asyncio
async def test_latest_bid_discovery_prefers_current_year_and_preserves_original_query(monkeypatch):
    from src.collectors import search_collector as module
    collector = module.SearchDiscoveryCollector()
    candidates = AsyncMock(return_value=CollectResult(False, "search"))
    monkeypatch.setattr(collector, "_collect_candidates", candidates)
    monkeypatch.setattr(module, "beijing_now", lambda: datetime.fromisoformat("2026-10-17T12:00:00+08:00"), raising=False)
    spec = state()["task_spec"]
    before = spec.model_dump(mode="json")
    await collector.collect_bid_candidates(spec)
    options = candidates.call_args.kwargs
    assert options["keywords"] == ["提交投标文件截止时间 医疗设备 招标公告 2026", "医疗设备 招标公告"]
    assert options["limit"] == 3 and spec.model_dump(mode="json") == before and spec.project_collection is None


@pytest.mark.asyncio
async def test_bid_nonindex_directory_reads_one_level_same_host_within_candidate_limit(monkeypatch):
    import httpx
    from src.collectors import search_collector as module
    collector = module.SearchDiscoveryCollector()
    catalog = "https://hospital.example/tradingSearch/searhType-1-key-med.html"
    requested, queries = [], []
    def respond(request):
        if request.method == "POST":
            queries.append(json.loads(request.content)["query"])
            return httpx.Response(200, json={"results": [{"url": catalog, "title": "医疗设备招标列表", "content": "目录检索摘要"}]})
        requested.append(str(request.url))
        if str(request.url) == catalog:
            html = ('<article><h1>医疗设备招标列表</h1><p>这里是招标公告目录。</p>'
                    '<p><a href="/detail/old.html">医疗设备采购公告</a> 2024-01-01</p>'
                    '<p><a href="/detail/new.html">医疗设备采购公告</a> 2026-10-16</p>'
                    '<p><a href="https://outside.example/notice">医疗设备采购公告</a> 2026-10-17</p></article>')
        else:
            html = '<article><h1>医疗设备正式采购公告</h1><p>' + '正式公告完整原文。' * 60 + '</p><a href="/third-level">医疗设备公告</a></article>'
        return httpx.Response(200, text=html)
    monkeypatch.setattr(collector, "_use_anysearch", lambda: False)
    monkeypatch.setattr(collector, "_use_tavily", lambda: True)
    monkeypatch.setattr(collector, "_discovery_backends", lambda: [])
    monkeypatch.setattr(collector, "_discover_one", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.settings, "tavily_base_url", "https://search.example")
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(module, "beijing_now", lambda: datetime.fromisoformat("2026-10-17T12:00:00+08:00"), raising=False)
    spec = state()["task_spec"].model_copy(update={"max_items": 4, "time_range": "最新"})
    result = await collector.collect_bid_candidates(spec)
    assert any(request.endswith("/detail/new.html") for request in requested), "非index目录也须保留原HTML详情链接"
    assert requested.index("https://hospital.example/detail/new.html") < requested.index("https://hospital.example/detail/old.html")
    assert all("outside.example" not in request and "third-level" not in request for request in requested)
    assert len(result.items) <= 4 and any(item.url == catalog for item in result.items), "目录原文不能为给详情让位而丢失"
    assert queries[0].startswith("提交投标文件截止时间 医疗设备 招标公告 2026") and any(query == "医疗设备 招标公告" for query in queries)


@pytest.mark.asyncio
async def test_bid_complete_detail_with_navigation_is_not_downgraded_to_directory(monkeypatch):
    from src.collectors import search_collector as module
    collector = module.SearchDiscoveryCollector()
    directory = CollectedItem(url="https://hospital.example/index.html", content="医疗设备公告目录",
        metadata={"body_status": "complete", "project_links": [{"text": "医疗设备正式公告",
            "url": "https://hospital.example/notice/", "context": "2026-10-17 医疗设备正式公告"}]})
    detail = source(1, "2026-10-17")
    detail.url = "https://hospital.example/notice/"
    detail.metadata["project_directory_hint"] = True
    monkeypatch.setattr(collector, "_read_project_pages", AsyncMock(return_value=[detail]))
    result = await collector._expand_project_directories([directory], ["医疗设备"], ["hospital.example"],
        2, set(), module.time.monotonic() + 10, {}, allow_page_links=True)
    assert result[1].metadata["body_status"] == "complete", "存在导航不能否认已经取得完整正式公告正文"
