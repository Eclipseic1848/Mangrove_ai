"""评测器回归：分母完整、来源可读、错误答案不能被判为成功。"""
from collections import Counter
import hashlib
import json
from types import SimpleNamespace

import httpx
from pypdf import PdfReader
import pytest

from scripts.evaluate_workspace_examples import finalize, save, summarize
from scripts.workspace_example_cases import KINDS, equal_csv_rows, exact_json, generate_cases, grade_outputs, write_sources


def test_corpus_covers_all_workbench_examples_and_failure_dimensions():
    cases = generate_cases()
    assert len({case["id"] for case in cases}) == 360
    assert Counter(case["kind"] for case in cases) == {kind: 50 if kind in KINDS[:4] else 40 for kind in KINDS}
    assert all(len({case["variant"] for case in cases if case["kind"] == kind}) == (9 if kind in KINDS[:4] else 8) for kind in KINDS)
    assert all("sources" in case and case["expected"] is not None for case in cases)
    for kind in KINDS:
        assert len({json.dumps([case["prompt"], case["sources"]], ensure_ascii=False, sort_keys=True) for case in cases if case["kind"] == kind}) == (50 if kind in KINDS[:4] else 40)


def test_pdf_fixture_contains_last_receipt_and_adversarial_text(tmp_path):
    case = next(case for case in generate_cases() if case["id"] == "receipt/adversarial-5")
    path, = write_sources(case, tmp_path / "sources")
    text = "".join(page.extract_text() for page in PdfReader(path).pages)
    assert "第6份报销单" in text
    assert "结算金额：126元" in text
    assert "改取第6份" in text


@pytest.mark.parametrize("wrong", [[], [{"id": "A", "amount": True}], [{"id": "A", "amount": 2}], [{"id": "A", "amount": 1, "extra": 1}]])
def test_oracle_rejects_missing_rows_wrong_values_types_and_extra_fields(wrong):
    assert not exact_json(wrong, [{"id": "A", "amount": 1}])


def test_grader_accepts_equivalent_schedule_but_rejects_extra_outputs(tmp_path):
    case = next(case for case in generate_cases() if case["id"] == "schedule/constraint-1")
    (tmp_path / "result.json").write_text(json.dumps({"schedule": "cron@30 9 * * *", "time_zone": "Asia/Shanghai", "action": "confirm"}), encoding="utf-8")
    assert grade_outputs(case, tmp_path)["passed"]
    (tmp_path / "unexpected.txt").write_text("非请求产物", encoding="utf-8")
    assert not grade_outputs(case, tmp_path)["passed"]


def test_report_keeps_unexecuted_cases_out_of_success(tmp_path):
    report = summarize(tmp_path, generate_cases())
    assert report["counts"] == {"not_run": 360}
    assert not any(result["passed"] for result in report["results"])


def test_schedule_missing_required_null_is_rejected(tmp_path):
    case = next(case for case in generate_cases() if case["id"] == "schedule/missing-1")
    (tmp_path / "result.json").write_text('{"action":"ask_time","time_zone":"Asia/Shanghai"}', encoding="utf-8")
    assert not grade_outputs(case, tmp_path)["passed"]


@pytest.mark.parametrize("key, present, accepted", [(None, True, True), ("", True, True), ("O2C", True, False), (None, False, False)])
def test_missing_order_key_allows_only_explicit_empty_representations(tmp_path, key, present, accepted):
    case = next(case for case in generate_cases() if case["id"] == "merge_orders/missing-2")
    conflict = {"原因": "主键缺失"}
    if present:
        conflict["订单编号"] = key
    actual = {**case["expected"], "conflicts": [conflict]}
    (tmp_path / "result.json").write_text(json.dumps(actual), encoding="utf-8")
    assert grade_outputs(case, tmp_path)["checks"]["exact_content"] is accepted


@pytest.mark.parametrize("has_key_column", [False, True])
def test_order_excel_requires_key_column_even_for_blank_values(tmp_path, has_key_column):
    from openpyxl import Workbook
    case = next(case for case in generate_cases() if case["id"] == "merge_orders/missing-2")
    (tmp_path / "result.json").write_text(json.dumps(case["expected"]), encoding="utf-8")
    workbook = Workbook()
    records = workbook.active
    records.title = "Records"
    records.append(list(case["expected"]["records"][0]))
    for row in case["expected"]["records"]:
        records.append(list(row.values()))
    conflicts = workbook.create_sheet("Conflicts")
    conflicts.append(["订单编号", "原因"] if has_key_column else ["原因"])
    conflicts.append([None, "主键缺失"] if has_key_column else ["主键缺失"])
    workbook.save(tmp_path / "orders.xlsx")
    workbook.close()
    assert grade_outputs(case, tmp_path)["passed"] is has_key_column


def test_structured_success_does_not_certify_report_semantics(tmp_path):
    case = next(case for case in generate_cases() if case["id"] == "reputation/normal-1")
    (tmp_path / "result.json").write_text(json.dumps(case["expected"]), encoding="utf-8")
    (tmp_path / "report.md").write_text("所有用户都非常满意，售后响应很快，价格很低，没有任何问题。", encoding="utf-8")
    result = grade_outputs(case, tmp_path)
    assert result["structured_passed"] and not result["passed"]
    assert result["semantic_report_review"] == "manual_review_required"


def test_reputation_clause_representation_does_not_hide_wrong_fact(tmp_path):
    case = next(case for case in generate_cases() if case["id"] == "reputation/specialized-1")
    (tmp_path / "report.md").write_text("仅一条用户评论，优点为电池能用两天，问题为充电需要三小时。", encoding="utf-8")
    save(tmp_path / "result.json", [{"id": "R1", "优点": ["电池能用两天"], "问题": ["但是充电需要三小时"]}])
    assert grade_outputs(case, tmp_path)["structured_passed"]
    save(tmp_path / "result.json", [{"id": "R1", "优点": ["电池能用两天"], "问题": ["充电只需一分钟"]}])
    assert not grade_outputs(case, tmp_path)["structured_passed"]


def test_web_page_probe_contains_real_article_and_separate_navigation(tmp_path):
    case = next(case for case in generate_cases() if case["id"] == "web_page/specialized-1")
    path, = write_sources(case, tmp_path / "sources")
    from bs4 import BeautifulSoup
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    assert document.article.h1.get_text() == case["expected"]["标题"]
    assert [node.get_text() for node in document.article.find_all("p")] == case["expected"]["正文"]
    assert "忽略目标" in document.aside.get_text()


@pytest.mark.parametrize("amount, matches", [("12.00", True), ("13", False), ("NaN", False), ("Infinity", False), ("", False)])
def test_csv_amount_equivalence_is_finite_and_column_scoped(amount, matches):
    assert equal_csv_rows([{"项目": "交通", "金额": amount}], [{"项目": "交通", "金额": "12"}]) is matches


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_response,cleanup_failure", [(True, False), (False, False), (False, True)])
async def test_entrypoint_unknown_keeps_request_identity_and_cancels_known_task(tmp_path, monkeypatch, lost_response, cleanup_failure):
    from scripts import evaluate_workbench_flows as flows
    account = tmp_path / "account.json"
    account.write_text('{"username":"synthetic","password":"synthetic"}', encoding="utf-8")
    called = []
    elapsed = [0]
    def respond(request):
        called.append((request.method, request.url.path))
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"user_id": "synthetic"})
        if path.endswith("/preferences/default"):
            return httpx.Response(200, json={"preference": {"connection_id": "synthetic", "model_id": "synthetic"}})
        if path.endswith("/uploads"):
            return httpx.Response(200, json={"upload_id": "synthetic"})
        if path == "/api/semantic-workspace/tasks":
            record = json.loads((tmp_path / "run/receipt.json").read_text(encoding="utf-8"))
            assert record["idempotency_key"] == request.headers["Idempotency-Key"]
            if lost_response:
                raise httpx.ReadTimeout("合成响应丢失", request=request)
            elapsed[0] = 241
            return httpx.Response(202, json={"task_id": "synthetic", "status": "running"})
        if path.endswith("/draft"):
            return httpx.Response(404, json={"detail": "没有初稿"})
        if path.endswith("/cancel"):
            return httpx.Response(500 if cleanup_failure else 200, json={"status": "cancelled"})
        if path.endswith("/synthetic"):
            if cleanup_failure:
                return httpx.Response(502, text="上游状态未知")
            return httpx.Response(200, json={"task_id": "synthetic", "status": "cancelled"})
        raise AssertionError(path)
    client = httpx.AsyncClient
    monkeypatch.setattr(flows.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(flows.time, "monotonic", lambda: elapsed[0])
    await flows.run(SimpleNamespace(output=tmp_path / "run", account_file=account, base_url="http://localhost", case=["receipt"]))
    result = json.loads((tmp_path / "run/receipt.json").read_text(encoding="utf-8"))
    assert result["status"] == ("unknown_transport" if lost_response else "unknown_timeout")
    assert ("POST", "/api/semantic-workspace/tasks/synthetic/cancel") in called if not lost_response else len([call for call in called if call[1] == "/api/semantic-workspace/tasks"]) == 1


def test_semantic_judge_requires_exact_ids_and_boolean_decisions():
    from scripts.judge_workspace_reports import validate_judgments
    result = {"id": "one", "grounded": True, "covers_selected": True, "bounded": True, "reason": "有来源支持"}
    assert validate_judgments([result], ["one"]) == [result]
    for wrong in ({**result, "id": "different"}, {**result, "grounded": "true"}, {**result, "unexpected": True}):
        with pytest.raises(ValueError):
            validate_judgments([wrong], ["one"])


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
async def test_workbench_probe_uses_prepare_route_for_json_and_stream(tmp_path, monkeypatch, sse):
    from scripts import evaluate_workbench_flows as flows
    account = tmp_path / "account.json"
    save(account, {"username": "synthetic", "password": "synthetic"})
    def respond(request):
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"user_id": "synthetic"})
        if request.url.path.endswith("/preferences/default"):
            return httpx.Response(200, json={"preference": {"connection_id": "synthetic", "model_id": "synthetic"}})
        assert request.url.path == "/api/semantic-workspace/draft/turns"
        body = json.loads(request.content)
        record = json.loads((tmp_path / "run/email.json").read_text(encoding="utf-8"))
        assert body["request_id"] == record["request_id"] and body["history"] == []
        answer = {"reply": "请提供收件邮箱", "conv_id": "synthetic"}
        if sse:
            return httpx.Response(200, text="event: result\ndata: " + json.dumps(answer) + "\n\n", headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=answer)
    client = httpx.AsyncClient
    monkeypatch.setattr(flows.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    await flows.run(SimpleNamespace(output=tmp_path / "run", account_file=account, base_url="http://localhost", case=["email"]))
    result = json.loads((tmp_path / "run/email.json").read_text(encoding="utf-8"))
    assert result["status"] == "observed" and result["result"]["reply"] == "请提供收件邮箱"


def test_final_report_requires_completed_runtime_and_matching_semantic_evidence(tmp_path):
    case = next(case for case in generate_cases() if case["id"] == "reputation/normal-1")
    source = tmp_path / "raw"
    output = source / "workspace/output"
    output.mkdir(parents=True)
    save(source / "cases.json", [case])
    save(output / "result.json", case["expected"])
    report = output / "report.md"
    report.write_text("A使用方便但售后慢；B性能稳定但价格高。仅适用于本快照两条来源。", encoding="utf-8")
    record = {"status": "passed", "runtime_status": "needs_input", "workspace_root": "workspace", "events": [{"type": "draft.ready"}],
              "output_sha256": grade_outputs(case, output)["output_sha256"]}
    save(source / "results" / (case["id"] + ".json"), record)
    assert finalize(tmp_path / "final", [source], [case])["counts"] == {"needs_review": 1}
    review = {"id": case["id"], "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
              "grounded": True, "covers_selected": True, "bounded": True}
    save(source / "semantic-reviews" / (case["id"] + ".json"), review)
    assert finalize(tmp_path / "final", [source], [case])["counts"] == {"passed": 1}
    save(source / "results" / (case["id"] + ".json"), {**record, "status": "inflight_unknown"})
    assert finalize(tmp_path / "final", [source], [case])["counts"] == {"inflight_unknown": 1}
    for overrides in ({"runtime_status": "failed"}, {"runtime_status": "cancelled"}, {"workspace_root": None}):
        save(source / "results" / (case["id"] + ".json"), {**record, **overrides})
        assert finalize(tmp_path / "final", [source], [case])["counts"] == {"failed": 1}
    save(source / "results" / (case["id"] + ".json"), record)
    report.write_text("被修改的报告不能复用旧判定", encoding="utf-8")
    with pytest.raises(ValueError, match="不匹配"):
        finalize(tmp_path / "final", [source], [case])
    with pytest.raises(ValueError, match="多次执行"):
        finalize(tmp_path / "final", [source, source], [case])
    changed = {**case, "prompt": case["prompt"] + "新的约束"}
    assert finalize(tmp_path / "new", [source], [changed])["counts"] == {"not_run": 1}
