"""隔离控制台重放开发监督器退出链；不启动真实后台服务或访问业务库。"""
from __future__ import annotations

import atexit
import asyncio
import builtins
import ctypes
import json
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def worker(entrypoint: Path, folder: Path, phase: str) -> None:
    # 只对测试子进程关闭系统弹窗，保留原生退出码和 faulthandler，避免阻塞测试。
    ctypes.windll.kernel32.SetErrorMode(0x0002)
    import uvicorn

    atexit.register(lambda: (folder / "finalized").write_text("已清理", encoding="utf-8"))

    async def app(scope, receive, send):
        assert scope["type"] == "lifespan"
        assert (await receive())["type"] == "lifespan.startup"
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        assert await child.wait() == 0
        db = sqlite3.connect(folder / "transaction.db")
        db.execute("CREATE TABLE stopped (ok INTEGER)")
        db.execute("INSERT INTO stopped VALUES (1)")
        await send({"type": "lifespan.startup.complete"})
        (folder / "ready").write_text("已启动", encoding="utf-8")
        assert (await receive())["type"] == "lifespan.shutdown"
        await asyncio.sleep(0.02)
        db.commit()
        db.close()
        (folder / "shutdown").write_text("已收尾", encoding="utf-8")
        await send({"type": "lifespan.shutdown.complete"})

    original_run = uvicorn.run
    # 运行真实入口的信号装配和真实 Uvicorn，仅将业务 app 换成无外部副作用的 ASGI。
    def run_isolated(*args, **kwargs):
        assert "duckdb" in sys.modules
        return original_run(app, host="127.0.0.1", port=0, log_level="warning")

    uvicorn.run = run_isolated
    if phase == "startup":
        original_import = builtins.__import__

        def pause_native_import(name, *args, **kwargs):
            module = original_import(name, *args, **kwargs)
            if name == "duckdb":
                # 在真实业务模块导入时接收停止信号，覆盖服务尚未就绪的退出窗口。
                (folder / "ready").write_text("导入中", encoding="utf-8")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    time.sleep(0.02)
            return module

        builtins.__import__ = pause_native_import
    runpy.run_path(str(entrypoint), run_name="__main__")
    (folder / "returned").write_text("已返回", encoding="utf-8")


def controller(entrypoint: Path, folder: Path, phase: str) -> None:
    from scripts.dev_reload import _stop_backend

    with (folder / "worker.log").open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-X", "faulthandler", __file__,
             "worker", str(entrypoint), str(folder), phase],
            cwd=folder, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
        )
        try:
            deadline = time.monotonic() + 45
            while not (folder / "ready").exists() and proc.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("隔离服务启动超时")
                time.sleep(0.02)
            assert (folder / "ready").exists(), "隔离服务启动失败"
            time.sleep(0.15)
            _stop_backend(proc)
            code = proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
    print(json.dumps({"exit_code": code, "pid": proc.pid}), flush=True)


if __name__ == "__main__":
    action, entrypoint, folder, phase = sys.argv[1:]
    {"worker": worker, "controller": controller}[action](Path(entrypoint), Path(folder), phase)
