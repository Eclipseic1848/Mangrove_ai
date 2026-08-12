# -*- coding: utf-8 -*-
"""图片文档解析器：复用 PaddleOCR-VL 完整 Pipeline，不自研 OCR。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.document_evidence import stable_element_id
from src.data_prep.document_models import BoundingBox, DocumentElement, ElementType
from src.data_prep.models import RawArtifact, RecordEnvelope
from src.services.document_parser_contracts import DocumentParserClient
from src.services.document_parser_factory import configured_document_parser_clients

from .registry import Parser


class ImageParser(Parser):
    """把带确定性坐标的 OCR 块转换为统一 DocumentElement。"""

    name = "image"
    media_types = ("image/png", "image/jpeg", "image/webp")
    extensions = ("png", "jpg", "jpeg", "webp")

    def __init__(
        self,
        *,
        document_clients: Optional[Sequence[DocumentParserClient]] = None,
        artifact_store: Optional[ArtifactStore] = None,
    ) -> None:
        clients = (
            list(document_clients)
            if document_clients is not None
            else configured_document_parser_clients()
        )
        self.document_clients = [
            client for client in clients if hasattr(client, "parse_image")
        ]
        self.artifact_store = artifact_store or ArtifactStore()

    def parse(
        self,
        artifact: RawArtifact,
        raw_bytes: bytes,
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        errors: list[str] = []
        for client in self.document_clients:
            provider = getattr(client, "provider", "document_parser")
            try:
                health = client.health()
                if health.status.lower() != "healthy":
                    raise RuntimeError(f"健康状态异常: {health.status}")
                result = client.parse_image(
                    raw_bytes,
                    filename=Path(artifact.uri).name or "image.png",
                )
                raw_ref = self.artifact_store.write_json_if_absent(
                    artifact.task_id,
                    (
                        f"third_party/{provider}/"
                        f"{artifact.artifact_id}-{artifact.sha256[:16]}.json"
                    ),
                    result.raw_response,
                )
                elements = []
                texts = []
                for order, block in enumerate(result.blocks):
                    texts.append(block.text)
                    element_type = (
                        ElementType.TABLE
                        if block.element_type == "table"
                        else ElementType.PARAGRAPH
                    )
                    element = DocumentElement(
                        element_id=stable_element_id(
                            artifact.artifact_id,
                            block.page,
                            element_type,
                            order,
                            block.text,
                        ),
                        artifact_id=artifact.artifact_id,
                        page=block.page,
                        element_type=element_type,
                        text=block.text,
                        bbox=BoundingBox(
                            x0=block.bbox[0],
                            y0=block.bbox[1],
                            x1=block.bbox[2],
                            y1=block.bbox[3],
                            coordinate_space=block.coordinate_space,
                        ),
                        reading_order=order,
                        extractor=provider,
                        extractor_version=result.version,
                        confidence=block.confidence,
                        raw_result_ref=str(raw_ref).replace("\\", "/"),
                    )
                    elements.append(element.model_dump(mode="json"))
                if not elements:
                    errors.append(f"{provider}: 未返回带坐标的内容块")
                    continue
                data = {"text": "\n".join(texts), "elements": elements}
                content_hash = hashlib.sha256(
                    json.dumps(
                        data,
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                return [
                    RecordEnvelope(
                        record_id=content_hash[:16],
                        data=data,
                        meta={
                            "source_id": artifact.source_id,
                            "artifact_id": artifact.artifact_id,
                            "parser": "image",
                            "content_hash": content_hash,
                            "route": {
                                "actual_backend": f"{provider}:{result.backend}",
                                "actual_version": result.version,
                            },
                        },
                    )
                ], []
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider}: {exc}")
        return [], [{
            "artifact_id": artifact.artifact_id,
            "reason": "image_ocr_required",
            "errors": errors or ["未配置支持图片的完整文档解析服务"],
        }]
