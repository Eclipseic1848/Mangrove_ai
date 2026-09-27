"""目标不符时，高分不能覆盖失败判定或显示成功。"""
import asyncio
import json

import pytest

from src.config.settings import settings
from src.conductor.nodes import checker, output
from src.conductor.task_spec import TaskSpec


@pytest.mark.parametrize("verdict", [False, None, "false"])
def test_high_score_cannot_override_failed_or_missing_verdict(tmp_path, monkeypatch, verdict):
    async def judge(*args, **kwargs):
        payload = {"score": 80, "issues": ["样本与中信私银无关"], "summary": "报告如实说明了数据错误"}
        if verdict is not None:
            payload["passed"] = verdict
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(checker, "achat", judge)
    monkeypatch.setattr(output, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "checker_enabled", True)
    monkeypatch.setattr(settings, "checker_rerun_enabled", False)
    monkeypatch.setattr(settings, "template_learning_enabled", True)
    state = {
        "task_spec": TaskSpec(intent="采集10条中信私银笔记", keywords=["中信私银"], max_items=10),
        "task_id": "mismatched-results", "analysis_source": "fallback",
        "analysis": "全部样本围绕你好，与中信私银无关，无法完成任务。",
        "cleaned_dataset": [{"title": "你好", "content": "与目标无关的合成正文。"}],
    }

    async def run():
        state.update(await checker.checker_node(state))
        assert state["quality"]["passed"] is False
        result = await output.output_node(state)
        assert "已完成采集与分析" not in result["reply"]
        assert "✅" not in result["reply"]
        assert result["grade"]["level"] == "未通过"
        assert not result["outputs"].get("template_suggest")
        assert "未通过" in result["outputs"]["report_text"]

    asyncio.run(run())
