# -*- coding: utf-8 -*-
from __future__ import annotations

from scripts import dev_reload
from types import SimpleNamespace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest


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


@pytest.mark.parametrize("failure", [None, "timeout", "signal"])
def test_windows_shutdown_gives_owned_group_a_graceful_window(monkeypatch, failure):
    events = []

    class Process:
        pid = 123
        def poll(self): return None
        def send_signal(self, value):
            events.append("signal")
            if failure == "signal":
                raise OSError("signal unavailable")
        def wait(self, timeout):
            events.append("wait")
            assert 0 < timeout <= 10
            if failure == "timeout":
                raise subprocess.TimeoutExpired("owned-backend", timeout)

    monkeypatch.setattr(dev_reload, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(dev_reload, "signal", SimpleNamespace(CTRL_BREAK_EVENT=1))
    monkeypatch.setattr(dev_reload.subprocess, "run", lambda command, **kwargs: events.append(command))
    dev_reload._stop_backend(Process())
    assert events[0] == "signal"
    if failure is None:
        assert events == ["signal", "wait"]
    else:
        assert events[-1] == ["taskkill", "/PID", "123", "/T", "/F"]


def test_shutdown_does_nothing_for_exited_process(monkeypatch):
    monkeypatch.setattr(dev_reload.subprocess, "run", lambda *args, **kwargs: pytest.fail("已退出进程不能再清理"))
    dev_reload._stop_backend(_ExitedProcess())


def test_posix_shutdown_preserves_terminate_and_bounded_wait(monkeypatch):
    events = []
    process = SimpleNamespace(poll=lambda: None, terminate=lambda: events.append("terminate"),
                              wait=lambda timeout: events.append(timeout))
    monkeypatch.setattr(dev_reload, "os", SimpleNamespace(name="posix"))
    dev_reload._stop_backend(process)
    assert events == ["terminate", 10]


@pytest.mark.skipif(os.name != "nt", reason="真实 Windows 控制台信号回归")
@pytest.mark.parametrize("phase", ["serving", "startup"])
def test_windows_backend_signal_preserves_shutdown_and_python_cleanup(tmp_path, phase):
    """实际入口加载 DuckDB 后仍须正常收尾，不能在 SIGBREAK 重放时原生崩溃。"""
    root = Path(__file__).resolve().parents[1]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    for index in range(3 if phase == "serving" else 1):
        folder = tmp_path / str(index)
        folder.mkdir()
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(root / "tests/fixtures/windows_backend_shutdown.py"),
             "controller", str(root / "src/api/main.py"), str(folder), phase],
            cwd=folder, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
            capture_output=True, text=True, encoding="utf-8", timeout=65,
        )
        log = (folder / "worker.log").read_text(encoding="utf-8")
        assert result.returncode == 0, result.stderr + log
        exit_code = json.loads(result.stdout)["exit_code"]
        assert (folder / "finalized").exists(), log
        if phase == "startup":
            assert exit_code & 0xffffffff in (130, 0xc000013a), log
            assert not (folder / "transaction.db").exists()
            continue
        assert exit_code == 0, log
        assert all((folder / name).exists() for name in ("shutdown", "returned", "finalized"))
        with sqlite3.connect(folder / "transaction.db") as db:
            assert db.execute("SELECT ok FROM stopped").fetchall() == [(1,)]
        assert not (folder / "transaction.db-journal").exists()
