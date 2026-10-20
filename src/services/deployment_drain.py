"""单进程部署排空门；维护文件只控制接单，不撤销已有执行身份。"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path


class Lease:
    def __init__(self, gate: DeploymentGate, kind: str):
        self._gate = gate
        self._kind = kind
        self._released = False

    def release(self) -> None:
        with self._gate._lock:
            if not self._released:
                self._released = True
                remaining = self._gate._active[self._kind] - 1
                if remaining:
                    self._gate._active[self._kind] = remaining
                else:
                    del self._gate._active[self._kind]


class DeploymentGate:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.request_path = self.state_dir / "drain.json"
        self._lock = threading.Lock()
        self._active: dict[str, int] = {}

    def _request(self) -> dict:
        try:
            if self.request_path.is_symlink():
                raise ValueError("linked_request")
            with self.request_path.open("rb") as handle:
                raw = handle.read(4097)
            if len(raw) > 4096:
                raise ValueError("oversized_request")
            request = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(request, dict)
                or set(request) != {"version", "request_id", "phase"}
                or type(request["version"]) is not int
                or request["version"] != 1
                or not isinstance(request["request_id"], str)
                or re.fullmatch(r"[0-9a-f]{32}", request["request_id"]) is None
                or request["phase"] not in ("draining", "sealed")
            ):
                raise ValueError("invalid_request")
            return {**request, "error": None}
        except FileNotFoundError:
            return {"phase": "open", "request_id": None, "error": None}
        except (OSError, ValueError, TypeError, RecursionError):
            # 标记损坏不能变成重新接单；错误只报告分类，不泄露文件内容。
            return {"phase": "invalid", "request_id": None, "error": "drain_request_unreadable"}

    def try_acquire(self, kind: str, continuation: bool = False) -> Lease | None:
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", kind) is None:
            raise ValueError("invalid_admission_kind")
        with self._lock:
            phase = self._request()["phase"]
            if phase != "open" and not (continuation and phase in ("draining", "invalid")):
                return None
            self._active[kind] = self._active.get(kind, 0) + 1
            return Lease(self, kind)

    @contextmanager
    def admission(self, kind: str, continuation: bool = False):
        lease = self.try_acquire(kind, continuation=continuation)
        try:
            yield lease is not None
        finally:
            if lease is not None:
                lease.release()

    def is_draining(self) -> bool:
        with self._lock:
            return self._request()["phase"] != "open"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                **self._request(),
                "active": dict(self._active),
                "total_active": sum(self._active.values()),
            }


_gate: DeploymentGate | None = None


def get_deployment_gate() -> DeploymentGate:
    global _gate
    if _gate is None:
        from src.config.settings import settings

        _gate = DeploymentGate(Path(settings.webui_db_path).resolve().parent / ".deployment-control")
    return _gate
