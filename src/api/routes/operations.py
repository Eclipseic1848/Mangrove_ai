"""管理员运营读模型；所有读取与导出均在返回前提交审计。"""
import json
import hashlib
import logging
import time
from uuid import UUID, uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from src import operations as ops
from src import operations_usage as usage
from ..auth import get_current_user, get_store

class AuditedRoute(APIRoute):
    """查询尝试独立提交，业务回滚不能抹掉拒绝或失败记录。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def audited(request: Request):
            if self.path.endswith(("/visits", "/heartbeat")):
                return await handler(request)
            suffix = self.path.removeprefix("/api/operations")
            action = {"/options": "查看筛选项", "/summary": "查看统计", "/events/query": "查看日志", "/events/{event_id}": "查看详情", "/export": "导出日志", "/policy": "修改保留策略"}.get(suffix, "删除常用视图" if request.method == "DELETE" else "保存常用视图" if request.method == "POST" else "查看常用视图")
            if suffix.startswith('/tokens/'):
                action = '导出Token统计' if suffix.endswith('/export') else '查看Token统计'
            store = get_store()

            def start():
                with store._conn() as conn:
                    return ops.begin(conn, kind="access", module="运营审计", action=action, metadata=ops.request_metadata(request))

            try:
                event = await run_in_threadpool(start)
            except Exception:
                raise HTTPException(503, "审计记录暂不可用，请稍后重试") from None
            request.state.operations_event_id = event
            request.state.operations_context = {}
            request.state.operations_changes = []
            started = time.monotonic()

            def finish(status):
                with store._conn() as conn:
                    ops.finish(conn, event, actor=getattr(request.state, "platform_user", None),
                        result="unknown" if status is None else "success" if status < 400 else "failure",
                        status_code=status, duration_ms=round((time.monotonic() - started) * 1000),
                        changes=[{"context": request.state.operations_context}, *request.state.operations_changes])

            try:
                response = await handler(request)
            except Exception as cause:
                try:
                    await run_in_threadpool(finish, 422 if isinstance(cause, RequestValidationError) else cause.status_code if isinstance(cause, StarletteHTTPException) else None)
                except Exception:
                    logging.getLogger(__name__).error("审计访问结果缺失；请求记录保留为未知")
                raise
            try:
                await run_in_threadpool(finish, response.status_code)
            except Exception:
                # 请求可能已完成，不能伪造失败诱导重复变更。
                response.headers["X-Mangrove-Audit"] = "outcome-unknown"
            response.headers["Cache-Control"] = "no-store"
            return response
        return audited


router = APIRouter(prefix="/api/operations", tags=["operations"], route_class=AuditedRoute)


@router.post('/tokens/query')
def token_usage(filters: usage.UsageFilters, request: Request, user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute('BEGIN')
        actor = ops.identity(conn, user)
        result = usage.query(conn, actor, filters)
        request.state.operations_context = {'start': str(filters.start), 'end': str(filters.end), 'count': result['total']}
        return result


class UsageExport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filters: usage.UsageFilters
    format: Literal['csv', 'xlsx']


@router.post('/tokens/export')
def export_token_usage(body: UsageExport, request: Request, user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute('BEGIN')
        actor = ops.identity(conn, user)
        content, mime, count = usage.export_data(conn, actor, body.filters, body.format)
        request.state.operations_context = {'start': str(body.filters.start), 'end': str(body.filters.end), 'count': count, 'format': body.format}
    return Response(content, media_type=mime, headers={'Content-Disposition': f'attachment; filename="token-usage.{body.format}"'})


def query_context(request, filters, count=None):
    request.state.operations_context = {"start": str(filters.start), "end": str(filters.end), "kind": filters.kind,
        "module": filters.module, "result": filters.result, "count": count,
        "query_digest": hashlib.sha256(filters.model_dump_json().encode("utf-8")).hexdigest()}


@router.get("/options")
def options(user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        condition, params = ("1=1", []) if actor["role"] == "super_admin" else ("role='user' OR user_id=?", [actor["user_id"]])
        users = [dict(row) for row in conn.execute(f"SELECT user_id,COALESCE(display_name,username) AS name FROM users WHERE {condition} ORDER BY username", params)]
        policy = dict(conn.execute("SELECT retention_days,version FROM operations_policy WHERE singleton=1").fetchone())
        visible, visible_params = ops.scope(actor)
        actions = [row[0] for row in conn.execute(f"SELECT DISTINCT e.action {ops.FROM} WHERE {visible} AND e.occurred_at>=? ORDER BY e.action", [*visible_params, time.time() - policy["retention_days"] * 86400])]
        return {"users": users, "modules": ops.MODULES, "actions": actions, "policy": policy}


class Visit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    page: str
    source: Literal["direct", "internal", "external", "unknown"] = "unknown"


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/heartbeat")
def heartbeat(body: Heartbeat, request: Request, user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user, admin=False)
        ops.heartbeat(conn, actor, ops.request_metadata(request)["session_ref"])
    return {"ok": True}


@router.post("/visits")
def visit(body: Visit, request: Request, user=Depends(get_current_user)):
    if body.page not in ops.PAGES:
        raise HTTPException(422, "不记录未知页面或带参数的网址")
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user, admin=False)
        if body.page in ("/operations", "/admin", "/feedback") and actor["role"] == "user":
            raise HTTPException(403, "无权访问该页面")
        event = ops.begin(conn, kind="visit", module=ops.PAGES[body.page], action="访问页面", actor=actor, object_ref=body.page, metadata=ops.request_metadata(request), source=body.source, client_id=str(body.event_id))
        ops.finish(conn, event)
    return {"event_id": event}


@router.post("/events/query")
def events(filters: ops.Filters, request: Request, user=Depends(get_current_user)):
    query_context(request, filters)
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        result = ops.query(conn, actor, filters)
        query_context(request, filters, len(result["items"]))
        return result


@router.post("/summary")
def summary(filters: ops.Filters, request: Request, user=Depends(get_current_user)):
    query_context(request, filters)
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        result = ops.summary(conn, actor, filters)
        return result


@router.get("/events/{event_id}")
def detail(event_id: UUID, request: Request, user=Depends(get_current_user)):
    request.state.operations_context = {"event_id": event_id.hex}
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        where, params = ops.scope(actor)
        cutoff = ops.time.time() - conn.execute("SELECT retention_days FROM operations_policy").fetchone()[0] * 86400
        row = conn.execute(f"{ops.SELECT}{ops.FROM} WHERE {where} AND e.event_id=? AND e.occurred_at>=?", [*params, event_id.hex, cutoff]).fetchone()
        result = ops.row_public(row) if row else None
    if result is None:
        raise HTTPException(404, "记录不存在、已过保留期或无权查看")
    return result


class Export(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: ops.Filters
    format: Literal["csv", "xlsx"]


@router.post("/export")
def export(body: Export, request: Request, user=Depends(get_current_user)):
    query_context(request, body.filters)
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        content, mime, count = ops.export_data(conn, actor, body.filters, body.format)
        query_context(request, body.filters, count)
        event = request.state.operations_event_id
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="operations-{event}.{body.format}"', "Cache-Control": "no-store"})


class SavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=30, pattern=r"\S")
    filters: ops.Filters
    tab: Literal["overview", "login", "visit", "audit", "users"] = "overview"


@router.get("/views")
def views(user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        items = [{"view_id": row["view_id"], "name": row["name"], **json.loads(row["filters_json"])} for row in conn.execute("SELECT * FROM operations_views WHERE owner_id=? ORDER BY rowid DESC", (actor["user_id"],))]
        for item in items:
            if item.get("filters", {}).get("module") == "运营与审计":
                item["filters"]["module"] = "运营审计"
        return {"items": items}


@router.post("/views")
def save_view(body: SavedView, user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        if conn.execute("SELECT COUNT(*) FROM operations_views WHERE owner_id=?", (actor["user_id"],)).fetchone()[0] >= 20:
            raise HTTPException(409, "最多保存 20 个视图，请先删除不再使用的视图")
        view_id = uuid4().hex
        conn.execute("INSERT INTO operations_views VALUES (?,?,?,?)", (actor["user_id"], view_id, body.name.strip(), json.dumps({"filters": body.filters.model_dump(mode="json"), "tab": body.tab}, ensure_ascii=False)))
        return {"view_id": view_id}


@router.delete("/views/{view_id}")
def delete_view(view_id: UUID, user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        deleted = conn.execute("DELETE FROM operations_views WHERE owner_id=? AND view_id=?", (actor["user_id"], view_id.hex)).rowcount
    if not deleted:
        raise HTTPException(404, "视图不存在或无权访问")
    return {"ok": True}


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retention_days: Literal[90, 180]
    version: int = Field(ge=1)
    confirmed: Literal[True]


@router.put("/policy")
def set_policy(body: Policy, request: Request, user=Depends(get_current_user)):
    with get_store()._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = ops.identity(conn, user)
        if actor["role"] != "super_admin":
            raise HTTPException(403, "只有超级管理员可以修改保留策略")
        current = conn.execute("SELECT retention_days,version FROM operations_policy WHERE singleton=1").fetchone()
        if current["version"] != body.version:
            raise HTTPException(409, "策略已被修改，请刷新后重试")
        conn.execute("UPDATE operations_policy SET retention_days=?,version=version+1 WHERE singleton=1", (body.retention_days,))
        request.state.operations_changes = [{"field": "运营日志保留天数", "before": current["retention_days"], "after": body.retention_days}]
        return {"retention_days": body.retention_days, "version": body.version + 1}
