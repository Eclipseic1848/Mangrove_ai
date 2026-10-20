# -*- coding: utf-8 -*-
"""图片文档解析器：复用 PaddleOCR-VL 完整 Pipeline，不自研 OCR。"""
from __future__ import annotations

import hashlib
import io
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from filelock import FileLock
from PIL import Image

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.document_evidence import stable_element_id
from src.data_prep.document_models import BoundingBox, DocumentElement, ElementType
from src.data_prep.models import RawArtifact, RecordEnvelope
from src.services.document_parser_contracts import DocumentParserClient, DocumentParseResult
from src.services.document_parser_factory import configured_document_parser_clients
from src.services.upload_store import inspect_uploaded_image

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

    def _load_image_result(
        self, client: DocumentParserClient, artifact: RawArtifact, raw_bytes: bytes,
    ) -> Tuple[DocumentParseResult, str]:
        provider = getattr(client, "provider", "document_parser")
        health = client.health()
        if health.status.lower() != "healthy":
            raise RuntimeError(f"健康状态异常: {health.status}")
        identity = "|".join((artifact.sha256, provider, client.base_url, client.backend,
                             health.version, "image-ocr-v2"))
        cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        safe_provider = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in provider)
        relative = f"third_party/{safe_provider}/{artifact.artifact_id}-{cache_key}.json"
        lock_path = self.artifact_store.resolve_path(f"{artifact.task_id}/{relative}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # 同任务同版本只认一份冻结响应；并发占用时失败关闭，不能重复识别后配上旧原文。
        with FileLock(str(lock_path), timeout=0):
            raw_response = self.artifact_store.read_json_if_exists(artifact.task_id, relative)
            if raw_response is None:
                # CDN 地址可无后缀，历史 MIME 可泛化；按已验证原件格式命名，不改 URI 和字节。
                with Image.open(io.BytesIO(raw_bytes), formats=("PNG", "JPEG", "WEBP")) as image:
                    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.format]
                result = client.parse_image(raw_bytes, filename=f"{artifact.artifact_id}.{extension}")
                raw_ref = self.artifact_store.write_json_if_absent(artifact.task_id, relative, result.raw_response)
            else:
                result = client.parse_response(raw_response)
                raw_ref = f"{artifact.task_id}/{relative}"
        return result, raw_ref

    def parse(
        self,
        artifact: RawArtifact,
        raw_bytes: bytes,
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        try:
            if hashlib.sha256(raw_bytes).hexdigest() != artifact.sha256:
                raise ValueError("图片原件哈希不一致")
            dimensions = inspect_uploaded_image(raw_bytes)
        except ValueError:
            return [], [{"artifact_id": artifact.artifact_id, "reason": "invalid_image"}]
        errors: list[str] = []
        for client in self.document_clients:
            provider = getattr(client, "provider", "document_parser")
            try:
                result, raw_ref = self._load_image_result(client, artifact, raw_bytes)
                source_coordinates = dimensions["image_orientation"] == 1 and result.source_coordinates_verified
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
                        ) if source_coordinates else None,
                        reading_order=order,
                        extractor=provider,
                        extractor_version=result.version,
                        confidence=block.confidence,
                        # 服务自动旋转与原件 EXIF 的映射未获证明时，只保留页级证据。
                        review_required=not block.confidence_known or not source_coordinates,
                        metadata={"confidence_known": block.confidence_known,
                                  "source_orientation": dimensions["image_orientation"],
                                  "coordinate_mapping": "source" if source_coordinates else "unverified"},
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
