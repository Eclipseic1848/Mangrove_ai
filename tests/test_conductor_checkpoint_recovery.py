"""真实编排图与SQLite重连；替换业务节点，不请求平台或模型。"""
import asyncio
from collections import Counter

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.conductor import graph as host
from src.config.settings import settings
from tests.test_evidence_collection_flow import spec_for


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("changed", [None, "execution_binding", "user_input", "provider", "model", "session_id", "approved_db_write", "ignore_schedule"])
def test_collection_checkpoint_reconnect_preserves_spec_and_rejects_changed_identity(tmp_path, monkeypatch, streaming, changed):
    calls = Counter()
    spec = spec_for("设备维护规则")
    init = host._build_init(spec.intent, None, "synthetic", "synthetic-model", "conversation", False, False, "synthetic-task")
    monkeypatch.setattr(settings, "checkpoint_enabled", True)

    def node(name):
        async def run(state):
            calls[name] += 1
            if name == "planner":
                return {"task_spec": spec}
            if name == "collect":
                assert state["task_spec"].model_dump(mode="json") == spec.model_dump(mode="json")
                if calls[name] == 1:
                    raise RuntimeError("synthetic interruption")
                return {"evidence_collection": {"synthetic": True}}
            return {"reply": "synthetic complete"} if name == "output" else {}
        return run

    for name in ("intent", "planner", "target_resolve", "router", "collect", "output"):
        monkeypatch.setattr(host, name + "_node", node(name))

    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoint.sqlite")) as saver:
            graph = host.build_graph(checkpointer=saver)
            async def current_graph():
                return graph
            monkeypatch.setattr(host, "_get_checkpoint_graph", current_graph)
            with pytest.raises(RuntimeError, match="synthetic interruption"):
                await host._ainvoke(init)
        before = calls.copy()
        resumed_init = dict(init)
        if changed:
            resumed_init[changed] = True if changed in {"approved_db_write", "ignore_schedule"} else "different"
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoint.sqlite")) as saver:
            graph = host.build_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": init["task_id"]}}
            original = (await graph.aget_state(config)).values
            if streaming:
                # 输入由已保存请求构造；仅替换输入工厂以逐项验证冻结字段。
                monkeypatch.setattr(host, "_build_init", lambda *args, **kwargs: resumed_init)
                async def resume():
                    events = [event async for event in host.astream_conductor(spec.intent)]
                    return next(value for kind, value in events if kind == "final")
            else:
                async def resume():
                    return await host._ainvoke(resumed_init)
            if changed:
                with pytest.raises(ValueError, match="拒绝恢复"):
                    await resume()
                assert calls == before
                assert (await graph.aget_state(config)).values == original
            else:
                result = await resume()
                assert result["reply"] == "synthetic complete"
                assert result["task_spec"].model_dump(mode="json") == spec.model_dump(mode="json")
                assert calls == {"intent": 1, "planner": 1, "target_resolve": 1, "router": 1, "collect": 2, "output": 1}
                completed = calls.copy()
                again = await resume()
                assert again["reply"] == result["reply"] and calls == completed
    asyncio.run(scenario())
