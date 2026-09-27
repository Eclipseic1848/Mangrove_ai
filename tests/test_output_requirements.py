"""用户原话衍生检查的冻结、重用和本地判分；模型是唯一替身边界。"""
import json
from types import SimpleNamespace

import pytest

from src.agentic_runtime.output_requirements import freeze_output_requirements, output_requirement_issues


def request():
    return SimpleNamespace(user_id="owner", task_id="task", revision=1,
        objective_text="输出result.json对象，只含name和enabled，enabled必须是布尔值。",
        requested_output_formats=("json",))


@pytest.mark.asyncio
async def test_interrupted_host_record_warns_without_resending_or_blocking_draft(tmp_path):
    from src.agentic_runtime.draft_snapshot import freeze_draft, draft_table_issues
    host = tmp_path / "host-state"
    host.mkdir()
    (host / "output-requirements.json").write_text('{', encoding="utf-8")
    async def forbidden():
        pytest.fail("未知请求不得重发")
    frozen = await freeze_output_requirements(tmp_path, request(), "run", forbidden)
    assert frozen["status"] == "unverified"
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "result.json").write_text('{}', encoding="utf-8")
    draft = freeze_draft(tmp_path, owner_id="owner", task_id="task", revision=1, run_id="run", formats=("json",))
    assert "未能完成" in draft_table_issues(tmp_path, draft, ())[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["deepseek", "qwen"])
async def test_extraction_avoids_reasoning_budget_exhaustion_on_supported_provider(tmp_path, preset):
    import httpx
    from src.model_connections import ConnectionBroker
    from src.model_connections.catalog import PRESETS_BY_ID
    from src.model_connections.storage import ModelConnectionRepository
    from src.model_connections.vault import FernetCredentialVault
    from src.agentic_runtime.output_requirements import infer_output_requirements
    from tests.database_migration_helpers import migrated_webui_database
    from src.account_execution import execution_context
    from tests.account_execution_helpers import seed_execution_owner
    requests = []
    def provider(incoming):
        body = json.loads(incoming.content)
        extracting = body.get("max_tokens", body.get("max_output_tokens")) == 8000
        if extracting:
            requests.append(body)
        exhausted = extracting and preset == "deepseek" and body.get("thinking") != {"type": "disabled"}
        if incoming.url.path.endswith("/responses"):
            return httpx.Response(200, json={"object": "response", "status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": '{"checks":[]}'}]}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "length" if exhausted else "stop",
            "message": {"role": "assistant", "content": json.dumps({"checks": []})}}]})
    broker = ConnectionBroker(repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "db.sqlite"))),
        vault=FernetCredentialVault.generate(), transport=httpx.MockTransport(provider), resolver=lambda _: ["8.8.8.8"])
    model = PRESETS_BY_ID[preset].recommended_model
    connection = await broker.configure_personal(owner_user_id="owner", preset_id=preset, api_key="synthetic", model=model)
    binding = broker.freeze_connection("owner", connection["connection_id"])
    req = request()
    req.model_connection_id = binding.connection_id
    req.model_connection_version = binding.connection_version
    req.model_connection_model = model
    with execution_context(seed_execution_owner(tmp_path / "db.sqlite", "owner")):
        value = await freeze_output_requirements(tmp_path, req, "run", lambda: infer_output_requirements(req, "run", broker))
    assert value["status"] == "ready"
    assert len(requests) == 1
    if preset == "deepseek":
        assert requests[0]["response_format"] == {"type": "json_object"}
    else:
        assert "thinking" not in requests[0] and "response_format" not in requests[0]


@pytest.mark.asyncio
async def test_requirements_use_user_quote_and_freeze_once(tmp_path):
    calls = []
    async def model():
        calls.append(1)
        return {"checks": [{"filename": "result.json", "evidence": "只含name和enabled，enabled必须是布尔值",
            "schema": {"type": "object", "properties": {"name": {}, "enabled": {"type": "boolean"}},
                       "required": ["name", "enabled"], "additionalProperties": False}}]}
    frozen = await freeze_output_requirements(tmp_path, request(), "run", model)
    assert frozen["status"] == "ready"
    assert await freeze_output_requirements(tmp_path, request(), "run", model) == frozen
    assert calls == [1]
    output = tmp_path / "output"
    output.mkdir()
    (output / "result.json").write_text('{"name":"甲","enabled":"true"}', encoding="utf-8")
    assert output_requirement_issues(output, frozen)
    (output / "result.json").write_text('{"name":"甲","enabled":true}', encoding="utf-8")
    assert output_requirement_issues(output, frozen) == []


@pytest.mark.asyncio
async def test_unknown_model_result_is_not_resent(tmp_path):
    calls = []
    async def model():
        calls.append(1)
        raise TimeoutError()
    frozen = await freeze_output_requirements(tmp_path, request(), "run", model)
    assert frozen["status"] == "unverified"
    assert await freeze_output_requirements(tmp_path, request(), "run", model) == frozen
    assert calls == [1]
    assert output_requirement_issues(tmp_path, frozen)


@pytest.mark.asyncio
async def test_unknown_provider_failure_keeps_safe_cause_without_resending(tmp_path):
    import httpx
    from src.model_connections import ProviderOutcomeUnknownError
    calls = []
    async def model():
        calls.append(1)
        try:
            raise httpx.ReadError("不能记录的业务正文和凭据")
        except httpx.ReadError as error:
            raise ProviderOutcomeUnknownError("结果未知") from error
    frozen = await freeze_output_requirements(tmp_path, request(), "run", model)
    assert frozen["status"] == "unverified"
    assert frozen["error_type"] == "ProviderOutcomeUnknownError"
    assert frozen["cause_type"] == "ReadError"
    assert "不能记录" not in json.dumps(frozen, ensure_ascii=False)
    assert await freeze_output_requirements(tmp_path, request(), "run", model) == frozen
    assert calls == [1]


@pytest.mark.asyncio
async def test_local_rejection_preserves_safe_reason_without_resending(tmp_path):
    req = request()
    async def model():
        return {"checks": [{"filename": "result.json", "evidence": req.objective_text,
            "schema": {"type": "object", "format": "含敏感输入的未知格式"}}]}

    frozen = await freeze_output_requirements(tmp_path, req, "run", model)
    assert frozen["status"] == "unverified" and frozen["checks"] == []
    assert frozen["error_type"] == "ValueError"
    assert frozen.get("error_stage") == "validation"
    assert frozen.get("validation_reason") == "输出检查含未支持的关键字"
    assert "敏感输入" not in json.dumps(frozen, ensure_ascii=False)
    async def forbidden():
        pytest.fail("已有拒绝记录不得重发模型请求")
    assert await freeze_output_requirements(tmp_path, req, "run", forbidden) == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["inference", "validation"])
async def test_untrusted_exception_text_never_enters_requirement_diagnostics(tmp_path, stage):
    req = request()
    secret = "凭据和业务原文不应落入异常诊断"
    async def model():
        if stage == "inference":
            raise ValueError(secret)
        return {"checks": [{"filename": "result.json", "evidence": req.objective_text,
            "schema": {"type": secret}}]}

    frozen = await freeze_output_requirements(tmp_path, req, "run", model)
    assert frozen["status"] == "unverified" and frozen["checks"] == []
    assert frozen.get("error_stage") == stage
    assert "validation_reason" not in frozen
    assert secret not in json.dumps(frozen, ensure_ascii=False)


@pytest.mark.parametrize("field", ["name", "金额", "flag", "item_42", "schedule", "标题", "x-y", "字段八"])
@pytest.mark.parametrize("schema,good,bad", [
    ({"type": "boolean"}, True, "true"),
    ({"type": "number"}, 3.5, "3.5"),
    ({"type": ["string", "null"]}, None, 3),
    ({"type": "array", "minItems": 1, "maxItems": 2}, [1], []),
    ({"type": "string", "pattern": "^cron@"}, "cron@0 9 * * 1", "0 9 * * 1"),
    ({"type": ["string", "null"], "pattern": "^prefix_"}, None, "missing_prefix"),
])
def test_generic_field_type_count_and_prefix_checks(tmp_path, field, schema, good, bad):
    frozen = {"status": "ready", "checks": [{"filename": "result.json", "evidence": "用户明确的结构要求",
        "schema": {"type": "object", "properties": {field: schema}, "required": [field], "additionalProperties": False}}]}
    path = tmp_path / "result.json"
    for value in ({field: bad}, {}, {field: good, "extra": 1}):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        assert output_requirement_issues(tmp_path, frozen)
    path.write_text(json.dumps({field: good}, ensure_ascii=False), encoding="utf-8")
    assert output_requirement_issues(tmp_path, frozen) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"evidence": "不在用户原话中"}, {"filename": "../result.json"},
    {"schema": {"$ref": "https://example.test/schema"}},
    {"schema": {"type": "string", "pattern": "^(a+)+$"}},
])
async def test_untrusted_inference_cannot_expand_checks(tmp_path, change):
    async def model():
        return {"checks": [{"filename": "result.json", "evidence": "enabled必须是布尔值",
            "schema": {"type": "object"}, **change}]}
    frozen = await freeze_output_requirements(tmp_path, request(), "run", model)
    assert frozen["status"] == "unverified" and frozen["checks"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["result.json", "details.csv"])
async def test_inference_does_not_relax_duplicate_or_non_json_guards(tmp_path, filename):
    req = request()
    req.objective_text += "另输出details.csv。"
    async def model():
        return {"checks": [
            {"filename": "result.json", "evidence": "enabled必须是布尔值", "schema": {"type": "object"}},
            {"filename": filename, "evidence": "enabled必须是布尔值", "schema": {"type": "object"}},
        ]}
    value = await freeze_output_requirements(tmp_path, req, "run", model)
    assert value["status"] == "unverified" and not value["checks"]


@pytest.mark.asyncio
async def test_frozen_interpretation_cannot_drift_to_another_goal(tmp_path):
    async def model():
        return {"checks": []}
    original = request()
    await freeze_output_requirements(tmp_path, original, "run", model)
    original.objective_text = "新的不同要求"
    with pytest.raises(ValueError, match="目标已变化"):
        await freeze_output_requirements(tmp_path, original, "run", model)


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
@pytest.mark.parametrize("ordered,allow_extra", [(True, False), (False, False), (False, True), (True, True)])
async def test_table_checks_warn_on_declared_columns_without_adding_limits(tmp_path, fmt, ordered, allow_extra):
    import csv
    from openpyxl import Workbook
    from src.agentic_runtime.draft_snapshot import freeze_draft, draft_table_issues

    req = request()
    req.requested_output_formats = (fmt,)
    req.objective_text = f"输出result.{fmt}，包含部门、金额两列。"
    req.objective_text += "按上述列顺序。" if ordered else "列顺序不限。"
    req.objective_text += "允许额外列。" if allow_extra else "不允许额外列。"
    calls = []
    async def model():
        calls.append(1)
        return {"checks": [{"filename": f"result.{fmt}", "evidence": req.objective_text,
            "table": {"format": fmt, "columns": ["部门", "金额"], "ordered": ordered, "allow_extra": allow_extra}}]}
    frozen = await freeze_output_requirements(tmp_path, req, "run", model)
    assert frozen["status"] == "ready"
    assert await freeze_output_requirements(tmp_path, req, "run", model) == frozen
    assert calls == [1]
    output = tmp_path / "output"
    output.mkdir()
    for columns, valid in [(["部门", "金额"], True), (["部门"], False),
                           (["金额", "部门"], not ordered), (["部门", "金额", "备注"], allow_extra),
                           (["部门", "部门", "金额"], allow_extra),
                           (["部门", "金额", "备注", "备注"], allow_extra)]:
        path = output / f"result.{fmt}"
        if fmt == "csv":
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows([columns, ["合成"] * len(columns)])
        else:
            book = Workbook()
            book.active.append(columns)
            book.active.append(["合成"] * len(columns))
            book.save(path)
            book.close()
        draft = freeze_draft(tmp_path, owner_id="owner", task_id="task", revision=1, run_id="run", formats=(fmt,))
        issues = draft_table_issues(tmp_path, draft, ())
        assert bool(issues) is not valid, (columns, issues)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"format": "json"}, {"columns": []}, {"columns": ["金额", "金额"]},
    {"columns": [42]}, {"ordered": "false"}, {"allow_extra": None}, {"extra": 1},
])
async def test_invalid_table_inference_warns_without_resending(tmp_path, change):
    req = request()
    req.objective_text = "输出result.csv，包含金额列。"
    req.requested_output_formats = ("csv",)
    calls = []
    async def model():
        calls.append(1)
        return {"checks": [{"filename": "result.csv", "evidence": req.objective_text,
            "table": {"format": "csv", "columns": ["金额"], "ordered": False, "allow_extra": True, **change}}]}
    frozen = await freeze_output_requirements(tmp_path, req, "run", model)
    assert frozen["status"] == "unverified" and not frozen["checks"]
    assert await freeze_output_requirements(tmp_path, req, "run", model) == frozen
    assert calls == [1]


def test_structure_evaluation_has_independent_positive_and_negative_probes(tmp_path):
    from collections import Counter
    from scripts.evaluate_output_requirements import structure_cases, regression_cases, check_probes

    cases = structure_cases()
    assert Counter(case["kind"] for case in cases) == {"prefix": 40, "compact_prefix": 40, "conditional_prefix": 40, "csv": 40, "xlsx": 40}
    assert len({case["id"] for case in cases}) == len({case["prompt"] for case in cases}) == 200
    regressions = regression_cases()
    assert len(regressions) == 9
    for case in cases + regressions:
        positives = case.get("valid_outputs", [{"result.json": case.get("expected")}])
        assert positives and case["mutations"]
        assert all(m["outputs"] not in positives for m in case["mutations"])
    # 空规则不能因“没有警告”冒充通过，模型未知也不能算检测到反例。
    for status in ("ready", "unverified"):
        probes = check_probes(cases[0], tmp_path / status, {"status": status, "checks": []})
        assert not probes["oracle_compatible"]
        assert not any(m["detected"] for m in probes["mutations"])


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["object", "nested", "array", "nullable_array", "literal_star"])
async def test_explicit_prefix_is_compiled_without_changing_the_model_record(tmp_path, shape):
    from copy import deepcopy

    field = "*" if shape == "literal_star" else "业务标识"
    leaf = {"type": ["string", "null"]}
    schema = {"type": "object", "properties": {field: leaf}, "required": [field]}
    path = [field]
    wrap = lambda value: {field: value}
    if shape == "nested":
        schema = {"type": "object", "properties": {"payload": schema}, "required": ["payload"]}
        path = ["payload", field]
        wrap = lambda value: {"payload": {field: value}}
    elif shape in {"array", "nullable_array"}:
        schema = {"type": ["array", "null"] if shape == "nullable_array" else "array", "items": schema}
        path = ["*", field]
        wrap = lambda value: [{field: value}]
    req = request()
    req.objective_text = f"输出result.json，{field}使用ref@前缀，缺失时允许null。"
    raw = {"checks": [{"filename": "result.json", "evidence": req.objective_text, "schema": schema,
        "prefixes": [{"path": path, "prefix": "ref@", "evidence": f"{field}使用ref@前缀"}]}]}
    original = deepcopy(raw)

    async def model():
        return raw

    frozen = await freeze_output_requirements(tmp_path, req, "run", model)
    assert frozen["status"] == "ready"
    assert "prefixes" not in frozen["checks"][0]
    assert raw == original, "保留原始回执，不能编译时改写提取证据"
    target = tmp_path / "result.json"
    for value in (wrap("ref@abc"), wrap(None)):
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        assert output_requirement_issues(tmp_path, frozen) == []
    target.write_text(json.dumps(wrap("abc"), ensure_ascii=False), encoding="utf-8")
    assert output_requirement_issues(tmp_path, frozen)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"path": ["invented"]}, {"path": "业务标识"}, {"path": ["*", "业务标识"]},
    {"prefix": "^(a+)+$"}, {"prefix": "not_in_quote"}, {"evidence": "自己编造的要求"},
])
async def test_prefix_metadata_cannot_add_paths_regexes_or_unquoted_requirements(tmp_path, change):
    req = request()
    req.objective_text = "输出result.json，业务标识使用ref@前缀。"
    async def model():
        return {"checks": [{"filename": "result.json", "evidence": req.objective_text,
            "schema": {"type": "object", "properties": {"业务标识": {"type": "string"}}},
            "prefixes": [{"path": ["业务标识"], "prefix": "ref@", "evidence": req.objective_text, **change}]}]}
    frozen = await freeze_output_requirements(tmp_path, req, "run", model)
    assert frozen["status"] == "unverified" and frozen["checks"] == []
