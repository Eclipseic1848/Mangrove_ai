"""内部质检只指导当前报告修订，不能冒充用户提供的业务历史。"""
import socket

import pytest

from src.conductor.nodes import analyze
from src.conductor.task_spec import TaskSpec


@pytest.fixture(autouse=True)
def isolate_analysis(monkeypatch):
    from src.api import auth

    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo

    def connect(sock, address):
        if address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("分析提示回归禁止真实模型或来源请求")
        return original_connect(sock, address)

    def resolve(host, *args, **kwargs):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("分析提示回归禁止真实模型或来源请求")
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(auth, "get_store", lambda: None)
    monkeypatch.setattr(analyze, "lesson_for_analyze", lambda *args, **kwargs: ("", None))
    monkeypatch.setattr(analyze, "skill_for_analysis", lambda *args, **kwargs: "")


@pytest.mark.asyncio
@pytest.mark.parametrize("comparison", [False, True])
async def test_internal_feedback_requests_current_report_without_fabricated_history(monkeypatch, comparison):
    intent = "梳理本次医疗设备公告"
    instruction = "保留公告日期和截止状态"
    content = "本次公告原文：设备预算100万元，公告内容末端。"
    report = "当前完整公告报告，预算100万元。"
    if comparison:
        intent = "对比用户提供的A、B两份报告并说明预算变化"
        instruction = "请保留报告对比：A预算100万元，B预算120万元，说明增加20万元。"
        content = "用户提供的报告A：预算100万元。用户提供的报告B：预算120万元。"
        report = "与用户提供的报告A对比，报告B预算增加20万元。"
    spec = TaskSpec(intent=intent, data_type="bid", analysis_type="summary", analysis_instruction=instruction)
    feedback = ["删除没有来源支持的结论，保留可核对字段。"]
    original = {"task_spec": spec, "cleaned_dataset": [{"title": "合成资料", "content": content}],
                "checker_feedback": feedback, "analysis": "内部首稿不应成为用户历史报告"}
    messages_seen = []

    async def respond(messages, **kwargs):
        messages_seen.append(messages)
        return report

    monkeypatch.setattr(analyze, "achat", respond)
    result = await analyze.analyze_node(original)
    prompt = messages_seen[0][-1]["content"]
    assert "内部质量反馈" in prompt and "不是用户提供的业务事实或历史报告" in prompt
    assert "只输出当前完整报告" in prompt and "不叙述内部质检、重写或更正过程" in prompt
    assert "用户明确要求报告对比" in prompt and "已提供的报告依据" in prompt
    assert all(value in prompt for value in (intent, instruction, content, feedback[0]))
    assert "内部首稿不应成为用户历史报告" not in prompt
    assert len(messages_seen) == 1 and result["analysis"] == report
    assert result["checker_feedback"] is None and original["checker_feedback"] == feedback


@pytest.mark.asyncio
async def test_first_report_does_not_receive_internal_revision_context(monkeypatch):
    requests = []

    async def respond(messages, **kwargs):
        requests.append(messages)
        return "首份完整报告"

    monkeypatch.setattr(analyze, "achat", respond)
    result = await analyze.analyze_node({"task_spec": TaskSpec(intent="汇总本次公告", data_type="bid"),
        "cleaned_dataset": [{"title": "合成公告", "content": "当前来源正文"}]})
    assert "内部质量反馈" not in requests[0][-1]["content"]
    assert result["analysis"] == "首份完整报告" and "checker_feedback" not in result
