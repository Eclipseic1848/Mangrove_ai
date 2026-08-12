"""
产出文件下载路由：按 downloads/<task_id>/<相对路径> 从磁盘解析，并校验任务归属。

不依赖内存登记表--只要文件还在 downloads/ 下，重载会话或重启服务后均可下载。
归属校验：该 task_id 必须出现在当前用户某会话的消息里（store.user_owns_task）。

支持子目录（data_prep v2 产物结构：clean/data.jsonl、lineage/records.jsonl、raw/<id>.json 等）。
安全：①归属校验确保只能访问自己 task_id 的目录；②路径 resolve 后必须在 task_dir 内，
杜绝对 `../` 等路径穿越（替代旧白名单，因 data_prep 产物多级多格式且 raw 文件名动态）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
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
    task = store.get_data_prep_task(task_id)
    quality = task.get("quality") if task else None
    if (
        isinstance(quality, dict)
        and quality.get("overall") == "fail"
        and file_path.replace("\\", "/") in {
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
    # 2. 路径穿越防护：resolve 后必须在 task_dir 内
    task_dir = ArtifactStore().task_dir(task_id).resolve()
    target = (task_dir / file_path).resolve()
    try:
        target.relative_to(task_dir)  # 不在 task_dir 内则抛 ValueError
    except ValueError:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已清理")
    return FileResponse(str(target), filename=target.name)
