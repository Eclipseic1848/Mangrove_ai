"""将既有采集编排的文本调用绑定到工作台选定的连接，不把密钥交给编排器。"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

from src.api.execution import execution_checkpoint
from src.account_execution import ExecutionDenied
from src.config.settings import settings
from src.llm.provider import _bound_chat, _bound_chat_failure, _bound_chat_identity, _DEEPSEEK_THINKING_MODELS, verify_bound_model
from src.model_connections import get_default_broker
from src.model_connections.text_protocol import structured_request, response_text, collect_response_usage, ModelOutputTruncatedError


# 百炼文档核验这两款支持 none：https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses
_QWEN_RESPONSES_REASONING_MODELS = ("qwen3.8-max", "qwen3.8-flash")


@contextmanager
def conductor_connection(*, owner_id, connection_id, connection_version, model, task_id, run_id, revision=1):
    broker = get_default_broker()
    failure = []

    async def generate(messages, *, max_tokens=None, thinking=None):
        verify_bound_model()
        execution_checkpoint(required=True)
        grant = None
        try:
            call_budget = settings.pi_runtime_timeout_seconds
            grant = broker.issue_grant(
                owner_user_id=owner_id, connection_id=connection_id, connection_version=connection_version,
                model_id=model, task_id=task_id, revision=revision, run_id=run_id,
                # 授权覆盖整次等待预算及短暂收口；停用、撤销仍由 Broker 实时检查。
                purpose="agent_inference", ttl_seconds=call_budget + 60,
            )
            system = "\n".join(str(message["content"]) for message in messages if message["role"] == "system")
            turns = [dict(message) for message in messages if message["role"] != "system"]
            output_limit = max_tokens
            if output_limit is None:
                preset = broker.verified_model_preset(owner_id, connection_id, connection_version, model)
                # 未核容量保留旧协议兼容值，不能凭同名自定义模型猜测官方上限。
                if not preset or not preset.max_output_tokens:
                    output_limit = 8192
                elif grant.api_format in {"openai_chat_completions", "anthropic_messages"}:
                    output_limit = preset.max_output_tokens
            path, body, headers = structured_request(api_format=grant.api_format, model=grant.model,
                                                     grant_token=grant.token, system_prompt=system, payload={}, max_tokens=output_limit)
            if (settings.qwen_enable_thinking is False and grant.api_format == "openai_responses"
                    and grant.model in _QWEN_RESPONSES_REASONING_MODELS):
                connection = broker.get_connection(owner_id, grant.connection_id)
                if connection and connection.get("preset_id") == "qwen":
                    # 冻结连接绕过旧 Provider 配置；只补已有关闭设置，不把同名自定义模型当百炼。
                    body["reasoning"] = {"effort": "none"}
            if thinking is not None and grant.model in _DEEPSEEK_THINKING_MODELS:
                connection = broker.get_connection(owner_id, grant.connection_id)
                if connection and connection.get("preset_id") == "deepseek":
                    # 原文抽取显式关思考，避免思考占满回答额度；普通任务沿用原默认值。
                    if grant.api_format == "openai_chat_completions":
                        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
                    elif grant.api_format == "openai_responses":
                        body["reasoning"] = {"effort": "high" if thinking else "none"}
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
            async with asyncio.timeout(call_budget):
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
        except ModelOutputTruncatedError:
            # 已知额度截断由调用方保留为未完成，不冒充连接失效，由任务层决定是否进行已授权的限次扩额重试。
            raise
        except Exception:
            # 旧节点可能捕获模型异常并降级；冻结连接失败后必须阻止后续采集或产出。
            failure.append("所选模型调用失败或结果未知，任务已停止，未切换模型或自动重发")
            raise ValueError(failure[0]) from None
        finally:
            if grant is not None:
                broker.revoke_grant(grant.grant_id, "conductor_call_finished")

    token = _bound_chat.set(generate)
    identity_token = _bound_chat_identity.set((owner_id, connection_id, connection_version, model, task_id, run_id, revision))
    failure_token = _bound_chat_failure.set(failure)
    try:
        yield
    finally:
        _bound_chat.reset(token)
        _bound_chat_identity.reset(identity_token)
        _bound_chat_failure.reset(failure_token)
