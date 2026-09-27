"""候选嵌套参数必须在写文件前校验，工具 Schema 与宿主使用同一契约。"""
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter, _tool_definitions


def candidate():
    evidence = {"source": "source.txt", "locator": "全文", "quote": "合成资料"}
    return {"filename": "result.txt", "format": "txt", "content": "合成资料",
            "description": "来源原文", "evidence": [evidence],
            "result_items": [{"result_id": "one", "label": "原文", **evidence}],
            "result_search_complete": True}


@pytest.mark.parametrize("field", ["evidence", "result_items", "qualified_omissions"])
@pytest.mark.parametrize("malformed", [{"source_id": "source-a"}, {"source": " ", "locator": "全文", "quote": "资料"}])
def test_invalid_nested_record_never_changes_existing_candidate(tmp_path, field, malformed):
    output = tmp_path / "output"
    output.mkdir()
    (output / "result.txt").write_text("旧候选", encoding="utf-8")
    (output / "candidate-manifest.json").write_text('{"artifacts":[]}', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    args = candidate()
    args[field] = [malformed]
    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path)
    with pytest.raises(ValueError):
        adapter._submit_candidate(SimpleNamespace(requested_output_formats=("txt",)), tmp_path, args)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_declared_nested_schema_and_host_accept_valid_records(tmp_path):
    schema = next(t["parameters"] for t in _tool_definitions() if t["name"] == "mangrove_submit_candidate")
    for field in ("evidence", "result_items", "qualified_omissions"):
        record = schema["properties"][field]["items"]
        assert {"source", "locator", "quote"} <= set(record["required"])
    args = candidate()
    Draft202012Validator(schema).validate(args)
    (tmp_path / "output").mkdir()
    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path)
    adapter._submit_candidate(SimpleNamespace(requested_output_formats=("txt",)), tmp_path, args)
    manifest = json.loads((tmp_path / "output/candidate-manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"][0]["evidence"] == args["evidence"]
    assert manifest["result_items"][0]["result_id"] == "one"
