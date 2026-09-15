"""将既有采集编排的文本调用绑定到工作台选定的连接，不把密钥交给编排器。"""
from __future__ import annotations

import json
from contextlib import contextmanager

from src.api.execution import execution_checkpoint
from src.account_execution import ExecutionDenied
from src.llm.provider import _bound_chat, _bound_chat_failure, verify_bound_model
from src.model_connections import get_default_broker
from src.model_connections.text_protocol import structured_request, response_text, collect_response_usage


@contextmanager
def conductor_connection(*, owner_id, connection_id, connection_version, model, task_id, run_id):
    broker = get_default_broker()
    failure = []

    async def generate(messages):
        verify_bound_model()
        execution_checkpoint(required=True)
        grant = None
        try:
            grant = broker.issue_grant(
                owner_user_id=owner_id, connection_id=connection_id, connection_version=connection_version,
                model_id=model, task_id=task_id, revision=1, run_id=run_id,
                purpose="agent_inference", ttl_seconds=300,
            )
            system = "\n".join(str(message["content"]) for message in messages if message["role"] == "system")
            turns = [dict(message) for message in messages if message["role"] != "system"]
            path, body, headers = structured_request(api_format=grant.api_format, model=grant.model,
                                                     grant_token=grant.token, system_prompt=system, payload={})
            if grant.api_format == "openai_chat_completions":
                body["messages"] = [{"role": "system", "content": system}, *turns]
            elif grant.api_format == "openai_responses":
                body["input"] = [{"role": "system", "content": system}, *turns]
            elif grant.api_format == "anthropic_messages":
                body["messages"] = turns
            else:
                body["systemInstruction"] = {"parts": [{"text": system}]}
                body["contents"] = [{"role": "model" if turn["role"] == "assistant" else "user",
                                     "parts": [{"text": turn["content"]}]} for turn in turns]
                body["generationConfig"].pop("responseMimeType", None)
            result = await broker.relay(grant_token=grant.token, protocol_path=path, method="POST",
                                        headers=headers, body=json.dumps(body, ensure_ascii=False).encode("utf-8"))
            try:
                raw = b"".join([chunk async for chunk in result.iter_bytes()])
            finally:
                await result.aclose()
            execution_checkpoint(required=True)
            if not 200 <= result.status_code < 300:
                raise ValueError("所选模型调用失败，未切换模型或自动重发")
            collect_response_usage(grant.api_format, raw)
            return response_text(grant.api_format, raw)
        except ExecutionDenied:
            raise
        except Exception:
            # 旧节点可能捕获模型异常并降级；冻结连接失败后必须阻止后续采集或产出。
            failure.append("所选模型调用失败或结果未知，任务已停止，未切换模型或自动重发")
            raise ValueError(failure[0]) from None
        finally:
            if grant is not None:
                broker.revoke_grant(grant.grant_id, "conductor_call_finished")

    token = _bound_chat.set(generate)
    failure_token = _bound_chat_failure.set(failure)
    try:
        yield
    finally:
        _bound_chat.reset(token)
        _bound_chat_failure.reset(failure_token)
