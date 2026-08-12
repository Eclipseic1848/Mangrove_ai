# -*- coding: utf-8 -*-
"""JSON/JSONL 解析器（Phase 2 Task 5）。

使用标准库 json（成熟库）：
- JSONL：逐行解析，坏行隔离，天然流式
- JSON：小对象/小数组全量解析；超大数组拒绝并建议改用 JSONL（不全量加载）
- HTML/XML/TXT 在后续步骤补齐（BeautifulSoup4 / lxml iterparse）

不引入 ijson 等额外依赖（YAGNI，Phase 2 首版拒绝大 JSON 数组即可）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.data_prep.models import RawArtifact, RecordEnvelope

from .registry import Parser

logger = logging.getLogger(__name__)


class JsonXmlParser(Parser):
    """JSON/JSONL 解析器。用标准库 json，逐行流式，坏行隔离。"""

    name = "json"
    media_types = ("application/json", "application/x-ndjson")
    extensions = ("json", "jsonl")

    def __init__(self, *, max_json_array_bytes: int = 50 * 1024 * 1024) -> None:
        self.max_json_array_bytes = max_json_array_bytes

    # ------------------------------------------------------------------
    # 格式识别
    # ------------------------------------------------------------------
    def _format(self, artifact: RawArtifact) -> str:
        uri = artifact.uri or artifact.storage_path or ""
        ext = uri.rsplit(".", 1)[-1].lower() if "." in uri else "json"
        return "jsonl" if ext == "jsonl" else "json"

    def _make_record(
        self,
        artifact: RawArtifact,
        obj: Dict[str, Any],
        *,
        parser_name: str,
        position: Dict[str, Any],
    ) -> RecordEnvelope:
        content_hash = hashlib.sha256(
            json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        rid = hashlib.sha256(
            f"{artifact.artifact_id}:{parser_name}:{position}".encode("utf-8")
        ).hexdigest()[:16]
        return RecordEnvelope(
            record_id=rid,
            data=dict(obj),
            meta={
                "source_id": artifact.source_id,
                "artifact_id": artifact.artifact_id,
                "parser": parser_name,
                "position": position,
                "content_hash": content_hash,
            },
        )

    # ------------------------------------------------------------------
    # JSONL
    # ------------------------------------------------------------------
    def _iter_jsonl(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Iterator[Tuple[Optional[Dict[str, Any]], Optional[Dict], Optional[str]]]:
        text = raw_bytes.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                yield None, {"position": {"line": line_no}}, "invalid_json"
                continue
            if not isinstance(obj, dict):
                yield None, {"position": {"line": line_no}}, "jsonl_not_object"
                continue
            yield obj, {"position": {"line": line_no}}, None

    # ------------------------------------------------------------------
    # JSON（小文件全量）
    # ------------------------------------------------------------------
    def _parse_json(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        if len(raw_bytes) > self.max_json_array_bytes:
            raise ValueError(
                f"JSON 超过 {self.max_json_array_bytes} 字节上限，请改用 JSONL 格式以支持流式"
            )
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            return [], [{
                "artifact_id": artifact.artifact_id,
                "reason": f"JSON 解析失败: {e}",
                "position": {},
            }]
        if isinstance(payload, list):
            records: List[RecordEnvelope] = []
            for i, item in enumerate(payload):
                if not isinstance(item, dict):
                    continue
                records.append(self._make_record(
                    artifact, item, parser_name="json_array", position={"index": i},
                ))
            return records, []
        if isinstance(payload, dict):
            return [self._make_record(
                artifact, payload, parser_name="json_object", position={"index": 0},
            )], []
        return [], [{
            "artifact_id": artifact.artifact_id,
            "reason": "json_unsupported_type",
            "position": {},
        }]

    # ------------------------------------------------------------------
    # Parser 接口
    # ------------------------------------------------------------------
    def parse(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        if self._format(artifact) == "jsonl":
            return self._parse_jsonl(artifact, raw_bytes)
        return self._parse_json(artifact, raw_bytes)

    def _parse_jsonl(
        self, artifact: RawArtifact, raw_bytes: bytes
    ) -> Tuple[List[RecordEnvelope], List[Dict]]:
        records: List[RecordEnvelope] = []
        rejects: List[Dict] = []
        for obj, position, err in self._iter_jsonl(artifact, raw_bytes):
            if err:
                rejects.append({
                    "artifact_id": artifact.artifact_id,
                    "reason": err,
                    "position": (position or {}).get("position", {}),
                })
                continue
            records.append(self._make_record(
                artifact, obj, parser_name="jsonl",
                position=(position or {}).get("position", {}),
            ))
        return records, rejects

    def parse_stream(
        self,
        artifact: RawArtifact,
        raw_bytes: bytes,
        *,
        batch_size: int = 10_000,
    ) -> Iterator[Tuple[List[RecordEnvelope], List[Dict]]]:
        """逐批产出。JSONL 天然流式；JSON 不支持流式，直接全量返回单批。"""
        if self._format(artifact) != "jsonl":
            records, rejects = self.parse(artifact, raw_bytes)
            yield records, rejects
            return
        batch: List[RecordEnvelope] = []
        rejects_batch: List[Dict] = []
        for obj, position, err in self._iter_jsonl(artifact, raw_bytes):
            if err:
                rejects_batch.append({
                    "artifact_id": artifact.artifact_id,
                    "reason": err,
                    "position": (position or {}).get("position", {}),
                })
                continue
            batch.append(self._make_record(
                artifact, obj, parser_name="jsonl",
                position=(position or {}).get("position", {}),
            ))
            if len(batch) >= batch_size:
                yield batch, rejects_batch
                batch = []
                rejects_batch = []
        if batch or rejects_batch:
            yield batch, rejects_batch
