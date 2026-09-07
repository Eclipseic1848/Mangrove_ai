"""平台执行安全点与原子线程收口；复用固定执行绑定和现有文件锁。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import hashlib
from pathlib import Path

from filelock import FileLock

from src import account_execution as execution

_authorizer = ContextVar("execution_authorizer", default=None)


@contextmanager
def execution_validation(authorizer):
    """显式数据库执行沿用原校验器；线程与嵌套 Provider 不借用 API 全局库。"""
    if not callable(authorizer):
        raise ValueError("执行授权校验器必须可调用")
    token = _authorizer.set(authorizer)
    try:
        yield
    finally:
        _authorizer.reset(token)


def execution_checkpoint(*, required: bool = False) -> None:
    auth = execution.current_authorization(required=required)
    if auth is not None:
        authorizer = _authorizer.get()
        if authorizer is None:
            from src.api.auth import get_store
            authorizer = get_store().require_account_authorization
        authorizer(auth)


def execution_http_checkpoint(_message) -> None:
    # 客户端可缓存；每次真正发包及返回时检查当前执行，覆盖 SDK 内部重试。
    execution_checkpoint()


async def execution_http_checkpoint_async(_message) -> None:
    execution_checkpoint()


async def execution_to_thread(func, /, *args, **kwargs):
    """取消 await 不等于线程已停止；释放执行身份前必须等待原子动作收口。"""
    execution_checkpoint()
    def invoke():
        # 线程池可能排队；取得真实线程后仍须核对原代数，才能开始原子动作。
        execution_checkpoint()
        return func(*args, **kwargs)

    pending = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        result = await asyncio.shield(pending)
    except asyncio.CancelledError:
        # 多次取消也不能提前释放锁或宣称资源已静默。
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
        if not pending.cancelled():
            pending.exception()
        raise
    execution_checkpoint()
    return result


def execution_lock(store, owner: str, kind: str, resource_id: str) -> FileLock:
    identity = "\0".join((str(Path(store.db_path).resolve()), owner, kind, resource_id))
    directory = Path(store.db_path).parent / ".account-execution-locks"
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(directory / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".lock"), timeout=0)


@asynccontextmanager
async def running_execution(store, kind: str, resource_id: str):
    auth = execution.current_authorization()
    with execution_lock(store, auth.owner_user_id, kind, resource_id):
        store.require_account_execution(auth, kind, resource_id)
        store.set_account_execution_state(auth, kind, resource_id, "active")
        try:
            yield auth
            store.set_account_execution_state(auth, kind, resource_id, "idle")
        except execution.ExecutionDenied:
            store.confirm_account_execution_stopped(auth.owner_user_id, kind, resource_id, auth.generation)
            raise
        except BaseException:
            # 未知异常可能来自关闭资源，保留失败事实，不能按 Python 栈退出宣告静默。
            store.confirm_account_execution_stopped(auth.owner_user_id, kind, resource_id, auth.generation, cleanup_failed=True)
            raise
