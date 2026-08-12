# -*- coding: utf-8 -*-
"""按配置构建文档解析服务主备链。"""
from __future__ import annotations

import logging
from typing import Optional

from src.config.settings import settings
from src.services.document_parser_contracts import DocumentParserClient
from src.services.mineru_document import MinerUDocumentClient
from src.services.paddleocr_vl_document import PaddleOCRVLDocumentClient

logger = logging.getLogger(__name__)


def _normalize_provider(name: str) -> str:
    value = (name or "").strip().lower().replace("-", "_")
    aliases = {
        "paddle": "paddleocr_vl",
        "paddleocr": "paddleocr_vl",
        "paddleocrvl": "paddleocr_vl",
    }
    return aliases.get(value, value)


def build_document_parser_client(name: str) -> Optional[DocumentParserClient]:
    provider = _normalize_provider(name)
    if not provider:
        return None
    if provider == "mineru":
        if not settings.mineru_enabled or not settings.mineru_base_url.strip():
            return None
        return MinerUDocumentClient()
    if provider == "paddleocr_vl":
        if (
            not settings.paddleocr_vl_enabled
            or not settings.paddleocr_vl_base_url.strip()
        ):
            return None
        return PaddleOCRVLDocumentClient()
    logger.warning("忽略未知文档解析服务: %s", name)
    return None


def configured_document_parser_clients() -> list[DocumentParserClient]:
    """返回去重后的首选/备用服务；未部署的服务自动跳过。"""
    names = [settings.document_parser_primary]
    if settings.document_parser_fallback_enabled:
        names.append(settings.document_parser_secondary)
    clients: list[DocumentParserClient] = []
    seen: set[str] = set()
    for name in names:
        provider = _normalize_provider(name)
        if not provider or provider in seen:
            continue
        seen.add(provider)
        client = build_document_parser_client(provider)
        if client is not None:
            clients.append(client)
    return clients
