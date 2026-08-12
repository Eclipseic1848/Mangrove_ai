"""
HITL 待确认动作的服务端暂存。

保存网关会话状态：聊天流跑完后，网关把本轮产生的"待确认"动作
（入库/邮件/Slack/沉淀模板/定时任务）连同所需数据暂存于此，按 (user_id, task_id) 索引；
前端点确认时只回传 task_id + 动作类型，网关取出执行，避免把大体量/敏感数据回传客户端。

单进程内存字典即可满足 v1（与 scheduler 同为单实例本地场景）。带容量上限防泄漏。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, Optional

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
        with self._lock:
            k = self._key(user_id, task_id)
            self._data[k] = pending
            self._data.move_to_end(k)
            while len(self._data) > _MAX_ENTRIES:
                self._data.popitem(last=False)

    def get(self, user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._data.get(self._key(user_id, task_id))

    def pop_action(self, user_id: str, task_id: str, action: str) -> Optional[Dict[str, Any]]:
        """取出并移除某任务下指定动作的数据（确认后调用，防重复执行）。"""
        with self._lock:
            entry = self._data.get(self._key(user_id, task_id))
            if not entry:
                return None
            return entry.pop(action, None)


# 进程内单例
pending_store = PendingStore()
