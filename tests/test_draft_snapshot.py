"""初稿必须冻结，后续工具写入不能改变用户已看到的文件。"""
import pytest
from src.agentic_runtime.draft_snapshot import freeze_draft, read_draft


@pytest.mark.parametrize("filename,content,fmt", [
    ("document.json", '{"name":"合成记录"}', "json"),
    ("table.csv", "name,amount\nsynthetic,10\n", "csv"),
    ("collection.md", "# 合成采集结果\n来源为测试数据。", "markdown"),
])
def test_draft_is_frozen_and_owner_bound(tmp_path, filename, content, fmt):
    output = tmp_path / "output"
    output.mkdir()
    (output / filename).write_text(content, encoding="utf-8")
    draft = freeze_draft(tmp_path, owner_id="a", task_id="task", revision=1,
                         run_id="run", formats=(fmt,))
    (output / filename).write_text("changed", encoding="utf-8")
    saved = read_draft(tmp_path, draft["draft_id"], owner_id="a", task_id="task", revision=1, run_id="run")
    assert (tmp_path / "drafts" / draft["draft_id"] / filename).read_text(encoding="utf-8") == content
    assert saved["files"] == draft["files"]
    with pytest.raises(ValueError):
        read_draft(tmp_path, draft["draft_id"], owner_id="b", task_id="task", revision=1, run_id="run")


def test_draft_rejects_missing_format_and_tampering(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "result.json").write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError):
        freeze_draft(tmp_path, owner_id="a", task_id="t", revision=1, run_id="r", formats=("json", "csv"))
    draft = freeze_draft(tmp_path, owner_id="a", task_id="t", revision=1, run_id="r", formats=("json",))
    (tmp_path / "drafts" / draft["draft_id"] / "result.json").write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_draft(tmp_path, draft["draft_id"], owner_id="a", task_id="t", revision=1, run_id="r")


@pytest.mark.parametrize("fmt", ["xlsx", "docx", "pptx", "pdf"])
def test_incomplete_office_or_pdf_is_not_a_fatal_runtime_error(tmp_path, fmt):
    output = tmp_path / "output"
    output.mkdir()
    (output / f"result.{fmt}").write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="初稿"):
        freeze_draft(tmp_path, owner_id="a", task_id="t", revision=1, run_id="r", formats=(fmt,))


@pytest.mark.asyncio
@pytest.mark.parametrize("name,content,fmt", [("result.json", '{"name":"文档"}', "json"),
    ("table.csv", "name,value\na,1\n", "csv"), ("collection.md", "# 合成来源报告", "markdown")])
async def test_runtime_emits_frozen_draft_before_finishing_or_verifying(tmp_path, monkeypatch, name, content, fmt):
    import hashlib
    from src.agentic_runtime.pi_runtime import PiRuntime, PiRuntimeError
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    from src.agentic_runtime.models import PiRuntimeRequest, SourceInput
    from tests.database_migration_helpers import migrated_webui_database
    output = tmp_path / "output"
    output.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("synthetic", encoding="utf-8")
    runtime = PiRuntime(execution_root=tmp_path, state_store=AgenticRuntimeRepository(migrated_webui_database(tmp_path / "db.sqlite")))
    events = []
    async def sink(event):
        events.append(event)
    async def process_boundary(request, **kwargs):
        await kwargs["capture_draft"]()
        assert not events
        (output / name).write_text(content, encoding="utf-8")
        await kwargs["capture_draft"]()
        assert events[-1].event_type == "draft.ready"
        saved = read_draft(tmp_path, events[-1].details["draft_id"], owner_id="a", task_id="t", revision=1, run_id="r")
        assert saved["files"][0]["sha256"] == hashlib.sha256((output / name).read_bytes()).hexdigest()
        await kwargs["capture_draft"]()
        assert len(events) == 1
        raise PiRuntimeError("合成进程停点：尚未开始最终验证")
    monkeypatch.setattr(runtime, "_run_rpc", process_boundary)
    request = PiRuntimeRequest(user_id="a", task_id="t", revision=1, objective_text="合成任务",
        sources=(SourceInput(upload_id="u", original_name=source.name, host_path=source, sha256=hashlib.sha256(source.read_bytes()).hexdigest()),),
        requested_output_formats=(fmt,), model="synthetic", base_url="http://127.0.0.1:1/v1", api_key="synthetic")
    with pytest.raises(PiRuntimeError, match="合成进程停点"):
        await runtime._execute_run(request, run_id="r", root=tmp_path, output_dir=output, session_dir=tmp_path / "session",
            trace_dir=tmp_path / "trace", container_name="not-started", command=(), on_event=sink)
