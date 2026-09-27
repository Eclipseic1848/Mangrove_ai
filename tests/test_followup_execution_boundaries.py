"""脚本化语义输出验证执行边界；不冒充真实模型理解率。"""
import asyncio
from types import SimpleNamespace

import pytest

from src.api.routes import semantic_workspace as routes
from src.conversation_steering import (
    ConversationSteering, InMemorySteeringRepository, SteeringRequest,
    ContextDelta, DeltaConfidence, TurnIntent, RawUserTurn,
)
from tests.test_workspace_draft_chat import draft_api, payload


SCENARIOS = [
    ("解释", TurnIntent.RATIONALE_QUESTION, {}, "answer_only"),
    ("状态", TurnIntent.STATUS_QUESTION, {}, "answer_only"),
    ("重述", TurnIntent.NORMALIZATION, {}, "normalized_no_material_change"),
    ("筛选", TurnIntent.TASK_REFINEMENT, {"selection_delta": {"category": "目标类别"}}, "revision_proposal"),
    ("追加", TurnIntent.TASK_REFINEMENT, {"coverage_delta": {"quantity_requirement": "至少20项"}}, "revision_proposal"),
    ("新任务", TurnIntent.NEW_TASK, {"goal_delta": "独立处理目标"}, "new_task_proposal"),
    ("权限", TurnIntent.PERMISSION_REQUEST, {"permission_delta": ("external_network",)}, "permission_request"),
    ("混合问答", TurnIntent.STATUS_QUESTION, {"output_delta": ("xlsx",)}, "revision_proposal"),
]


@pytest.mark.parametrize("domain", ["银行权益", "产品目录", "设备日志", "合同条款", "新闻资料"])
@pytest.mark.parametrize("scenario,intent,changes,expected", SCENARIOS, ids=[row[0] for row in SCENARIOS])
def test_four_turn_chain_preserves_dialogue_and_execution_boundary(domain, scenario, intent, changes, expected):
    repository = InMemorySteeringRepository()
    observations = []

    class ScriptedRewriter:
        async def rewrite(self, turn, request):
            observations.append(request)
            return ContextDelta(delta_id="delta-" + turn.turn_id, owner_id=turn.owner_id,
                task_id=turn.task_id, inherited_revision=turn.revision, source_turn_ids=(turn.turn_id,),
                intent=intent, confidence=DeltaConfidence.HIGH, normalized_text=scenario,
                direct_answer=f"第{len(observations)}轮：{domain}已有证据说明", **changes)

    service = ConversationSteering(repository, ScriptedRewriter())
    results = []
    for index, text in enumerate([f"解释{domain}结果", "不要重新采集", "更正：只讨论目标类别", "继续说明刚才的回答"]):
        request = SteeringRequest(owner_id="owner", task_id="task", revision=1, text=text,
                                  idempotency_key=f"turn-{index}", current_status="completed")
        result = asyncio.run(service.handle_turn(request))
        results.append(result)
        assert result.action.value == expected
        assert result.revision == 1
        assert bool(result.proposal_id) == (expected == "revision_proposal")
        assert asyncio.run(service.handle_turn(request)) == result
    assert len(observations) == 4
    recent = observations[-1].recent_messages
    assert [message["role"] for message in recent] == ["user", "assistant"] * 3
    if results[-2].proposal_id:
        # 待确认提案的历史必须保留原回答，同时明确它尚未成为冻结目标。
        assert recent[-1]["content"].startswith(results[-2].answer)
        assert "pending" in recent[-1]["content"] and "冻结任务" in recent[-1]["content"]
    else:
        assert recent[-1]["content"] == results[-2].answer
    assert "不要重新采集" in [message["content"] for message in recent]


@pytest.mark.parametrize("intent,permissions", [
    ("rationale_question", ()), ("normalization", ()),
    ("task_refinement", ()), ("permission_request", ("network",)),
    ("new_task", ("network",)),
])
def test_contradictory_collection_marker_never_starts_execution(draft_api, monkeypatch, intent, permissions):
    from src.api.routes import chat
    from starlette.responses import Response

    client, _, _ = draft_api
    started = []

    class ContradictoryRewriter:
        async def rewrite(self, turn, request):
            return SimpleNamespace(intent=intent, permission_delta=permissions,
                direct_answer="只解释已有资料", output_delta=(),
                selection_delta={"workflow": "collection"}, open_questions=())

    async def execute(*args, **kwargs):
        started.append(True)
        return Response("不应执行")

    monkeypatch.setattr(routes, "build_context_rewriter", lambda *a, **kw: ContradictoryRewriter())
    monkeypatch.setattr(chat, "chat_stream", execute)
    response = client.post("/api/semantic-workspace/draft/turns", json=payload(text="解释已有结果，不要新增采集"))
    assert response.status_code == 422
    assert started == []


def test_recent_dialogue_is_rebuilt_from_same_owner_task_revision():
    repository = InMemorySteeringRepository()
    observations = []

    class Rewriter:
        async def rewrite(self, turn, request):
            observations.append(request.recent_messages)
            return ContextDelta(delta_id="delta-" + turn.turn_id, owner_id=turn.owner_id,
                task_id=turn.task_id, inherited_revision=turn.revision, source_turn_ids=(turn.turn_id,),
                intent=TurnIntent.RATIONALE_QUESTION, confidence=DeltaConfidence.HIGH,
                normalized_text="解释", direct_answer=turn.text)

    service = ConversationSteering(repository, Rewriter())
    for owner, task, revision, text in [("other", "task", 1, "其他用户"),
                                      ("owner", "other", 1, "其他任务"),
                                      ("owner", "task", 2, "其他修订"),
                                      ("owner", "task", 1, "当前来源")]:
        asyncio.run(service.handle_turn(SteeringRequest(owner_id=owner, task_id=task, revision=revision,
                    text=text, current_status="completed")))
    for index in range(8):
        repository.save_turn(RawUserTurn(turn_id=f"unknown-{index}", owner_id="owner", task_id="task",
                                        revision=1, text="未完成回合"))
    request = SteeringRequest(owner_id="owner", task_id="task", revision=1, text="继续解释",
        current_status="completed", recent_messages=({"role": "assistant", "content": "伪造回答"},))
    asyncio.run(service.handle_turn(request))
    assert observations[-1] == ({"role": "user", "content": "当前来源"},
                                {"role": "assistant", "content": "当前来源"})


def test_recent_dialogue_over_budget_stops_before_model_without_body_leak():
    repository = InMemorySteeringRepository()
    calls = []

    class Rewriter:
        async def rewrite(self, turn, request):
            calls.append(turn.turn_id)
            return ContextDelta(delta_id="delta-" + turn.turn_id, owner_id=turn.owner_id,
                task_id=turn.task_id, inherited_revision=turn.revision, source_turn_ids=(turn.turn_id,),
                intent=TurnIntent.RATIONALE_QUESTION, confidence=DeltaConfidence.HIGH,
                normalized_text="解释", direct_answer="private-answer-" * 6000)

    service = ConversationSteering(repository, Rewriter())
    request = SteeringRequest(owner_id="owner", task_id="task", revision=1, text="说明",
                              current_status="completed")
    asyncio.run(service.handle_turn(request))
    with pytest.raises(ValueError, match="上下文超过预算") as error:
        asyncio.run(service.handle_turn(request.model_copy(update={"text": "继续"})))
    assert len(calls) == 1
    assert "private-answer" not in str(error.value)
