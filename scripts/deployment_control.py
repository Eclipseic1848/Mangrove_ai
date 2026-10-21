"""本机单进程维护控制；激活、备份及代码回退按部署手册独立执行。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.deployment_drain import DeploymentGate
from src.services.deployment_runtime import write_private_json


def _require_running_process(pid: int, runtime_root: Path) -> None:
    if sys.platform != "linux":
        raise ValueError("生产进程核验仅支持 Linux；不得在 Windows 发送探测信号")
    process = Path("/proc") / str(pid)
    state = (process / "stat").read_text(encoding="utf-8").rpartition(") ")[2].split()
    if not state or state[0] in {"Z", "X", "x"} or (process / "cwd").resolve(strict=True) != runtime_root.resolve(strict=True):
        raise ValueError("进程已结束或未从预期源码根运行")


def checked_report(gate: DeploymentGate, request_id: str, pid: int, runtime_root: Path) -> dict:
    request = gate.snapshot()
    if request["phase"] not in {"draining", "sealed"} or request["request_id"] != request_id:
        raise ValueError("维护请求不匹配")
    status_path = gate.state_dir / "status.json"
    if status_path.is_symlink():
        raise ValueError("拒绝重定向维护回执")
    with status_path.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("维护回执过大")
    report = json.loads(raw.decode("utf-8"))
    if (
        report.get("version") != 1 or report.get("request_id") != request_id
        or report.get("phase") != request["phase"] or report.get("pid") != pid or pid <= 0
        or not re.fullmatch(r"[0-9a-f]{32}", report.get("process_token", ""))
        or Path(report.get("runtime_root", "")).resolve() != runtime_root.resolve(strict=True)
        or not isinstance(report.get("updated_at"), (int, float))
        or not 0 <= time.time() - report["updated_at"] <= 10
    ):
        raise ValueError("维护回执身份、阶段或新鲜度不符")
    _require_running_process(pid, runtime_root)
    return report


def require_quiet(report: dict) -> None:
    if report.get("quiet") is not True or report.get("total_active") != 0 or report.get("active") != {} or report.get("blockers") != [] or report.get("error") is not None:
        raise ValueError("活动执行或停止未确认，禁止推进")


def control(command: str, state_dir: Path, *, request_id: str | None = None, pid: int | None = None, runtime_root: Path | None = None) -> dict:
    if state_dir.is_symlink():
        raise ValueError("维护控制目录不能是链接")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix" and state_dir.stat().st_mode & 0o022:
        raise ValueError("维护控制目录不能允许组或其他用户写入")
    gate = DeploymentGate(state_dir)
    with FileLock(state_dir / "operator.lock", timeout=0):
        state = gate.snapshot()
        if command == "request":
            if state["phase"] != "open":
                raise ValueError("已有维护请求，不能覆盖")
            request_id = uuid.uuid4().hex
            write_private_json(gate.request_path, {"version": 1, "request_id": request_id, "phase": "draining"})
            return {"request_id": request_id, "phase": "draining"}
        if not request_id or state["request_id"] != request_id:
            raise ValueError("维护请求不匹配")
        if command == "cancel":
            if state["phase"] != "draining":
                raise ValueError("封口后须完成版本核验再 resume")
            # 取消更新只恢复接单；不触碰执行、失败回执或资源账本。
            gate.request_path.unlink()
            return {"request_id": request_id, "phase": "open"}
        if pid is None or runtime_root is None:
            raise ValueError("必须提供 systemd 主进程和预期源码根")
        report = checked_report(gate, request_id, pid, runtime_root)
        if command == "status":
            return report
        require_quiet(report)
        if command == "seal":
            if state["phase"] != "draining":
                raise ValueError("仅 draining 可以封口")
            write_private_json(gate.request_path, {"version": 1, "request_id": request_id, "phase": "sealed"})
            return {"request_id": request_id, "phase": "sealed", "requires_fresh_sealed_report": True}
        if command == "resume":
            if state["phase"] != "sealed":
                raise ValueError("仅已核验的 sealed 进程可以恢复接单")
            gate.request_path.unlink()
            return {"request_id": request_id, "phase": "open"}
        raise ValueError("不支持的维护命令")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("request", "status", "seal", "resume", "cancel"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--request-id")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--runtime-root", type=Path)
    args = parser.parse_args()
    try:
        result = control(args.command, args.state_dir, request_id=args.request_id, pid=args.pid, runtime_root=args.runtime_root)
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "detail": "维护检查未通过；保留现场，禁止切换"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
