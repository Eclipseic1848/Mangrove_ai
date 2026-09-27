"""先持久化请求，再追加结果；中断只留下未知，不把接受请求冒充任务完成。"""
import hashlib
import logging
import time

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from src import operations as ops

logger = logging.getLogger(__name__)
MODULE_PREFIXES = (
    ("/api/admin/capabilit", "扩展工具"),
    ("/api/auth/", "登录"), ("/api/admin/", "用户管理"),
    ("/api/config", "平台配置"), ("/api/model-connections", "模型与连接"),
    ("/api/semantic-", "任务工作台"), ("/api/chat", "任务工作台"),
    ("/api/conversations", "任务工作台"), ("/api/data-", "文件与交付"),
    ("/api/tasks", "自动化任务"), ("/api/templates", "模板库"),
    ("/api/memory", "记忆"), ("/api/feedback", "反馈管理"),
    ("/api/capabilit", "扩展工具"),
    ("/api/confirm", "任务工作台"), ("/api/downloads/", "文件与交付"),
    ("/api/lessons", "模板库"),
)


def action_identity(path, method):
    """仅按受控路径标注动作；对象摘要不包含文件名、正文或查询参数。"""
    parts = path.strip("/").split("/")
    tail = parts[-1]
    download = method == "GET" and (path.startswith("/api/downloads/") or
        path == "/api/feedback/export" or tail in ("download", "bundle", "source-bundle") or
        (path.startswith("/api/semantic-workspace/tasks/") and "/drafts/" in path and "/files/" in path))
    action = "下载文件" if download else {"POST": "新增或执行", "PUT": "修改", "PATCH": "修改", "DELETE": "删除"}.get(method, "读取")
    if not download:
        action = {"login": "密码登录", "logout": "退出设备", "logout-all": "退出全部设备", "password": "修改密码",
            "cancel": "停止任务", "notify": "发送结果", "turns": "追加需求", "answer": "回答确认", "restore": "恢复任务",
            "permanent": "永久删除", "run_now": "立即执行", "test": "验证连接", "apply": "更新连接", "share": "分享",
            "approve": "审批", "publish": "发布", "revoke": "撤销", "quarantine": "隔离", "rollback": "回滚"}.get(tail, action)
        if path == "/api/auth/refresh":
            action = "登录状态续期"
        if path.startswith("/api/config"):
            action = "恢复配置默认值" if method == "DELETE" else "更新配置"
        elif path.startswith("/api/confirm/"):
            action = {"email": "确认发送邮件", "slack": "确认发送 Slack", "db": "确认入库", "template": "确认保存模板"}.get(tail, "确认操作")
        elif path in ("/api/tasks", "/api/tasks/manual") and method == "POST":
            action = "创建计划"
        elif path == "/api/semantic-workspace/tasks" and method == "POST":
            action = "创建任务"
        elif path == "/api/admin/users" and method == "POST":
            action = "创建用户"
        elif path.startswith("/api/admin/users/") and method in ("PATCH", "PUT"):
            action = "修改用户"
        elif path == "/api/data-sources/uploads" and method == "POST":
            action = "上传文件"
        elif path.startswith("/api/semantic-workspace/tasks/") and "/revision-proposals/" in path and tail == "reject":
            action = "拒绝修订"
        elif path.startswith("/api/semantic-workspace/tasks/") and path.endswith("/draft/accept"):
            action = "接受初稿"
    identity = "模块:" + "/".join(parts[:2])
    if path.startswith("/api/downloads/") and len(parts) > 2:
        identity = "任务:" + parts[2]
    else:
        for prefix, kind in (("/api/semantic-workspace/tasks/", "任务"), ("/api/tasks/", "计划"),
            ("/api/admin/users/", "用户"), ("/api/model-connections/", "连接"),
            ("/api/data-sources/uploads/", "上传文件"), ("/api/templates/", "模板"), ("/api/lessons/", "经验"), ("/api/feedback/", "反馈")):
            if path.startswith(prefix):
                identity = kind + ":" + path.removeprefix(prefix).split("/")[0]
                break
    return action, hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24], download


class OperationsMiddleware:
    def __init__(self, app, store_provider=None):
        self.app = app
        self.store_provider = store_provider

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        method = scope.get("method", "")
        module = next((label for prefix, label in MODULE_PREFIXES if path.startswith(prefix)), None)
        action, object_ref, download = action_identity(path, method)
        # 下载属于审计操作而不是 PV；其他 GET 不登记为页面访问。
        if scope["type"] != "http" or (method not in ("POST", "PUT", "PATCH", "DELETE") and not download) or not module:
            return await self.app(scope, receive, send)
        from .auth import access_identity, get_store
        request = Request(scope)
        store = (self.store_provider or get_store)()
        actor = None
        try:
            actor, claims, _ = await run_in_threadpool(access_identity, request)
            request.state.platform_claims = claims
        except HTTPException:
            pass
        login = path == "/api/auth/login"
        if login:
            actor = None
        request.state.operations_changes = []
        target = None
        if path.startswith("/api/admin/users/"):
            target_id = path.removeprefix("/api/admin/users/").split("/")[0]
            user = await run_in_threadpool(store.admin_user, target_id)
            if user:
                target = {"user_id": user["user_id"], "role": user["role"]}

        def start():
            with store._conn() as conn:
                return ops.begin(conn, kind="login" if login else "action", module=module, action=action, actor=actor, object_ref=object_ref, metadata=ops.request_metadata(request))

        try:
            event = await run_in_threadpool(start)
        except Exception:
            # 登记失败时不执行有副作用的请求；不向日志输出配置对象或底层异常内容。
            return await JSONResponse({"detail": "审计记录暂不可用，本次请求未执行，请稍后重试"}, status_code=503)(scope, receive, send)
        started = time.monotonic()
        recorded = False

        def complete(status):
            current = getattr(request.state, "operations_login_actor", None) if login else getattr(request.state, "platform_user", None)
            changes = list(getattr(request.state, "operations_changes", ())) if status and status < 400 else []
            if reference := getattr(request.state, "operations_object_ref", None):
                changes.insert(0, {"context": {"object_ref": reference}})
            with store._conn() as conn:
                ops.finish(conn, event, actor=current or actor, subject=target,
                    result="unknown" if status is None or status == 202 else "success" if status < 400 else "failure",
                    status_code=status, duration_ms=round((time.monotonic() - started) * 1000),
                    changes=changes)

        async def audited_send(message):
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                try:
                    await run_in_threadpool(complete, message["status"])
                except Exception:
                    # 操作可能已完成，不能伪造失败诱导重试；持久请求仍明确为未知。
                    logger.error("运营请求结果写入失败；对应请求保留为未知")
                    message["headers"] = [*message.get("headers", []), (b"x-mangrove-audit", b"outcome-unknown")]
            await send(message)
        try:
            await self.app(scope, receive, audited_send)
        except Exception:
            if not recorded:
                try:
                    await run_in_threadpool(complete, None)
                except Exception:
                    logger.error("运营异常请求保留为未知")
            raise
