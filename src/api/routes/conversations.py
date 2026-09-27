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
    messages = get_store().list_messages(conv_id)
    assistants = [message for message in messages if message["role"] == "assistant"]
    from src.api.session_store import pending_store
    for message in assistants:
        # 消息身份只用于定位；能否操作每次按当前 Owner 的服务端暂存核对。
        message["meta"] = {**(message.get("meta") or {}), "template_available": bool(
            message.get("task_id") and pending_store.has_action(user["user_id"], message["task_id"], "template")
        )}
    missing = [message for message in assistants if not (message.get("meta") or {}).get("token_usage")]
    if missing:
        from src.model_connections.storage import ModelConnectionRepository
        usage = ModelConnectionRepository(get_store().db_path).list_usage(user["user_id"], task_id=conv_id, revision=1, include_identity=True)
        run_ids = {item["run_id"] for item in usage}
        linked_runs = {(message.get("meta") or {}).get("chat_run_id") for message in assistants}
        legacy = [message for message in missing if not (message.get("meta") or {}).get("chat_run_id") and (message.get("meta") or {}).get("kind") != "chat"]
        unlinked_runs = run_ids - linked_runs
        for message in missing:
            meta = message.get("meta") or {}
            # 新记录按运行身份对账；排除已绑定的新回复后，旧数据只允许单回复、单运行关联。
            run_id = meta.get("chat_run_id")
            if not run_id and message in legacy and len(legacy) == 1 and len(unlinked_runs) == 1:
                run_id = next(iter(unlinked_runs))
            rows = [item for item in usage if run_id and item["run_id"] == run_id]
            if not rows:
                continue
            totals = {"calls": sum(item["request_count"] for item in rows), "scope": "execution_only"}
            missing_fields = []
            for source, target in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
                totals[target] = sum(item[source] for item in rows if item[source] is not None)
                if any(item[source] is None for item in rows):
                    missing_fields.append(target)
            if missing_fields:
                totals.update(incomplete=True, missing_fields=missing_fields)
            # 只投影已有账本，不改历史正文或原始计费记录；不含旧版会话外的需求识别调用。
            message["meta"] = {**meta, "token_usage": totals}
    return messages


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
