"""
HITL 待确认动作的服务端暂存。

保存网关会话状态：聊天流跑完后，网关把本轮产生的"待确认"动作
（入库/邮件/Slack/沉淀模板/定时任务）连同所需数据暂存于此，按 (user_id, task_id) 索引；
前端点确认时只回传 task_id + 动作类型，网关取出执行，避免把大体量/敏感数据回传客户端。

单进程内存字典即可满足 v1（与 scheduler 同为单实例本地场景）。带容量上限防泄漏。
"""
from __future__ import annotations

import threading
import hashlib
import time
from contextlib import contextmanager
from copy import deepcopy
from filelock import Timeout
from collections import OrderedDict
from typing import Any, Dict, Optional
from src import account_execution as execution

_MAX_ENTRIES = 500  # 超过则淘汰最早的（LRU 近似），防止内存无限增长


class PendingStore:
    def __init__(self) -> None:
        self._data: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(user_id: str, task_id: str) -> str:
        return f"{user_id}::{task_id}"

    def put(self, user_id: str, task_id: str, pending: Dict[str, Any]) -> None:
        """暂存某任务的全部待确认动作（pending 形如 {"db": {...}, "email": {...}}）。"""
        from src.api.auth import get_store
        auth = execution.current_authorization()
        if auth.owner_user_id != user_id or not task_id or set(pending) - {'db', 'email', 'slack', 'template', 'schedule'}:
            raise execution.ExecutionDenied('待确认动作缺少可信身份')
        store = get_store()
        with self._lock, store.account_execution_transaction(auth) as conn:
            frozen = {}
            for action, payload in pending.items():
                resource_id = self._resource_id(task_id, action)
                # 领取后或进程中断的动作不能通过再次 put 复活；新任务须使用新身份。
                if execution._binding(conn, user_id, 'chat', resource_id) is not None:
                    raise execution.ExecutionDenied('待确认动作已经注册，不能重放')
                execution.bind_execution(conn, auth, 'chat', resource_id, state='idle', now=time.time())
                frozen[action] = (auth, deepcopy(payload))
            k = self._key(user_id, task_id)
            self._data[k] = frozen
            self._data.move_to_end(k)
            while len(self._data) > _MAX_ENTRIES:
                self._data.popitem(last=False)

    def get(self, user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._data.get(self._key(user_id, task_id))
            return {action: deepcopy(value[1]) for action, value in entry.items()} if entry is not None else None

    @staticmethod
    def _resource_id(task_id: str, action: str) -> str:
        return 'confirm:' + hashlib.sha256((task_id + '\0' + action).encode('utf-8')).hexdigest()

    @contextmanager
    def claim_action(self, user_id: str, task_id: str, action: str):
        """SQL 只覆盖领取；文件锁跨真实动作，未知结果不允许自动重放。"""
        from src.api.auth import get_store
        from src.api.execution import execution_lock
        auth = execution.current_authorization()
        if auth.owner_user_id != user_id:
            raise execution.ExecutionDenied('待确认动作 Owner 不匹配')
        store = get_store()
        resource_id = self._resource_id(task_id, action)
        lease = execution_lock(store, user_id, 'chat', resource_id)
        try:
            lease.acquire(timeout=0)
        except Timeout as exc:
            raise execution.ExecutionDenied('待确认动作正在执行，请核对状态') from exc
        try:
            with self._lock, store.account_execution_transaction(auth) as conn:
                entry = self._data.get(self._key(user_id, task_id), {})
                frozen = entry.get(action)
                if frozen is None:
                    payload = None
                else:
                    binding = execution.require_binding(conn, auth, 'chat', resource_id)
                    if frozen[0] != auth or binding['state'] != 'idle':
                        raise execution.ExecutionDenied('待确认动作授权已变化或停止未知')
                    execution.set_execution_state(conn, auth, 'chat', resource_id, state='active', now=time.time())
                    payload = entry.pop(action)[1]
            if payload is None:
                yield None
                return
            try:
                yield payload
            except BaseException:
                store.confirm_account_execution_stopped(user_id, 'chat', resource_id, auth.generation, cleanup_failed=True)
                raise
            else:
                try:
                    store.set_account_execution_state(auth, 'chat', resource_id, 'idle')
                except execution.ExecutionDenied:
                    # 业务已正常返回，可确认本地动作停止；迟到结果仍不回传。
                    store.confirm_account_execution_stopped(user_id, 'chat', resource_id, auth.generation)
                    raise

        finally:
            lease.release()


# 进程内单例
pending_store = PendingStore()
