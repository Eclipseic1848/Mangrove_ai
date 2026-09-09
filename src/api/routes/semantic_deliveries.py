# -*- coding: utf-8 -*-
"""Phase 4B 批次 6：用户隔离的正式交付查询与下载。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.api.auth import get_current_user, get_store
from src.source_acquisition.reuse import guarded_response, verified_output


router = APIRouter(
    prefix="/api/semantic-deliveries",
    tags=["semantic-deliveries"],
)


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
@guarded_response("export",lambda values:[{"kind":"delivery_output","output_id":values["output_id"]}])
def download_output(
    output_id: str,
    user=Depends(get_current_user),
):
    try:
        output,path=verified_output(user["user_id"],output_id)
    except PermissionError as exc:
        raise HTTPException(404,"交付文件不存在") from exc
    except (ValueError,OSError) as exc:
        raise HTTPException(409,"交付文件缺失或完整性校验失败") from exc
    return FileResponse(
        path,
        media_type=output["media_type"],
        filename=output["filename"],
    )
