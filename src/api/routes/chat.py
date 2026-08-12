"""
聊天路由：SSE 流式转发 astream_conductor。

POST /api/chat/stream  (Bearer 鉴权，请求体 ChatIn)
事件序列：
  event: meta    {"conv_id": ...}                       会话句柄（新建时回传）
  event: node    {"node": "collect", "label": "采集数据"} 每个流水线节点完成
  event: result  {结构化最终结果}                          见 _build_result
  event: error   {"message": ...}
  event: done    {}

执行与连接解耦：流水线跑在独立后台 asyncio.Task 里，SSE 生成器只从队列转发事件。
客户端断开（切页面/刷新）不会取消任务——跑完照样把助手回复落库，用户回到会话即可看到；
GET /api/chat/running/{conv_id} 供前端查询会话是否有任务仍在后台执行。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.conductor.context import compress_history
from src.conductor.graph import astream_conductor
from src.llm import list_models
from src.llm.provider import _usage_ctx

from ..auth import get_current_user, get_store
from ..schemas import ChatIn
from ..session_store import pending_store

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 节点 -> 进度展示文案（旧分析图 + data_prep 新图共用 intent/clean/output）
_NODE_LABELS = {
    # 旧分析图
    "intent": "理解意图",
    "planner": "规划任务",
    "router": "选择采集引擎",
    "collect": "采集数据",
    "clean": "清洗数据",
    "analyze": "分析",
    "checker": "质量评估",
    "output": "生成产出",
    # data_prep 新图
    "prep_planner": "规划任务",
    "acquire": "采集数据",
    "parse": "解析数据",
    "profile_before": "数据剖析",
    "validate": "质量校验",
}

# 产出 state 键 -> (下载文件名, mime)
_FILE_MAP = {
    "report_md": ("report.md", "text/markdown"),
    "json": ("data.json", "application/json"),
    "trace_file": ("trace.json", "application/json"),
}


def _resolve_model(provider: Optional[str], model: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """前端未指定时，回落到默认供应商的首个模型。"""
    if provider and model:
        return provider, model
    from src.config.settings import settings
    catalog = list_models()  # {provider: [models]}
    dp = (settings.llm_default_provider or "deepseek").lower()
    if dp in catalog and catalog[dp]:
        return dp, catalog[dp][0]
    for prov, models in catalog.items():
        if models:
            return prov, models[0]
    return provider, model


def _build_result(
    user_id: str, conv_id: str, state: Dict[str, Any], reply: str,
    provider: Optional[str], model: Optional[str], user_input: str,
) -> Dict[str, Any]:
    """把最终 state 整理成前端可直接渲染的结构，并把 HITL 待确认动作暂存到服务端。"""
    task_id = state.get("task_id") or ""
    spec = state.get("task_spec")
    analysis = state.get("analysis")

    # 需要澄清
    if state.get("needs_clarification"):
        return {"conv_id": conv_id, "task_id": task_id, "kind": "clarification",
                "reply": state.get("clarification_question") or "请补充更多细节。"}

    # 出错（且无任何产出）
    if state.get("error") and not state.get("outputs"):
        return {"conv_id": conv_id, "task_id": task_id, "kind": "error",
                "reply": f"❌ {state.get('error')}"}

    # 定时任务：暂存待确认
    if state.get("schedule_request"):
        pending_store.put(user_id, task_id, {"schedule": {
            "schedule": state["schedule_request"], "user_input": user_input,
            "provider": provider, "model": model,
            "intent": getattr(spec, "intent", "") if spec else "",
        }})
        return {"conv_id": conv_id, "task_id": task_id, "kind": "schedule",
                "reply": state.get("reply") or "已识别为定时任务，确认后创建。",
                "schedule": str(state["schedule_request"])}

    # 正常产出
    outputs = state.get("outputs", {}) or {}
    files: List[Dict[str, str]] = []
    for key, (fname, mime) in _FILE_MAP.items():
        if outputs.get(key):
            # 下载按磁盘 downloads/<task_id>/<fname> 解析（重载会话/重启后仍可下载）
            files.append({"name": fname, "url": f"/api/downloads/{task_id}/{fname}", "mime": mime})

    pending: Dict[str, Any] = {}
    actions: List[str] = []
    if outputs.get("db_pending"):
        pending["db"] = {
            "task_id": task_id, "items": state.get("cleaned_dataset", []),
            "source": (getattr(spec, "db_target", "") if spec else "") or "",
        }
        actions.append("db")
    if outputs.get("email_pending"):
        pending["email"] = {
            "to": outputs.get("email_to") or [],
            "subject": f"【数据采集分析报告】{getattr(spec, 'intent', '') if spec else ''}"[:120],
            "body": outputs.get("report_text") or reply,
            "attachments": [p for p in (outputs.get("report_md"), outputs.get("json")) if p],
        }
        actions.append("email")
    if outputs.get("slack_pending"):
        pending["slack"] = {
            "title": f"数据采集分析报告：{getattr(spec, 'intent', '') if spec else ''}",
            "body": analysis or reply,
        }
        actions.append("slack")
    if outputs.get("template_suggest") and analysis:
        pending["template"] = {
            "intent": getattr(spec, "intent", "") if spec else "",
            "data_type": spec.data_type.value if spec else "generic",
            "keywords": list(getattr(spec, "keywords", []) or []) if spec else [],
            "analysis": analysis, "provider": provider, "model": model,
        }
        actions.append("template")
    if pending:
        pending_store.put(user_id, task_id, pending)

    return {
        "conv_id": conv_id, "task_id": task_id, "kind": "output",
        "reply": reply, "analysis": analysis,
        "files": files, "actions": actions,
        "grade": state.get("grade"),
        "template_status": state.get("template_status"),
        "data_type": (spec.data_type.value if spec else None),
        "collector": state.get("collector_used") or state.get("collector"),
        "item_count": len(state.get("cleaned_dataset", []) or []),
    }


# data_prep 产出的数据文件格式 -> mime
_DP_FILE_MIME = {
    "jsonl": "application/x-ndjson", "parquet": "application/octet-stream",
    "csv": "text/csv", "tsv": "text/tab-separated-values",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _build_data_prep_result(
    user_id: str, conv_id: str, state: Dict[str, Any],
    provider: Optional[str], model: Optional[str], user_input: str,
) -> Dict[str, Any]:
    """把 data_prep 最终 state 整理成前端结构（产物为数据文件 + 质量报告，非分析报告）。"""
    task_id = state.get("task_id") or ""

    if state.get("needs_clarification"):
        return {"conv_id": conv_id, "task_id": task_id, "kind": "clarification",
                "reply": state.get("clarification_question") or "请补充更多细节。"}

    if state.get("error"):
        return {"conv_id": conv_id, "task_id": task_id, "kind": "error",
                "reply": f"❌ {state.get('error')}"}

    # 数据文件下载（outputs 在子目录，downloads 路由已支持子目录）
    outputs = state.get("outputs") or []
    files: List[Dict[str, str]] = []
    for o in outputs:
        fmt = str(o.get("format", ""))
        path = str(o.get("path", ""))  # 如 clean/data.jsonl
        fname = path.split("/")[-1] if path else fmt
        files.append({
            "name": fname,
            "url": f"/api/downloads/{task_id}/{path}",
            "mime": _DP_FILE_MIME.get(fmt, "application/octet-stream"),
        })
    # 元数据文件（根目录，便于前端直接取 manifest/质量/schema）
    for extra in ("manifest.json", "quality_report.json", "schema.json"):
        files.append({"name": extra, "url": f"/api/downloads/{task_id}/{extra}", "mime": "application/json"})

    # 质量摘要作为回复正文
    quality = state.get("quality")
    if quality is not None:
        try:
            from src.quality.report import to_human_summary
            reply = to_human_summary(quality)
        except Exception:
            reply = "数据准备完成。"
    else:
        reply = "数据准备完成。"

    counts = state.get("record_counts") or {}
    quality_dict = quality.model_dump(mode="json") if quality is not None else None

    return {
        "conv_id": conv_id, "task_id": task_id, "kind": "output",
        "reply": reply, "files": files, "actions": [],
        "data_type": "data_prep",
        "item_count": counts.get("clean", 0),
        "quality": quality_dict,
        "record_counts": counts,
        "manifest_path": state.get("manifest_path"),
    }


@router.post("/stream")
async def chat_stream(body: ChatIn, user=Depends(get_current_user)):
    store = get_store()
    user_id = user["user_id"]

    # 解析/新建会话（校验归属）
    conv_id = body.conv_id
    if conv_id:
        conv = store.get_conversation(conv_id)
        if not conv or conv["user_id"] != user_id:
            conv_id = None
    if not conv_id:
        title = body.content.strip()[:24] or "新会话"
        conv = store.create_conversation(user_id, title)
        conv_id = conv["conv_id"]

    provider, model = _resolve_model(body.provider, body.model)

    # 载入历史 + 追加本轮用户消息（持久化）
    history = [{"role": m["role"], "content": m["content"]} for m in store.list_messages(conv_id)]
    history.append({"role": "user", "content": body.content})
    store.add_message(conv_id, "user", body.content)

    queue: asyncio.Queue = asyncio.Queue()

    async def pipeline():
        """真正跑流水线的后台任务：事件写队列；结果无论 SSE 连接是否还在都会落库。"""
        _usage_tok = None
        try:
            # 按用户隔离的凭证覆盖（自配 API Key/Cookie）注入本任务上下文，流水线内 effective() 取用
            from src.config.runtime_config import USER_KEYS
            from src.config.user_ctx import set_user_memories, set_user_overrides
            set_user_overrides({k: v for k, v in (store.config_all(user_id) or {}).items() if k in USER_KEYS})
            set_user_memories([m["text"] for m in store.memory_list(user_id)])

            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
            _usage_tok = _usage_ctx.set(usage)
            hist = await compress_history(history, provider=provider, model=model)
            # 模式选择：body.mode 优先，否则按全局开关（6B 默认 data_prep，可回退 legacy_analysis）
            from src.config.settings import settings as _settings
            mode = body.mode or ("data_prep" if _settings.data_prep_mode_enabled else "legacy_analysis")

            state: Dict[str, Any] = {}
            seen: set[str] = set()
            if mode == "data_prep":
                from src.data_prep.graph import astream_data_prep
                stream = astream_data_prep(
                    user_input=body.content, messages=hist,
                    provider=provider, model=model, session_id=conv_id,
                )
            else:
                stream = astream_conductor(
                    user_input=body.content, messages=hist,
                    provider=provider, model=model, session_id=conv_id,
                )
            async for kind, payload in stream:
                if kind == "node":
                    node_name = payload.get("node") if isinstance(payload, dict) else payload
                    view = payload.get("view") if isinstance(payload, dict) else None
                    label = _NODE_LABELS.get(node_name)
                    if label and node_name not in seen:
                        seen.add(node_name)
                        queue.put_nowait({"event": "node",
                                          "data": json.dumps({"node": node_name, "label": label, "view": view},
                                                             ensure_ascii=False, default=str)})
                elif kind == "final":
                    state = payload

            if mode == "data_prep":
                result = _build_data_prep_result(user_id, conv_id, state, provider, model, body.content)
            else:
                reply = state.get("reply") or "已完成。"
                # 透明告知：系统自动推断了采集数量，让用户知道可以改
                if state.get("inferred_quantity") and not state.get("needs_clarification"):
                    n = state["inferred_quantity"]
                    reply += f"\n\n> 💡 本次将采集约 {n} 条数据（系统默认，可随时告知调整数量）。"
                result = _build_result(user_id, conv_id, state, reply, provider, model, body.content)
            result["token_usage"] = dict(usage) if usage["calls"] else None
            # 持久化助手回复（澄清/错误/回执都记入历史，保证多轮上下文连续）
            assistant_text = result.get("reply") or reply
            if result.get("analysis"):
                assistant_text = f"{result['reply']}\n\n---\n\n{result['analysis']}"
            # 持久化展示用元数据（文件/质量/采集器等）——重载会话后仍能展示并下载；
            # 不持久化一次性 HITL 动作(actions/schedule)，它们依赖服务端暂存，重载后不可再确认。
            persist_meta = {
                k: result.get(k)
                for k in ("files", "grade", "collector", "item_count", "data_type", "template_status", "kind", "token_usage", "record_counts", "quality")
                if result.get(k) not in (None, [], "")
            }
            # 记录本轮实际用的模型（供反馈管理页展示"用了什么模型"）
            if provider or model:
                persist_meta["model"] = f"{provider}/{model}" if (provider and model) else (model or provider)
            msg_id = store.add_message(
                conv_id, "assistant", assistant_text,
                task_id=result.get("task_id") or None,
                meta=persist_meta or None,
            )
            result["message_id"] = msg_id
            queue.put_nowait({"event": "result", "data": json.dumps(result, ensure_ascii=False, default=str)})
        except asyncio.CancelledError:
            store.add_message(conv_id, "assistant", "❌ 用户已取消任务")
            queue.put_nowait({"event": "result", "data": json.dumps({
                "conv_id": conv_id, "kind": "cancelled",
                "reply": "❌ 用户已取消任务",
            }, ensure_ascii=False)})
        except Exception as e:  # noqa: BLE001
            try:
                store.add_message(conv_id, "assistant", f"❌ 任务执行失败：{e}")
            except Exception:
                pass
            queue.put_nowait({"event": "error", "data": json.dumps({"message": str(e)}, ensure_ascii=False)})
        finally:
            if _usage_tok is not None:
                _usage_ctx.reset(_usage_tok)
            queue.put_nowait({"event": "done", "data": "{}"})
            _RUNNING.pop(task_key, None)

    task_key = f"{user_id}:{conv_id}"
    bg = asyncio.create_task(pipeline())
    _RUNNING[task_key] = bg

    async def event_gen():
        """只负责把队列里的事件转发给客户端；客户端断开只是本生成器被取消，pipeline 不受影响。"""
        yield {"event": "meta", "data": json.dumps({"conv_id": conv_id}, ensure_ascii=False)}
        while True:
            item = await queue.get()
            yield item
            if item.get("event") == "done":
                break

    return EventSourceResponse(event_gen())


# 正在后台执行的会话任务注册表："user_id:conv_id" -> asyncio.Task（done 后自清）
_RUNNING: Dict[str, asyncio.Task] = {}


@router.get("/running/{conv_id}")
def chat_running(conv_id: str, user=Depends(get_current_user)):
    """查询会话是否有任务仍在后台执行（前端重进会话时用：显示执行中提示并轮询）。"""
    store = get_store()
    conv = store.get_conversation(conv_id)
    if not conv or conv["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="会话不存在")
    task = _RUNNING.get(f"{user['user_id']}:{conv_id}")
    return {"running": bool(task and not task.done())}


@router.post("/{conv_id}/cancel")
async def cancel_pipeline(conv_id: str, user=Depends(get_current_user)):
    """取消正在执行的聊天流水线。"""
    user_id = user["user_id"]
    task = _RUNNING.get(f"{user_id}:{conv_id}")
    if not task or task.done():
        raise HTTPException(status_code=404, detail="没有正在执行的任务")
    task.cancel()
    return {"ok": True, "message": "已取消"}


class FeedbackIn(BaseModel):
    message_id: int
    conv_id: str
    rating: str  # 'up' | 'down'
    reasons: Optional[List[str]] = None
    comment: Optional[str] = None


@router.post("/feedback")
def submit_feedback(body: FeedbackIn, user=Depends(get_current_user)):
    """提交/更新一条消息反馈（点赞/点踩，UNIQUE 覆盖）。"""
    store = get_store()
    user_id = user["user_id"]
    if body.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating 必须为 up/down")
    conv = store.get_conversation(body.conv_id)
    if not conv or conv["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    reasons_json = json.dumps(body.reasons, ensure_ascii=False) if body.reasons else None
    store.upsert_feedback(
        message_id=body.message_id, conv_id=body.conv_id, user_id=user_id,
        rating=body.rating, reasons=reasons_json, comment=body.comment,
    )
    return {"ok": True}


@router.get("/feedback")
def list_feedback(conv_id: str, user=Depends(get_current_user)):
    """返回当前用户在某会话内的全部反馈（重载会话时恢复反馈状态）。"""
    store = get_store()
    conv = store.get_conversation(conv_id)
    if not conv or conv["user_id"] != user["user_id"]:
        raise HTTPException(status_code=404, detail="会话不存在")
    fb = store.list_feedback(conv_id, user["user_id"])
    for _mid, item in fb.items():
        if item.get("reasons"):
            try:
                item["reasons"] = json.loads(item["reasons"])
            except Exception:
                pass
    return {"feedback": fb}


@router.delete("/feedback/{message_id}")
def delete_feedback(message_id: int, user=Depends(get_current_user)):
    """取消一条消息反馈（再点一次赞/踩时调用）。"""
    store = get_store()
    store.delete_feedback(message_id, user["user_id"])
    return {"ok": True}
