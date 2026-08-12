# -*- coding: utf-8 -*-
"""只对任务 Runtime 开放的文档能力 Relay Adapter。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.agentic_runtime.document_tools import (
    DocumentToolBroker,
    DocumentToolClaims,
    DocumentToolError,
    get_default_document_tool_broker as _default_broker,
)


router = APIRouter()
_MAX_REQUEST_BYTES = 1024 * 1024


def get_document_tool_broker() -> DocumentToolBroker:
    try:
        return _default_broker()
    except DocumentToolError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="文档能力工具暂不可用",
        ) from exc


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少文档工具 Grant",
        )
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少文档工具 Grant",
        )
    return token


async def _payload(request: Request) -> dict[str, object]:
    raw_length = request.headers.get("content-length", "").strip()
    if raw_length.isdigit() and int(raw_length) > _MAX_REQUEST_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="文档工具请求体超过限制",
        )
    body = await request.body()
    if len(body) > _MAX_REQUEST_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="文档工具请求体超过限制",
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="文档工具请求体必须是 UTF-8 JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="文档工具请求体必须是 JSON 对象",
        )
    return payload


@router.post(
    "/internal/document-tools/{operation}",
    include_in_schema=False,
)
async def relay_document_tool(
    operation: str,
    request: Request,
    broker: DocumentToolBroker = Depends(get_document_tool_broker),
) -> dict[str, object]:
    try:
        claims = DocumentToolClaims(
            grant_id=request.headers.get("x-mangrove-grant-id", ""),
            owner_binding=request.headers.get(
                "x-mangrove-owner-binding", ""
            ),
            task_id=request.headers.get("x-mangrove-task-id", ""),
            revision=int(request.headers.get("x-mangrove-revision", "0")),
            run_id=request.headers.get("x-mangrove-run-id", ""),
            purpose=request.headers.get("x-mangrove-purpose", ""),
        )
        return await broker.call(
            grant_token=_bearer_token(request),
            operation=operation,
            payload=await _payload(request),
            claims=claims,
        )
    except (DocumentToolError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
