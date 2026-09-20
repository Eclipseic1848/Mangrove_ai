"""低敏运营数据：唯一授权谓词、追加式事件和有界查询。"""
from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import sqlite3
import time
from datetime import date, datetime, time as daytime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

TZ = timezone(timedelta(hours=8))
PAGES = {"/": "概览", "/data-prep": "任务工作台", "/chat": "历史对话", "/tasks": "自动化任务", "/templates": "模板库", "/memory": "记忆", "/settings": "设置", "/feedback": "反馈管理", "/admin": "用户管理", "/operations": "运营审计"}
MODULES = tuple(dict.fromkeys([*PAGES.values(), "登录", "模型与连接", "平台配置", "扩展工具", "文件与交付"]))
KINDS = {"login": "登录", "visit": "访问", "action": "操作", "access": "审计查看"}
RESULTS = {"success": "成功", "failure": "失败", "unknown": "结果未知"}


def object_reference(kind, identifier):
    return hashlib.sha256(f"{kind}:{identifier}".encode("utf-8")).hexdigest()[:24]


def bind_object(request, kind, identifier):
    # 只能由已校验的业务路由提供编号，不能解析任意请求或响应正文。
    if request is not None and identifier:
        request.state.operations_object_ref = object_reference(kind, identifier)


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date
    kind: Literal["", "login", "visit", "action", "access"] = ""
    actor_id: str = Field(default="", max_length=128)
    result: Literal["", "success", "failure", "unknown"] = ""
    module: str = Field(default="", max_length=32)
    source: Literal["", "direct", "internal", "external", "unknown"] = ""
    action: str = Field(default="", max_length=32)
    search: str = Field(default="", max_length=80)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=10, ge=1, le=100)
    granularity: Literal["day", "hour"] = "day"

    @model_validator(mode="after")
    def validate_range(self):
        # 兼容已保存视图中的旧名称，不改写不可变审计记录。
        if self.module == "运营与审计":
            self.module = "运营审计"
        if self.end < self.start or (self.end - self.start).days > 365:
            raise ValueError("时间范围应为 1 至 366 天，开始日期不能晚于结束日期")
        if self.module and self.module not in MODULES:
            raise ValueError("请选择有效模块")
        return self

    def bounds(self):
        return (datetime.combine(self.start, daytime.min, TZ).timestamp(), datetime.combine(self.end + timedelta(days=1), daytime.min, TZ).timestamp())


def request_metadata(request):
    # 不信任客户端转发头；地址只保留网段，UA 只保留受控设备分类。
    peer = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(peer)
        masked = str(ipaddress.ip_network(f"{address}/{'24' if address.version == 4 else '48'}", strict=False))
    except ValueError:
        masked = "未知"
    agent = request.headers.get("user-agent", "").lower()[:1024]
    browser = next((label for needle, label in (("edg/", "Edge"), ("chrome", "Chrome"), ("firefox", "Firefox"), ("safari", "Safari")) if needle in agent), "其他浏览器")
    system = next((label for needle, label in (("android", "Android"), ("iphone", "iOS"), ("ipad", "iOS"), ("windows", "Windows"), ("macintosh", "macOS"), ("linux", "Linux")) if needle in agent), "未知系统")
    claims = getattr(request.state, "platform_claims", {})
    sid = claims.get("sid", "")
    return {"ip_mask": masked, "device": f"{browser} / {system}", "session_ref": hashlib.sha256(sid.encode()).hexdigest() if sid else ""}


def identity(conn, actor, *, admin=True):
    # 再查当前角色，不能让 Cookie 缓存、降权或接口参数扩大权限。
    user = conn.execute("SELECT user_id,role,disabled,pending FROM users WHERE user_id=?", (actor["user_id"],)).fetchone()
    if not user or user["disabled"] or user["pending"] or (admin and user["role"] not in ("admin", "super_admin")):
        raise HTTPException(403, "无权查看运营审计")
    return dict(user)


def scope(actor):
    if actor["role"] == "super_admin":
        return "1=1", []
    # 同时校验事件时及当前角色；降权不能暴露过去的管理日志，升权立即收紧历史可见范围。
    return """(e.actor_id=? OR (e.actor_role='user' AND COALESCE(u.role,'user')='user'))
        AND (e.subject_id IS NULL OR e.subject_id=? OR
        (e.subject_role='user' AND COALESCE(s.role,'user')='user'))""", [actor["user_id"], actor["user_id"]]


FROM = """FROM (SELECT r.event_id,r.occurred_at,r.kind,
    COALESCE(o.actor_id,r.actor_id) AS actor_id,COALESCE(o.actor_role,r.actor_role) AS actor_role,
    CASE WHEN r.module='运营与审计' THEN '运营审计' ELSE r.module END AS module,
    r.action,COALESCE(json_extract(o.changes_json,'$[0].context.object_ref'),r.object_ref) AS object_ref,r.ip_mask,r.device,r.source,r.session_ref,
    COALESCE(o.result,'unknown') AS result,o.subject_id,o.subject_role,
    o.status_code,o.duration_ms,COALESCE(o.changes_json,'[]') AS changes_json
    FROM operations_events r LEFT JOIN operations_outcomes o ON o.event_id=r.event_id
    ) e LEFT JOIN users u ON u.user_id=e.actor_id LEFT JOIN users s ON s.user_id=e.subject_id"""


def predicate(conn, actor, filters):
    condition, values = scope(actor)
    start, end = filters.bounds()
    retention = conn.execute("SELECT retention_days FROM operations_policy WHERE singleton=1").fetchone()[0]
    clauses = [condition, "e.occurred_at>=?", "e.occurred_at<?"]
    values += [max(start, time.time() - retention * 86400), end]
    for field in ("kind", "actor_id", "result", "module", "source", "action"):
        if value := getattr(filters, field):
            clauses.append(f"e.{field}=?")
            values.append(value)
    if filters.search.strip():
        clauses.append("(instr(COALESCE(u.display_name,''),?)>0 OR instr(COALESCE(u.username,''),?)>0 OR instr(e.event_id,?)>0 OR instr(e.object_ref,?)>0)")
        values += [filters.search.strip()] * 4
    return " AND ".join(clauses), values


def begin(conn, *, kind, module, action, actor=None, object_ref="", metadata=None, source="unknown", client_id=None):
    metadata = metadata or {}
    event_id = uuid4().hex
    if client_id and actor:
        existing = conn.execute("SELECT event_id,kind,module,object_ref,source FROM operations_events WHERE actor_id=? AND client_id=?", (actor["user_id"], client_id)).fetchone()
        if existing:
            if tuple(existing)[1:] != (kind, module, object_ref, source):
                raise HTTPException(409, "同一访问编号不能用于不同页面，请重新记录")
            return existing[0]
    conn.execute("INSERT INTO operations_events(event_id,occurred_at,kind,actor_id,actor_role,module,action,object_ref,ip_mask,device,source,session_ref,client_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        event_id, time.time(), kind, actor["user_id"] if actor else None, actor.get("role") if actor else None,
        module, action, object_ref, metadata.get("ip_mask", ""), metadata.get("device", "未知"), source, metadata.get("session_ref", ""), client_id,
    ))
    return event_id


def finish(conn, event_id, *, result="success", actor=None, subject=None, status_code=None, duration_ms=None, changes=()):
    conn.execute("INSERT OR IGNORE INTO operations_outcomes(event_id,result,actor_id,actor_role,subject_id,subject_role,status_code,duration_ms,changes_json,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (
        event_id, result, actor["user_id"] if actor else None, actor.get("role") if actor else None,
        subject["user_id"] if subject else None, subject.get("role") if subject else None,
        status_code, duration_ms, json.dumps(list(changes), ensure_ascii=False), time.time(),
    ))


def heartbeat(conn, actor, session_ref):
    if not session_ref:
        raise HTTPException(401, "会话身份失效，请重新登录")
    now = time.time()
    previous = conn.execute("SELECT * FROM operations_presence WHERE actor_id=? AND session_ref=? ORDER BY last_seen DESC LIMIT 1", (actor["user_id"], session_ref)).fetchone()
    # 同一登录会话跨标签去重；90 秒无心跳视为离开，跨日分段，时长不信任客户端。
    if previous and previous["actor_role"] == actor["role"] and 0 <= now - previous["last_seen"] <= 90 and datetime.fromtimestamp(now, TZ).date() == datetime.fromtimestamp(previous["last_seen"], TZ).date():
        conn.execute("UPDATE operations_presence SET last_seen=?,active_seconds=active_seconds+? WHERE actor_id=? AND session_ref=? AND tab_id=?", (now, min(45, now - previous["last_seen"]), actor["user_id"], session_ref, previous["tab_id"]))
    else:
        conn.execute("INSERT INTO operations_presence VALUES (?,?,?,?,?,?,0)", (actor["user_id"], actor["role"], session_ref, uuid4().hex, now, now))
    policy = conn.execute("SELECT retention_days,last_pruned_at FROM operations_policy WHERE singleton=1").fetchone()
    if now - policy["last_pruned_at"] >= 3600:
        cutoff = now - policy["retention_days"] * 86400
        # 只清理本模块到期记录，每次有界，避免阻塞业务数据库。
        conn.execute("DELETE FROM operations_events WHERE event_id IN (SELECT event_id FROM operations_events WHERE occurred_at<? LIMIT 2000)", (cutoff,))
        conn.execute("DELETE FROM operations_presence WHERE rowid IN (SELECT rowid FROM operations_presence WHERE last_seen<? LIMIT 2000)", (cutoff,))
        conn.execute("UPDATE operations_policy SET last_pruned_at=? WHERE singleton=1", (now,))


def sessions(conn, actor, filters):
    # 会话只能关联当前仍属于可见角色的用户；会话不按页面或结果拆分。
    condition, params = ("1=1", []) if actor["role"] == "super_admin" else ("(p.actor_id=? OR (p.actor_role='user' AND u.role='user'))", [actor["user_id"]])
    if filters.actor_id:
        condition += " AND p.actor_id=?"
        params.append(filters.actor_id)
    start, end = filters.bounds()
    retention = conn.execute("SELECT retention_days FROM operations_policy WHERE singleton=1").fetchone()[0]
    start = max(start, time.time() - retention * 86400)
    base = f"FROM operations_presence p JOIN users u ON p.actor_id=u.user_id WHERE {condition}"
    result = dict(conn.execute(f"SELECT COUNT(*) AS count,COALESCE(AVG(p.active_seconds),0) AS average_seconds {base} AND p.started_at>=? AND p.started_at<?", [*params, start, end]).fetchone())
    result["online_users"] = conn.execute(f"SELECT COUNT(DISTINCT p.actor_id) {base} AND p.last_seen>=?", [*params, time.time() - 90]).fetchone()[0]
    result["average_seconds"] = round(result["average_seconds"])
    # 按心跳有效区间扫描峰值；同一用户多会话合并计数，不将会话数冒充人数。
    points = []
    for row in conn.execute(f"SELECT p.actor_id,p.started_at,p.last_seen {base} AND p.last_seen+90>? AND p.started_at<?", [*params, start, end]):
        points.append((max(start, row["started_at"]), 1, row["actor_id"]))
        points.append((min(end, row["last_seen"] + 90), -1, row["actor_id"]))
    active = {}
    peak = 0
    for _, delta, user_id in sorted(points):
        active[user_id] = active.get(user_id, 0) + delta
        if active[user_id] <= 0:
            active.pop(user_id, None)
        peak = max(peak, len(active))
    result["peak_users"] = peak
    return result


def row_public(row):
    item = dict(row)
    item["changes"] = json.loads(item.pop("changes_json", "[]"))
    item["context"] = next((change["context"] for change in item["changes"] if "context" in change), {})
    item["changes"] = [change for change in item["changes"] if "context" not in change]
    item.pop("session_ref", None)
    item.pop("subject_role", None)
    item["occurred_at"] = datetime.fromtimestamp(item["occurred_at"], timezone.utc).isoformat()
    return item


SELECT = "SELECT e.*,COALESCE(u.display_name,u.username,CASE WHEN e.actor_id IS NULL THEN '未识别账号' ELSE '已删除用户' END) AS actor_name "


def query(conn, actor, filters, *, limit=None):
    where, params = predicate(conn, actor, filters)
    total = conn.execute(f"SELECT COUNT(*) {FROM} WHERE {where}", params).fetchone()[0]
    if limit is not None and total > limit:
        raise HTTPException(413, f"超过 {limit} 条，请缩小时间或筛选范围")
    size = limit or filters.page_size
    page = min(filters.page, max(1, (total + size - 1) // size))
    rows = conn.execute(f"{SELECT}{FROM} WHERE {where} ORDER BY e.occurred_at DESC,e.event_id DESC LIMIT ? OFFSET ?", [*params, size, 0 if limit else (page - 1) * size]).fetchall()
    return {"items": [row_public(row) for row in rows], "total": total, "page": page, "page_size": size}


def summary(conn, actor, filters):
    where, params = predicate(conn, actor, filters)
    row = conn.execute(f"""SELECT
        COUNT(CASE WHEN e.kind='visit' THEN 1 END) AS pv,
        COUNT(DISTINCT CASE WHEN e.kind='visit' THEN e.actor_id END) AS uv,
        COUNT(CASE WHEN e.kind='login' AND e.result='success' THEN 1 END) AS login_success,
        COUNT(CASE WHEN e.kind='login' AND e.result='failure' THEN 1 END) AS login_failure,
        COUNT(CASE WHEN e.kind='action' AND e.result='failure' THEN 1 END) AS action_failure,
        COUNT(CASE WHEN e.kind='action' THEN 1 END) AS actions
        {FROM} WHERE {where}""", params).fetchone()
    result = dict(row)
    result["pv_per_user"] = round(result["pv"] / result["uv"], 2) if result["uv"] else 0
    pattern = "%Y-%m-%d %H:00" if filters.granularity == "hour" else "%Y-%m-%d"
    result["trend"] = [dict(item) for item in conn.execute(f"""SELECT
        strftime(?,e.occurred_at,'unixepoch','+8 hours') AS bucket,
        COUNT(CASE WHEN e.kind='visit' THEN 1 END) AS pv,
        COUNT(DISTINCT CASE WHEN e.kind='visit' THEN e.actor_id END) AS uv,
        COUNT(CASE WHEN e.kind='login' AND e.result='success' THEN 1 END) AS logins,
        COUNT(CASE WHEN e.result='failure' THEN 1 END) AS failures
        {FROM} WHERE {where} AND e.kind!='access' GROUP BY bucket ORDER BY bucket""", [pattern, *params])]
    result["modules"] = [dict(item) for item in conn.execute(f"""SELECT e.module,
        COUNT(CASE WHEN e.kind='visit' THEN 1 END) AS pv,
        COUNT(DISTINCT CASE WHEN e.kind='visit' THEN e.actor_id END) AS uv,
        COUNT(CASE WHEN e.kind='action' THEN 1 END) AS actions
        {FROM} WHERE {where} AND e.kind IN ('visit','action') GROUP BY e.module ORDER BY pv DESC,actions DESC""", params)]
    result["sources"] = [dict(item) for item in conn.execute(f"SELECT e.source,COUNT(*) AS pv {FROM} WHERE {where} AND e.kind='visit' GROUP BY e.source ORDER BY pv DESC", params)]
    result["users"] = [dict(item) for item in conn.execute(f"""SELECT e.actor_id,
        COALESCE(u.display_name,u.username,'已删除用户') AS actor_name,
        COUNT(CASE WHEN e.kind='visit' THEN 1 END) AS pv,
        COUNT(CASE WHEN e.kind='action' THEN 1 END) AS actions,
        COUNT(CASE WHEN e.kind='login' AND e.result='success' THEN 1 END) AS logins,
        MAX(e.occurred_at) AS last_active
        {FROM} WHERE {where} AND e.kind IN ('visit','login','action') AND e.actor_id IS NOT NULL
        GROUP BY e.actor_id ORDER BY pv DESC,actions DESC LIMIT 100""", params)]
    result["active_users"] = {}
    # 活跃口径为成功登录或页面访问，窗口截止于所选结束日；保留账号与模块筛选。
    for name, days in (("day", 1), ("week", 7), ("month", 30)):
        rolling = filters.model_copy(update={"start": filters.end - timedelta(days=days - 1), "kind": "", "result": ""})
        active_where, active_params = predicate(conn, actor, rolling)
        result["active_users"][name] = conn.execute(f"SELECT COUNT(DISTINCT e.actor_id) {FROM} WHERE {active_where} AND (e.kind='visit' OR (e.kind='login' AND e.result='success'))", active_params).fetchone()[0]
    policy = conn.execute("SELECT started_at,retention_days FROM operations_policy WHERE singleton=1").fetchone()
    result["coverage"] = {"started_at": datetime.fromtimestamp(policy["started_at"], timezone.utc).isoformat(), "retention_days": policy["retention_days"], "timezone": "Asia/Shanghai"}
    result["sessions"] = sessions(conn, actor, filters)
    result["comparison"] = {}
    available_start = max(policy["started_at"], time.time() - policy["retention_days"] * 86400)
    current_start, current_end = filters.bounds()
    elapsed = max(0, min(time.time(), current_end) - current_start)
    for name, days in (("previous", (filters.end - filters.start).days + 1), ("year", 365)):
        earlier = filters.model_copy(update={"start": filters.start - timedelta(days=days), "end": filters.end - timedelta(days=days)})
        before_start, _ = earlier.bounds()
        if before_start < available_start or elapsed == 0:
            result["comparison"][name] = {"available": False, "reason": "采集或保留范围不足"}
            continue
        earlier_where, earlier_params = predicate(conn, actor, earlier)
        counts = conn.execute(f"SELECT COUNT(*) AS pv,COUNT(DISTINCT e.actor_id) AS uv {FROM} WHERE {earlier_where} AND e.kind='visit' AND e.occurred_at<?", [*earlier_params, before_start + elapsed]).fetchone()
        result["comparison"][name] = {"available": True, **dict(counts),
            "pv_change": round((result["pv"] - counts["pv"]) / counts["pv"] * 100, 1) if counts["pv"] else None,
            "uv_change": round((result["uv"] - counts["uv"]) / counts["uv"] * 100, 1) if counts["uv"] else None}
    user_condition, user_params = ("1=1", []) if actor["role"] == "super_admin" else ("role='user' OR user_id=?", [actor["user_id"]])
    if filters.actor_id:
        user_condition = f"({user_condition}) AND user_id=?"
        user_params.append(filters.actor_id)
    visible = {row[0] for row in conn.execute(f"SELECT user_id FROM users WHERE {user_condition}", user_params)}
    usage = {row["actor_id"]: row["count"] for row in conn.execute(f"SELECT e.actor_id,COUNT(*) AS count {FROM} WHERE {where} AND (e.kind='visit' OR (e.kind='login' AND e.result='success')) GROUP BY e.actor_id", params)}
    result["activity_distribution"] = {"high": sum(usage.get(user, 0) >= 10 for user in visible),
        "active": sum(0 < usage.get(user, 0) < 10 for user in visible), "unseen": sum(not usage.get(user, 0) for user in visible), "threshold": 10}
    return result


def export_data(conn, actor, filters, format):
    rows = query(conn, actor, filters, limit=10000)["items"]
    table = [["事件编号", "时间（UTC）", "用户", "类型", "模块", "操作", "对象编号", "结果", "IP网段", "设备"]]
    for row in rows:
        cells = [row["event_id"], row["occurred_at"], row["actor_name"], KINDS[row["kind"]], row["module"], row["action"], row["object_ref"], RESULTS[row["result"]], row["ip_mask"], row["device"]]
        # CSV 与 Excel 都拒绝将用户可控名称解释成公式。
        table.append(["'" + str(value) if str(value).lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else str(value) for value in cells])
    if format == "csv":
        stream = io.StringIO(newline="")
        csv.writer(stream).writerows(table)
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", len(rows)
    from openpyxl import Workbook
    book = Workbook(write_only=True)
    sheet = book.create_sheet("运营日志")
    for row in table:
        sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(rows)
