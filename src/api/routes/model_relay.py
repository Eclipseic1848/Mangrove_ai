"""只对任务 Runtime 开放的模型协议 Relay Adapter。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from src.model_connections import (
    ConnectionBroker,
    ConnectionError,
    GrantError,
)

from .model_connections import get_connection_broker


router = APIRouter()
_MAX_RELAY_REQUEST_BYTES = 16 * 1024 * 1024


def _grant_token(request: Request) -> str:
    candidates: list[str] = []
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        candidates.append(authorization[7:].strip())
    for name in ("x-api-key", "x-goog-api-key"):
        value = request.headers.get(name, "").strip()
        if value:
            candidates.append(value)
    unique = {item for item in candidates if item}
    if len(unique) != 1:
        # 多个不一致的鉴权值可能是混淆攻击，不能猜测哪一个有效。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少唯一有效的模型 Relay Grant",
        )
    return unique.pop()


async def _bounded_request_body(request: Request) -> bytes:
    """限制不可信 Runtime 的请求体，避免 Relay 进程被大包耗尽内存。"""

    content_length = request.headers.get("content-length", "").strip()
    if content_length.isdigit() and int(content_length) > _MAX_RELAY_REQUEST_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="模型 Relay 请求体超过限制",
        )
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_RELAY_REQUEST_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="模型 Relay 请求体超过限制",
            )
        body.extend(chunk)
    return bytes(body)


@router.api_route(
    "/internal/model-relay/{protocol_path:path}",
    methods=["POST"],
    include_in_schema=False,
)
async def relay_model_request(
    protocol_path: str,
    request: Request,
    broker: ConnectionBroker = Depends(get_connection_broker),
):
    """鉴权位置由 Adapter 归一化，协议和连接权限仍由 Broker 决定。"""

    token = _grant_token(request)
    try:
        relayed = await broker.relay(
            grant_token=token,
            protocol_path=protocol_path,
            method=request.method,
            headers=request.headers,
            body=await _bounded_request_body(request),
        )
    except GrantError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="上游模型服务暂时不可用",
        ) from exc
    return StreamingResponse(
        relayed.iter_bytes(),
        status_code=relayed.status_code,
        headers=relayed.headers,
        background=BackgroundTask(relayed.aclose),
    )
