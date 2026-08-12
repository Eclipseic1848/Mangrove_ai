"""会话路由：列表 / 新建 / 详情(含消息) / 重命名 / 删除。多用户隔离。"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user, get_store
from ..schemas import ConversationOut, MessageOut, RenameIn

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _own_conv_or_404(conv_id: str, user):
    conv = get_store().get_conversation(conv_id)
    if not conv or conv["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.get("", response_model=List[ConversationOut])
def list_conversations(user=Depends(get_current_user)):
    return get_store().list_conversations(user["user_id"])


@router.post("", response_model=ConversationOut)
def create_conversation(user=Depends(get_current_user)):
    return get_store().create_conversation(user["user_id"])


@router.get("/{conv_id}/messages", response_model=List[MessageOut])
def get_messages(conv_id: str, user=Depends(get_current_user)):
    _own_conv_or_404(conv_id, user)
    return get_store().list_messages(conv_id)


@router.patch("/{conv_id}")
def rename_conversation(conv_id: str, body: RenameIn, user=Depends(get_current_user)):
    _own_conv_or_404(conv_id, user)
    get_store().rename_conversation(conv_id, body.title.strip() or "新会话")
    return {"ok": True}


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str, user=Depends(get_current_user)):
    _own_conv_or_404(conv_id, user)
    get_store().delete_conversation(conv_id)
    return {"ok": True}
