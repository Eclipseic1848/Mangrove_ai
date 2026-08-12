"""开发环境热更新入口：监听项目文件并重启单进程 FastAPI 网关。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from watchfiles import Change, DefaultFilter, watch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHED_ROOTS = (PROJECT_ROOT / "src", PROJECT_ROOT / ".env", PROJECT_ROOT / "requirements.txt")
LOG_PATH = PROJECT_ROOT / "logs" / "dev_reload.log"


class MangroveReloadFilter(DefaultFilter):
    """只监听后端源码、环境配置与依赖清单，避免无关文件触发重启。"""

    def __call__(self, change: Change, path: str) -> bool:
        candidate = Path(path)
        return candidate.suffix == ".py" or candidate.name in {".env", "requirements.txt"}


def _log(message: str) -> None:
    """同时写控制台和 UTF-8 日志，保留运行期退出证据。"""

    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        # 日志故障不能反过来终止核心服务监督器。
        print(f"[Mangrove 热更新] 日志写入失败：{exc}", flush=True)


def _start_backend() -> subprocess.Popen:
    """以普通单进程模式启动网关，保留 Windows 的 Proactor 子进程能力。"""
    kwargs = {"cwd": str(PROJECT_ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([sys.executable, "-m", "src.api.main"], **kwargs)


def _stop_backend(proc: subprocess.Popen) -> None:
    """停止网关及其子进程，防止热更新后留下采集子进程。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], check=False)
    else:
        proc.terminate()
        proc.wait(timeout=10)


def _ensure_backend(proc: subprocess.Popen) -> subprocess.Popen:
    """网关意外退出时自动拉起；正常运行则保持原进程。"""

    exit_code = proc.poll()
    if exit_code is None:
        return proc
    _log(
        f"[Mangrove 监督] 后端进程 {proc.pid} 意外退出"
        f"（退出码 {exit_code}），正在自动恢复"
    )
    return _start_backend()


def main() -> None:
    """启动网关并在受监控文件变更后重启。"""
    _log("[Mangrove 热更新] 监听 src/**/*.py、.env、requirements.txt")
    proc = _start_backend()
    try:
        while True:
            try:
                for changes in watch(
                    *WATCHED_ROOTS,
                    watch_filter=MangroveReloadFilter(),
                    rust_timeout=1000,
                    yield_on_timeout=True,
                ):
                    if not changes:
                        proc = _ensure_backend(proc)
                        continue
                    changed = ", ".join(
                        sorted({Path(path).name for _, path in changes})
                    )
                    _log(
                        f"[Mangrove 热更新] 检测到变更：{changed}，正在重启网关"
                    )
                    _stop_backend(proc)
                    proc = _start_backend()
                _log("[Mangrove 监督] 文件监听意外结束，正在恢复监听")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                # 临时文件监听故障不应带走已经运行的网关。
                _log(f"[Mangrove 监督] 文件监听异常：{exc}；1 秒后重试")
                proc = _ensure_backend(proc)
                time.sleep(1)
    except KeyboardInterrupt:
        _log("[Mangrove 热更新] 已停止")
    finally:
        _stop_backend(proc)


if __name__ == "__main__":
    main()
