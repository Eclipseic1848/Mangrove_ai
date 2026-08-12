# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts import dev_reload


class _ExitedProcess:
    pid = 12345

    @staticmethod
    def poll() -> int:
        return 17


class _RunningProcess:
    pid = 23456

    @staticmethod
    def poll() -> None:
        return None


def test_ensure_backend_restarts_an_unexpectedly_exited_process(
    monkeypatch,
) -> None:
    """网关意外退出后，监督器必须自动恢复而不是静默失联。"""

    replacement = _RunningProcess()
    starts: list[bool] = []
    monkeypatch.setattr(
        dev_reload,
        "_start_backend",
        lambda: starts.append(True) or replacement,
    )
    monkeypatch.setattr(dev_reload, "_log", lambda message: None)

    result = dev_reload._ensure_backend(_ExitedProcess())

    assert result is replacement
    assert starts == [True]


def test_ensure_backend_keeps_a_healthy_process(monkeypatch) -> None:
    running = _RunningProcess()
    monkeypatch.setattr(
        dev_reload,
        "_start_backend",
        lambda: (_ for _ in ()).throw(AssertionError("不应重复启动")),
    )

    assert dev_reload._ensure_backend(running) is running
