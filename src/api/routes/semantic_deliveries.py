# -*- coding: utf-8 -*-
"""Phase 4B 批次 6：用户隔离的正式交付查询与下载。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.api.auth import get_current_user, get_store


router = APIRouter(
    prefix="/api/semantic-deliveries",
    tags=["semantic-deliveries"],
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.get("/{delivery_id}")
def get_delivery(
    delivery_id: str,
    user=Depends(get_current_user),
):
    delivery = get_store().get_semantic_delivery(
        user["user_id"], delivery_id
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="交付记录不存在")
    return delivery


@router.get("/runs/{run_id}/latest")
def latest_delivery(
    run_id: str,
    user=Depends(get_current_user),
):
    delivery = get_store().latest_semantic_delivery(
        user["user_id"], run_id
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="该 run 尚无正式交付")
    return delivery


@router.get("/outputs/{output_id}")
def download_output(
    output_id: str,
    user=Depends(get_current_user),
):
    output = get_store().get_semantic_delivery_output(
        user["user_id"], output_id
    )
    if output is None:
        raise HTTPException(status_code=404, detail="交付文件不存在")
    path = Path(output["file_path"])
    if (
        not path.is_file()
        or path.stat().st_size != output["size_bytes"]
        or _sha256(path) != output["sha256"]
    ):
        raise HTTPException(
            status_code=409,
            detail="交付文件缺失或完整性校验失败",
        )
    return FileResponse(
        path,
        media_type=output["media_type"],
        filename=output["filename"],
    )
