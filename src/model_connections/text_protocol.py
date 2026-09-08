"""共享的受控模型文本协议编解码，不签发权限或发起网络请求。"""
from __future__ import annotations

import json


def structured_request(
    *,
    api_format: str,
    model: str,
    grant_token: str,
    system_prompt: str,
    payload: dict[str, object],
) -> tuple[str, dict[str, object], dict[str, str]]:
    user_text = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if api_format == "openai_chat_completions":
        return (
            "chat/completions",
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "temperature": 0,
                "max_tokens": 2000,
                "stream": False,
            },
            {"authorization": f"Bearer {grant_token}"},
        )
    if api_format == "openai_responses":
        return (
            "responses",
            {
                "model": model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "max_output_tokens": 2000,
                "store": False,
                "stream": False,
            },
            {"authorization": f"Bearer {grant_token}"},
        )
    if api_format == "anthropic_messages":
        return (
            "v1/messages",
            {
                "model": model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_text}],
                "temperature": 0,
                "max_tokens": 2000,
                "stream": False,
            },
            {"x-api-key": grant_token},
        )
    if api_format == "gemini_generate_content":
        return (
            f"models/{model}:generateContent",
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": f"{system_prompt}\n{user_text}"}
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0,
                    "maxOutputTokens": 2000,
                    "responseMimeType": "application/json",
                },
            },
            {"x-goog-api-key": grant_token},
        )
    raise ValueError("该 Provider 协议不支持独立语义验证")


def response_text(
    api_format: str,
    body: bytes,
) -> str:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("外部语义验证返回的不是有效 JSON") from exc
    if api_format == "openai_chat_completions":
        return str(payload["choices"][0]["message"]["content"])
    if api_format == "openai_responses":
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if isinstance(content.get("text"), str):
                    return content["text"]
    elif api_format == "anthropic_messages":
        for content in payload.get("content", []):
            if content.get("type") == "text":
                return str(content.get("text") or "")
    elif api_format == "gemini_generate_content":
        return str(
            payload["candidates"][0]["content"]["parts"][0]["text"]
        )
    raise ValueError("外部语义验证响应缺少可解析的结构化结论")
