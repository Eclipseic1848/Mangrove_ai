"""定时任务路由：确认创建（来自聊天暂存）/ 手动创建 / 模板 / 列表 / 编辑 / 暂停恢复 /
立即执行 / 取消 / 查看上次报告 / 跨任务执行记录。按用户归属隔离。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.scheduler import Schedule, TASK_TEMPLATES, compute_next_run, parse_schedule

from ..auth import get_current_user
from ..schemas import ManualTaskIn, ScheduleIn, TaskPatchIn, TriggerIn
from ..services import get_schedule_store, get_scheduler_service
from ..session_store import pending_store

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _trigger_to_schedule_str(trigger: TriggerIn) -> str:
    """把表单化的触发方式转成 once@/cron@/every@ 调度串，复用既有 parse_schedule 校验。"""
    if trigger.type == "cron":
        return f"cron@{trigger.cron_expr or ''}"
    if trigger.type == "interval":
        return f"every@{trigger.interval_seconds or 0}"
    if trigger.type == "once":
        return f"once@{trigger.run_at or ''}"
    raise HTTPException(status_code=422, detail=f"未知触发类型: {trigger.type!r}")


@router.get("/templates")
def list_templates(user=Depends(get_current_user)) -> List[Dict[str, Any]]:
    """自动化任务模板（场景化预设），供任务中心「添加自动化」时快速预填。"""
    return TASK_TEMPLATES


@router.get("/runs/recent")
def recent_runs(
    task_id: Optional[str] = None,
    success: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    """跨任务聚合的执行记录（新→旧，后端分页），供任务中心「运行记录」Tab 展示。

    task_id/success/q 分别对应前端的任务筛选/状态筛选/关键词搜索；
    同「单任务执行历史」一样不暴露服务器文件路径，只给前端渲染/下载所需的字段。
    """
    store = get_schedule_store()
    filters: Dict[str, Any] = {
        "owner_user_id": user["user_id"], "task_id": task_id, "success": success, "q": q,
    }
    rows = store.list_recent_runs(limit=limit, offset=offset, **filters)
    total = store.count_recent_runs(**filters)
    items = [
        {
            "run_id": r["run_id"],
            "task_id": r["task_id"],
            "task_name": r.get("task_name") or (r.get("task_user_input") or "")[:30],
            "run_at": r["run_at"],
            "success": bool(r["success"]),
            "summary": (r["summary"] or "")[:200],
            "has_report": bool(r["report_path"]),
            "has_json": bool(r["json_path"]),
        }
        for r in rows
    ]
    return {"items": items, "total": total}


@router.get("")
def list_tasks(user=Depends(get_current_user)) -> List[Dict[str, Any]]:
    return get_schedule_store().list_active(owner_user_id=user["user_id"])


@router.post("")
def create_task(body: ScheduleIn, user=Depends(get_current_user)):
    pend = pending_store.pop_action(user["user_id"], body.task_id, "schedule")
    if not pend:
        raise HTTPException(status_code=404, detail="没有待创建的定时任务或已处理")
    try:
        sched = parse_schedule(pend.get("schedule", ""))
        next_run = compute_next_run(sched)
        if next_run is None:
            raise HTTPException(status_code=422, detail="该计划的执行时间已过或无后续，未创建")
        user_input = pend.get("user_input", "")
        sched_id = get_schedule_store().add(
            user_input=user_input, provider=pend.get("provider"),
            model=pend.get("model"), trigger_type=sched.trigger_type,
            cron_expr=sched.cron_expr, run_at=sched.run_at, next_run_at=next_run,
            owner_user_id=user["user_id"],
            name=(pend.get("intent") or user_input)[:30] or None,
            source="auto",
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"创建定时任务失败：{e}")
    return {"ok": True, "task_id": sched_id, "next_run_at": next_run.isoformat(timespec="minutes")}


@router.post("/manual")
def create_manual_task(body: ManualTaskIn, user=Depends(get_current_user)):
    """手动创建自动化任务（含从模板创建：template_id 非空则 source 记 template）。"""
    try:
        sched = parse_schedule(_trigger_to_schedule_str(body.trigger))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    next_run = compute_next_run(sched, start_date=body.start_date, end_date=body.end_date)
    if next_run is None:
        raise HTTPException(status_code=422, detail="该计划的执行时间已过或无后续，未创建")
    sched_id = get_schedule_store().add(
        user_input=body.prompt, provider=body.provider, model=body.model,
        trigger_type=sched.trigger_type, cron_expr=sched.cron_expr, run_at=sched.run_at,
        next_run_at=next_run, owner_user_id=user["user_id"],
        name=body.name, source="template" if body.template_id else "manual",
        interval_seconds=sched.interval_seconds,
        start_date=body.start_date, end_date=body.end_date,
    )
    return {"ok": True, "task_id": sched_id, "next_run_at": next_run.isoformat(timespec="minutes")}


@router.patch("/{sched_id}")
def update_task(sched_id: str, body: TaskPatchIn, user=Depends(get_current_user)):
    """暂停/恢复（仅传 status）或整体编辑（名称/提示词/触发方式/生效区间）。"""
    task = _owned_task(sched_id, user)
    store = get_schedule_store()

    if body.status is not None:
        if body.status not in ("active", "paused"):
            raise HTTPException(status_code=422, detail="status 仅支持 active/paused")
        if body.status == "paused":
            store.set_status(sched_id, "paused")
            return {"ok": True}
        # 恢复：原定时刻可能已过去，需重算 next_run_at
        cur_sched = Schedule(
            trigger_type=task["trigger_type"], cron_expr=task.get("cron_expr"),
            interval_seconds=task.get("interval_seconds"),
            run_at=datetime.fromisoformat(task["run_at"]) if task.get("run_at") else None,
        )
        next_run = compute_next_run(
            cur_sched, start_date=task.get("start_date"), end_date=task.get("end_date")
        )
        if next_run is None:
            raise HTTPException(status_code=422, detail="该任务的执行时间已过或无后续，无法恢复")
        store.set_status(sched_id, "active", next_run_at=next_run)
        return {"ok": True}

    # 整体编辑：未传的字段沿用旧值
    name = body.name if body.name is not None else task.get("name")
    user_input = body.prompt if body.prompt is not None else task["user_input"]
    provider = body.provider if body.provider is not None else task.get("provider")
    model = body.model if body.model is not None else task.get("model")
    start_date = body.start_date if body.start_date is not None else task.get("start_date")
    end_date = body.end_date if body.end_date is not None else task.get("end_date")

    if body.trigger is not None:
        try:
            sched = parse_schedule(_trigger_to_schedule_str(body.trigger))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        sched = Schedule(
            trigger_type=task["trigger_type"], cron_expr=task.get("cron_expr"),
            interval_seconds=task.get("interval_seconds"),
            run_at=datetime.fromisoformat(task["run_at"]) if task.get("run_at") else None,
        )

    next_run = compute_next_run(sched, start_date=start_date, end_date=end_date)
    if next_run is None:
        raise HTTPException(status_code=422, detail="该计划的执行时间已过或无后续，未保存")

    store.edit(
        sched_id, name=name, user_input=user_input, provider=provider, model=model,
        trigger_type=sched.trigger_type, cron_expr=sched.cron_expr,
        interval_seconds=sched.interval_seconds, run_at=sched.run_at,
        next_run_at=next_run, start_date=start_date, end_date=end_date,
    )
    return {"ok": True, "next_run_at": next_run.isoformat(timespec="minutes")}


@router.post("/{sched_id}/run_now")
async def run_task_now_endpoint(sched_id: str, user=Depends(get_current_user)):
    """立即执行一次，不影响原定 next_run_at/status。"""
    _owned_task(sched_id, user)
    outcome = await get_scheduler_service().run_task_now(sched_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="任务不存在")
    if outcome == "running":
        raise HTTPException(status_code=409, detail="任务正在执行中，请稍候")
    return {"ok": True, "started": True}


@router.get("/{sched_id}/report")
def task_report(sched_id: str, user=Depends(get_current_user)) -> Dict[str, Any]:
    """读取任务上次执行生成的报告正文（Markdown），供前端弹窗渲染。

    路径来自调度器写入的 last_result（服务端自有数据，非用户输入），
    用户侧不暴露文件系统路径——前端拿到的是可直接渲染的报告内容。
    """
    store = get_schedule_store()
    task = store.get(sched_id)
    if not task or task.get("owner_user_id") != user["user_id"]:
        raise HTTPException(status_code=404, detail="任务不存在")
    m = re.search(r"report=([^;]+)", task.get("last_result") or "")
    if not m:
        detail = task.get("last_error") or "该任务还没有生成过报告"
        raise HTTPException(status_code=404, detail=detail)
    p = Path(m.group(1).strip())
    if not p.is_file():
        raise HTTPException(status_code=404, detail="报告文件已不存在（可能已被清理）")
    return {
        "content": p.read_text(encoding="utf-8", errors="replace"),
        "last_run_at": task.get("last_run_at"),
        "success": bool(task.get("last_success")),
    }


def _owned_task(sched_id: str, user) -> Dict[str, Any]:
    """取任务并校验归属，不存在或非本人一律 404（不暴露他人任务存在性）。"""
    task = get_schedule_store().get(sched_id)
    if not task or task.get("owner_user_id") != user["user_id"]:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{sched_id}/runs")
def task_runs(sched_id: str, user=Depends(get_current_user)) -> List[Dict[str, Any]]:
    """执行历史列表（新→旧）：周期任务的每次执行一条，供前端按次查看/下载报告。"""
    _owned_task(sched_id, user)
    runs = get_schedule_store().list_runs(sched_id)
    return [
        {
            "run_id": r["run_id"],
            "run_at": r["run_at"],
            "success": bool(r["success"]),
            "summary": (r["summary"] or "")[:200],
            "has_report": bool(r["report_path"]),
            "has_json": bool(r["json_path"]),
        }
        for r in runs
    ]


@router.get("/{sched_id}/runs/{run_id}/report")
def run_report(sched_id: str, run_id: int, user=Depends(get_current_user)) -> Dict[str, Any]:
    """某次执行的报告正文（Markdown），供前端弹窗渲染。"""
    _owned_task(sched_id, user)
    run = get_schedule_store().get_run(sched_id, run_id)
    if not run or not run.get("report_path"):
        raise HTTPException(status_code=404, detail="该次执行没有生成报告")
    p = Path(run["report_path"])
    if not p.is_file():
        raise HTTPException(status_code=404, detail="报告文件已不存在（可能已被清理）")
    return {"content": p.read_text(encoding="utf-8", errors="replace"),
            "run_at": run["run_at"], "success": bool(run["success"])}


@router.get("/{sched_id}/runs/{run_id}/download")
def run_download(sched_id: str, run_id: int, kind: str = "report", user=Depends(get_current_user)):
    """下载某次执行的产物文件。kind=report（Markdown 报告）| json（采集数据）。"""
    _owned_task(sched_id, user)
    run = get_schedule_store().get_run(sched_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    path = run.get("report_path") if kind == "report" else run.get("json_path")
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="文件不存在（可能已被清理）")
    ts = (run.get("run_at") or "").replace(":", "").replace("-", "").replace("T", "_")
    suffix = ".md" if kind == "report" else ".json"
    media = "text/markdown" if kind == "report" else "application/json"
    return FileResponse(path, filename=f"{kind}_{ts}{suffix}", media_type=media)


@router.delete("/{sched_id}")
def cancel_task(sched_id: str, user=Depends(get_current_user)):
    store = get_schedule_store()
    task = store.get(sched_id)
    if not task or task.get("owner_user_id") != user["user_id"]:
        raise HTTPException(status_code=404, detail="任务不存在")
    ok = store.cancel(sched_id)
    return {"ok": ok, "message": "已取消。" if ok else "任务已是取消状态。"}
