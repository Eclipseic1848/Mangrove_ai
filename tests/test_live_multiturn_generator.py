"""真实多轮评测的固定分母与格式兼容，不启动外部模型。"""
from scripts.evaluate_multiturn_live import parse_reply, scenarios


def test_generated_conversations_have_independent_ground_truth():
    cases = scenarios()
    assert len(cases) == len({case["name"] for case in cases}) == 40
    assert all(len(case["turns"]) == 4 for case in cases)
    assert {case["kind"] for case in cases} == {"correction_undo", "unknown_withdrawal", "quoted_instruction", "topic_return"}
    for case in cases:
        if case["kind"] == "unknown_withdrawal":
            assert case["turns"][2][1]["乙"] is None
        if case["kind"] == "correction_undo":
            assert case["turns"][1][1] == case["turns"][3][1]
    assert parse_reply('```json\n{"甲": 3}\n```') == {"甲": 3}


def test_summary_does_not_hide_unknown_or_extra_prose():
    import pytest
    from scripts.summarize_multiturn_live import summarize
    report = {"cases": [{"name": "test", "turns": [
        {"status": "needs_review", "expected": {"甲": 3}, "reply": '说明\n```json\n{"甲":3}\n```'},
        {"status": "unknown_no_retry", "expected": {"甲": 3}},
        {"status": "needs_review", "expected": {"甲": 3}, "reply": '{"甲":999}'},
    ]}]}
    result = summarize([report])
    assert result["complete_value_matching_cases"] == 0
    assert result["turn_counts"] == {"match": 1, "extra_text_review": 1, "unknown": 1, "needs_review": 1}
    with pytest.raises(ValueError, match="重复"):
        summarize([report, report])


def test_long_history_exceeds_old_boundary_without_oversized_turns():
    from scripts.evaluate_multiturn_live import long_scenarios
    cases = long_scenarios()
    assert len(cases) == 2
    for case in cases:
        assert sum(len(text) for text, _ in case["turns"][:-1]) > 20000
        assert all(len(text) < 7900 for text, _ in case["turns"])
        assert case["turns"][-1][1]["乙"] is None
    assert cases[1]["turns"][-1][1]["甲"] == 23


def test_live_runner_persists_identity_before_send_and_stops_on_unknown(tmp_path, monkeypatch):
    import asyncio
    import json
    import sqlite3
    from types import SimpleNamespace
    import httpx
    from scripts import evaluate_multiturn_live as live

    database, account, output = (tmp_path / name for name in ("test.db", "account.json", "report.json"))
    with sqlite3.connect(database) as connection:
        for name in ("data_prep_tasks", "semantic_workspace_tasks", "agentic_runtime_runs"):
            connection.execute(f"CREATE TABLE {name} (id INTEGER)")
    account.write_text(json.dumps({"username": "test", "password": "synthetic"}), encoding="utf-8")
    sent = []

    def respond(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={})
        if request.url.path.endswith("preferences/default"):
            return httpx.Response(200, json={"preference": {"model_id": "deepseek-flash", "connection_id": "test"}})
        body = json.loads(request.content)
        saved = json.loads(output.read_text(encoding="utf-8"))["cases"][0]["turns"][-1]
        assert saved["request_id"] == body["request_id"]
        assert saved["status"] == "inflight_unknown"
        sent.append(body["request_id"])
        return httpx.Response(502, json={"error_code": "provider_outcome_unknown", "detail": "private-not-for-report"})

    original = httpx.AsyncClient
    monkeypatch.setattr(live.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    assert asyncio.run(live.run(SimpleNamespace(account=account, database=database, output=output,
        base_url="http://test", suite="short", start_case=40, reconnect_each_turn=True))) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(sent) == 1
    assert report["counts"] == {"unknown_no_retry": 1}
    assert "private-not-for-report" not in output.read_text(encoding="utf-8")


def test_summary_uses_planned_long_turns_and_rejects_mixed_denominators():
    import pytest
    from scripts.summarize_multiturn_live import summarize
    from scripts.evaluate_multiturn_live import long_scenarios
    cases = long_scenarios()
    report = {"planned_cases": 2, "cases": [
        {"name": case["name"], "turns": [
            {"status": "passed", "reply": '{"甲":17,"乙":null}', "expected": {"甲":17,"乙":None}}
            for _ in case["turns"]
        ]} for case in cases
    ]}
    result = summarize([report])
    assert result["planned_cases"] == 2
    assert result["complete_value_matching_cases"] == 2
    report["cases"][0]["turns"].pop()
    assert summarize([report])["complete_value_matching_cases"] == 1
    with pytest.raises(ValueError, match="计划"):
        summarize([report, {"planned_cases": 40, "cases": []}])


def test_custom_report_requires_saved_plan_before_claiming_complete():
    from scripts.summarize_multiturn_live import summarize
    case = {"name": "custom-history", "turns": [
        {"status": "passed", "reply": '{"甲":3}', "expected": {"甲":3}}
        for _ in range(5)
    ]}
    report = {"planned_cases": 1, "cases": [case]}
    assert summarize([report])["complete_value_matching_cases"] == 0
    case["planned_turns"] = 5
    assert summarize([report])["complete_value_matching_cases"] == 1


def test_summary_separates_plain_json_from_values_and_wrappers():
    from scripts.summarize_multiturn_live import summarize
    replies = ['{"甲":3}', '```json\n{"甲":3}\n```', '说明\n```json\n{"甲":3}\n```', '{"话题":{"甲":3}}', '{"甲":999}', None, '{"甲":NaN}', '{"甲":Infinity}', '{"甲":-Infinity}']
    report = {"planned_cases": len(replies), "cases": [
        {"name": str(index), "planned_turns": 1, "turns": [
            {"status": "needs_review", "expected": {"甲": 3}, **({"reply": reply} if reply is not None else {})}
        ]} for index, reply in enumerate(replies)
    ]}
    result = summarize([report])
    assert result["reply_format_counts"] == {"plain_json": 3, "wrapped_or_invalid": 5, "unknown": 1}
    assert result["complete_plain_json_matching_cases"] == 1
    assert result["complete_value_matching_cases"] == 3
