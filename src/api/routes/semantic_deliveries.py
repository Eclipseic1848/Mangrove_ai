# -*- coding: utf-8 -*-
"""Phase 4B 批次 6：用户隔离的正式交付查询与下载。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from src.api.auth import get_current_user, get_store
from src.api.execution import execution_to_thread
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


@router.get("/outputs/{output_id}/office-preview")
@guarded_response("preview", lambda values: [{"kind": "delivery_output", "output_id": values["output_id"]}], joined_reader=True)
async def preview_office_output(output_id: str, user=Depends(get_current_user)):
    from src.services.office_preview import office_preview

    try:
        output, path = verified_output(user["user_id"], output_id)
    except PermissionError as exc:
        raise HTTPException(404, "交付文件不存在") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "交付文件缺失或完整性校验失败") from exc
    extension = Path(output["filename"]).suffix.lower()
    if extension not in {".doc", ".docx", ".ppt", ".pptx"}:
        raise HTTPException(415, "此接口仅支持 Word 和 PPT 结果")
    try:
        # 复用只读、断网转换；等待线程收口，避免取消后提前释放原件读取保护。
        content = await execution_to_thread(office_preview, path, extension)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Office 预览服务暂不可用，请重试或下载文件") from exc
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": "inline", "Cache-Control": "no-store"})
