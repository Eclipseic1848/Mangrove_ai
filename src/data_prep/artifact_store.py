"""不可变制品存储 + 任务产物目录管理（plan.md 第 6.2/6.4 节，ADR-0002）。

职责：
- 任务目录隔离：downloads/<task_id>/{raw,parsed,clean,rejects,lineage}
- 原始制品不可变：落盘后只读，计算 sha256，登记 RawArtifact 元数据
- 批次文件：parsed/clean/rejects/lineage 用 JSONL 分批，state 只存路径引用
- Manifest/Quality/Schema/Trace/Recipe：JSON 落盘
- 凭证脱敏：写入前剔除 Cookie/Token/密码/完整授权头（由调用方经 SourceSpec.to_public_dict）

所有文件 UTF-8 显式编码（ADR-0006）。大数据不进 state，只进这里。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from src.config.settings import settings

from .batches import BatchReference
from .models import (
    DatasetManifest,
    ManifestArtifactEntry,
    ManifestOutputEntry,
    QualityReport,
    RawArtifact,
    RecordEnvelope,
    Recipe,
)

logger = logging.getLogger(__name__)

# 任务产物根目录（与旧 output.py 的 downloads/ 同根，但子结构按 plan 6.4）
_DEFAULT_ROOT = "downloads"

# 凭证相关键名：写入 Manifest/request_snapshot 前必删
_SENSITIVE_KEYS = {
    "cookie", "cookies", "authorization", "auth", "token", "api_key",
    "apikey", "password", "passwd", "secret", "set-cookie",
}


def _scrub(obj: Any) -> Any:
    """递归剔除敏感键（凭证脱敏，ADR-0002/0004）。大小写不敏感。"""
    if isinstance(obj, dict):
        return {
            k: ("····" if k.lower() in _SENSITIVE_KEYS else _scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class ArtifactStore:
    """任务产物存储。每个 task_id 一个独立目录，互不踩踏。"""

    def __init__(self, root: Optional[str] = None) -> None:
        # 优先用 settings.file_resource_base_url 同级的 downloads 根；默认 downloads/
        # 统一保存为绝对路径，避免 Windows 下 resolve_path() 返回绝对路径后，
        # 再相对于字符串形式的相对根目录计算路径而触发 ValueError。
        self.root = Path(root or _DEFAULT_ROOT).resolve()

    # ------------------------------------------------------------------
    # 目录管理
    # ------------------------------------------------------------------
    def task_dir(self, task_id: str) -> Path:
        d = self.root / task_id
        return d

    def _ensure(self, task_id: str, sub: str) -> Path:
        d = self.task_dir(task_id) / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def resolve_path(self, rel_path: str) -> Path:
        """解析制品相对路径，并拒绝逃离存储根目录。"""
        root = self.root.resolve()
        path = (self.root / rel_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("路径必须位于任务产物目录内") from exc
        return path

    # ------------------------------------------------------------------
    # 原始制品（不可变）
    # ------------------------------------------------------------------
    def write_raw(
        self,
        task_id: str,
        source_id: str,
        data: bytes,
        *,
        uri: str,
        media_type: str = "text/html",
        request_snapshot: Optional[Dict[str, Any]] = None,
        response_metadata: Optional[Dict[str, Any]] = None,
        parent_artifact_id: Optional[str] = None,
        ext: Optional[str] = None,
    ) -> RawArtifact:
        """落盘一个不可变原始制品，返回登记元数据。同内容 sha256 一致（幂等性基础）。"""
        sha = _sha256_bytes(data)
        # artifact_id 用 sha256 前 16 位 + 短 uuid 防同哈希不同来源碰撞
        artifact_id = f"raw-{sha[:16]}"
        ext = ext or media_type.split("/")[-1].split(";")[0] or "bin"
        raw_dir = self._ensure(task_id, "raw")
        path = raw_dir / f"{artifact_id}.{ext}"
        # 不可变：已存在则不覆盖（同内容幂等）
        if not path.exists():
            path.write_bytes(data)
        artifact = RawArtifact(
            artifact_id=artifact_id,
            source_id=source_id,
            task_id=task_id,
            uri=uri,
            media_type=media_type,
            size_bytes=len(data),
            sha256=sha,
            created_at=datetime.utcnow(),
            fetched_at=datetime.utcnow(),
            request_snapshot=_scrub(request_snapshot or {}),
            response_metadata=_scrub(response_metadata or {}),
            parent_artifact_id=parent_artifact_id,
            storage_path=str(path.relative_to(self.root)).replace("\\", "/"),
        )
        return artifact

    def read_raw_bytes(self, task_id: str, storage_path: str) -> bytes:
        """按相对路径读取原始制品字节（不可变，仅供解析器读）。"""
        return self.resolve_path(storage_path).read_bytes()

    # ------------------------------------------------------------------
    # 批次 JSONL（parsed/clean/rejects/lineage）
    # ------------------------------------------------------------------
    def write_jsonl(
        self, task_id: str, sub: str, filename: str, records: Iterable[Dict[str, Any]]
    ) -> str:
        """写一批 JSONL 记录。返回相对路径字符串（存入 state/Manifest 作引用）。"""
        d = self._ensure(task_id, sub)
        path = d / filename
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("x", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str))
                    f.write("\n")
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return str(path.relative_to(self.root)).replace("\\", "/")

    def append_jsonl_batch(
        self,
        task_id: str,
        dataset: str,
        rows: Iterable[Dict[str, Any]],
        part_no: int,
    ) -> BatchReference:
        """以独占方式写入一个 JSONL 批次，返回不可变引用。"""
        path = self._ensure(task_id, dataset) / f"part-{part_no:05d}.jsonl"
        digest = hashlib.sha256()
        record_count = 0
        byte_count = 0
        with path.open("xb") as fh:
            for row in rows:
                line = (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                fh.write(line)
                digest.update(line)
                record_count += 1
                byte_count += len(line)
        rel_path = str(path.relative_to(self.root)).replace("\\", "/")
        return BatchReference(
            batch_id=f"{dataset}-{part_no:05d}-{digest.hexdigest()[:16]}",
            dataset=dataset,
            part_no=part_no,
            path=rel_path,
            record_count=record_count,
            byte_count=byte_count,
            sha256=digest.hexdigest(),
        )

    def iter_jsonl(self, rel_path: str) -> Iterator[Dict[str, Any]]:
        """逐行读取 JSONL，避免为大数据集构造完整列表。"""
        path = self.resolve_path(rel_path)
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("JSONL 每行必须是 JSON 对象")
                    yield value

    def write_rejects(self, task_id: str, kind: str, records: List[Dict[str, Any]]) -> str:
        """kind: parse | clean。"""
        return self.write_jsonl(task_id, "rejects", f"{kind}_rejects.jsonl", records)

    def write_lineage(self, task_id: str, records: List[Dict[str, Any]]) -> str:
        return self.write_jsonl(task_id, "lineage", "records.jsonl", records)

    def append_jsonl(self, task_id: str, rel_within_task: str, records: Iterable[Dict[str, Any]]) -> str:
        """追加 JSONL 到任务目录内文件（逐批写 lineage，不全量驻留内存，plan Phase 2 Task 12）。

        与 write_jsonl（覆盖）区别：用 ``a`` 模式追加，适合 clean_node 逐批写 lineage。
        """
        path = self.task_dir(task_id) / rel_within_task
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str))
                f.write("\n")
        return str(path.relative_to(self.root)).replace("\\", "/")

    def read_jsonl(self, task_id: str, rel_path: str) -> List[Dict[str, Any]]:
        """读回 JSONL（测试/复跑用）。

        rel_path 支持两种形式：从 root 起的完整路径（含 task_id 前缀），
        或任务目录内相对路径（不含 task_id 前缀）。后者自动拼 task_dir。
        两种形式均经 resolve_path 校验，禁止逃离存储根目录。
        """
        candidates = [rel_path, f"{task_id}/{rel_path}"]
        for candidate in candidates:
            path = self.resolve_path(candidate)
            if path.exists():
                return list(self.iter_jsonl(candidate))
        return []

    # ------------------------------------------------------------------
    # JSON 单文件（manifest/quality/schema/trace/recipe）
    # ------------------------------------------------------------------
    def write_json(self, task_id: str, filename: str, obj: Any) -> Path:
        path = self.task_dir(task_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return path.relative_to(self.root)

    def read_json_if_exists(
        self, task_id: str, rel_within_task: str
    ) -> Optional[Dict[str, Any]]:
        """读取任务目录内的 JSON 对象；不存在时返回 None。"""
        rel_path = f"{task_id}/{rel_within_task}"
        path = self.resolve_path(rel_path)
        task_root = self.task_dir(task_id).resolve()
        try:
            path.relative_to(task_root)
        except ValueError as exc:
            raise ValueError("JSON 制品必须位于当前任务目录内") from exc
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON 制品必须是对象")
        return value

    def write_json_if_absent(
        self, task_id: str, rel_within_task: str, obj: Any
    ) -> str:
        """独占创建不可变 JSON 制品；已存在则保持原文件不变。"""
        rel_path = f"{task_id}/{rel_within_task}"
        path = self.resolve_path(rel_path)
        task_root = self.task_dir(task_id).resolve()
        try:
            path.relative_to(task_root)
        except ValueError as exc:
            raise ValueError("JSON 制品必须位于当前任务目录内") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        try:
            with path.open("xb") as fh:
                fh.write(payload)
        except FileExistsError:
            pass
        return str(path.relative_to(self.root)).replace("\\", "/")

    def write_quality(self, task_id: str, report: QualityReport) -> Path:
        return self.write_json(task_id, "quality_report.json", report.model_dump(mode="json"))

    def write_schema(self, task_id: str, schema: Dict[str, Any]) -> Path:
        return self.write_json(task_id, "schema.json", schema)

    def write_trace(self, task_id: str, trace: List[Dict[str, Any]]) -> Path:
        return self.write_json(task_id, "trace.json", trace)

    def write_recipe(self, task_id: str, recipe: Recipe) -> Path:
        # recipe.yaml 是人可读；同时写等价 JSON（plan 6.5.3）
        path = self.write_json(task_id, "recipe.json", recipe.model_dump(mode="json"))
        return path

    # ------------------------------------------------------------------
    # Manifest 生成
    # ------------------------------------------------------------------
    def write_manifest(
        self,
        task_id: str,
        *,
        artifacts: List[RawArtifact],
        outputs: List[ManifestOutputEntry],
        record_counts: Dict[str, int],
        recipe_version: Optional[str] = None,
        schema_ref: Optional[str] = None,
        quality_ref: Optional[str] = None,
        lineage_ref: Optional[str] = None,
    ) -> Path:
        """生成 manifest.json（唯一入口，不含凭证）。"""
        import sys as _sys
        manifest = DatasetManifest(
            task_id=task_id,
            spec_version="2",
            recipe_version=recipe_version,
            artifacts=[
                ManifestArtifactEntry(
                    artifact_id=a.artifact_id,
                    kind="raw",
                    path=a.storage_path,
                    sha256=a.sha256,
                    size_bytes=a.size_bytes,
                )
                for a in artifacts
            ],
            outputs=outputs,
            record_counts=record_counts,
            schema_ref=schema_ref,
            quality_ref=quality_ref,
            lineage_ref=lineage_ref,
            environment={
                "python": f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}",
                "engine": "data_prep_v1",
                "created_at": _now_iso(),
            },
        )
        return self.write_json(task_id, "manifest.json", manifest.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def sha256_text(self, text: str) -> str:
        """计算文本 sha256（记录 content_hash 用）。"""
        return _sha256_text(text)

    def file_sha256(self, rel_path: str) -> str:
        p = self.root / rel_path
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
