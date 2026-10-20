"""
产出文件下载路由：按 downloads/<task_id>/<相对路径> 从磁盘解析，并校验任务归属。

不依赖内存登记表--只要文件还在 downloads/ 下，重载会话或重启服务后均可下载。
归属校验：该 task_id 必须出现在当前用户某会话的消息里（store.user_owns_task）。

支持子目录（data_prep v2 产物结构：clean/data.jsonl、lineage/records.jsonl、raw/<id>.json 等）。
安全：①归属校验确保只能访问自己 task_id 的目录；②路径 resolve 后必须在 task_dir 内，
杜绝对 `../` 等路径穿越（替代旧白名单，因 data_prep 产物多级多格式且 raw 文件名动态）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from src.data_prep.artifact_store import ArtifactStore

from ..auth import get_current_user, get_store

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.get("/{task_id}/{file_path:path}")
def download(task_id: str, file_path: str, user=Depends(get_current_user)):
    """下载任务产物。file_path 支持子目录（如 clean/data.jsonl、raw/<id>.json）。"""
    # 1. 归属校验：task_id 必须属于当前用户
    store = get_store()
    if not store.user_owns_task(user["user_id"], task_id):
        raise HTTPException(status_code=404, detail="文件不存在或无权访问")
    # 先归一化为任务内相对路径，避免 ./、绝对路径或软链绕过质量门。
    task_dir = ArtifactStore().task_dir(task_id).resolve()
    target = (task_dir / file_path).resolve()
    try:
        relative_path = target.relative_to(task_dir).as_posix()
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在")
    task = store.get_data_prep_task(task_id)
    quality = task.get("quality") if task else None
    if (
        isinstance(quality, dict)
        and quality.get("overall") == "fail"
        and relative_path in {
            "extraction/document_extraction.xlsx",
            "extraction/extracted_fields.json",
            "extraction/extracted_fields.jsonl",
            "extraction/extracted_records.jsonl",
            "extraction/extracted_tables.json",
        }
    ):
        raise HTTPException(
            status_code=409,
            detail="质量门未通过，权威结果不可下载",
        )
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已清理")
    return FileResponse(str(target), filename=target.name)


@router.get("/{task_id}")
def preview(
    task_id: str, preview: str = Query(..., min_length=1, max_length=1024),
    sheet: int = Query(0, ge=0), row: int = Query(0, ge=0, le=1048575),
    column: int = Query(0, ge=0, le=16383), user=Depends(get_current_user),
):
    """预览已有产物；不修改原件、不执行任务，也不改变交付资格。"""
    # 下载与预览共用归属、质量门和路径保护，不能靠前端隐藏来隔离数据。
    path = Path(download(task_id, preview, user).path)
    extension = path.suffix.lower()
    if extension == ".xlsx":
        from src.services.workbook_preview import WorkbookPreviewError, workbook_preview
        try:
            return workbook_preview(path, extension, sheet, row, column)
        except WorkbookPreviewError as error:
            raise HTTPException(422, str(error))
        except Exception:
            raise HTTPException(422, "工作簿无法预览，可能已损坏、加密或超过安全限制；可下载原件查看")
    if extension != ".json":
        raise HTTPException(415, "在线预览支持 JSON 和 XLSX；其他文件可下载查看")
    # 有界读取，避免大文件或并发预览占满内存；全文仍保留在原下载文件中。
    with path.open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(422, "JSON 超过 8 MB 在线预览上限，请下载原件查看")
    try:
        text = raw.decode("utf-8-sig")
        def reject_constant(value):
            raise ValueError("JSON 包含非有限数值")
        def finite_float(value):
            number = float(value)
            return number if math.isfinite(number) else reject_constant(value)
        snapshot = json.loads(text, parse_constant=reject_constant, parse_float=finite_float)
    except (UnicodeError, ValueError, RecursionError):
        raise HTTPException(422, "JSON 无法预览，请下载原件检查格式与 UTF-8 编码")
    table = None
    candidate_summary = None
    candidate_counts = None
    if isinstance(snapshot, dict) and snapshot.get("schema_version") in {"bank-benefits-v2", "evidence-collection-v1"}:
        from src.conductor.collection_results import candidate_review_details, collection_candidate_counts, partial_evidence_records
        from src.conductor.progress import source_links
        rows = []
        summaries = []
        total = 0
        candidates = snapshot.get("candidates")
        if not isinstance(candidates, list):
            raise HTTPException(422, "权益快照缺少有效的笔记清单；可下载原件核对")
        candidate_counts = collection_candidate_counts(snapshot)
        for index, note in enumerate(candidates, 1):
            if not isinstance(note, dict):
                continue
            metadata = note.get("metadata") or {}
            note_id = metadata.get("note_id") if isinstance(metadata, dict) else None
            url, title = note.get("url"), note.get("title")
            details = candidate_review_details(note, snapshot.get("scope"))
            links = source_links([{"url": url, "title": title}], str(note.get("status") or "")) if isinstance(url, str) and len(url) <= 2048 else []
            source = {"note_id": note_id if isinstance(note_id, str) and len(note_id) <= 256 else None,
                      "note_url": links[0]["url"] if links else None,
                      "source_title": title[:256] if isinstance(title, str) else None}
            if len(summaries) < 200:
                status = note.get("status") if note.get("status") in {"selected", "excluded", "review_required", "unprocessed"} else "unprocessed"
                summaries.append({**source, "index": index,
                    "status": status,
                    **details})
            if snapshot["schema_version"] != "bank-benefits-v2" or note.get("status") not in {"selected", "review_required"}:
                continue
            assessment = note.get("assessment") or {}
            partial_evidence = note.get("status") == "review_required"
            if partial_evidence:
                benefits = partial_evidence_records(note)
            else:
                benefits = assessment.get("benefits") if isinstance(assessment, dict) else None
            if not isinstance(benefits, list):
                continue
            for benefit in benefits:
                if not isinstance(benefit, dict):
                    continue
                total += 1
                if len(rows) < 200:
                    # 每条原文关联只返回短标识，防止超长笔记标题被数百行重复放大。
                    rows.append({**benefit, **source, "source_status": note["status"],
                                 "review_reason": details["reason"] if partial_evidence else None,
                                 "partial_evidence": partial_evidence, "review_required": True})
        if snapshot["schema_version"] == "bank-benefits-v2":
            table = {"rows": rows, "total_rows": total, "truncated": total > len(rows)}
        candidate_summary = {"rows": summaries, "total_rows": candidate_counts["found"],
                             "truncated": candidate_counts["found"] > len(summaries)}
    return {"kind": "json", "text": text[:16000], "truncated": len(text) > 16000,
            "benefit_table": table, "candidate_summary": candidate_summary, "candidate_counts": candidate_counts,
            "in_progress": isinstance(snapshot, dict) and snapshot.get("in_progress") is True,
            "snapshot_revision": snapshot.get("snapshot_revision") if isinstance(snapshot, dict)
                and type(snapshot.get("snapshot_revision")) is int and snapshot["snapshot_revision"] >= 0 else None,
            "outcome": snapshot.get("outcome") if isinstance(snapshot, dict) else None}
