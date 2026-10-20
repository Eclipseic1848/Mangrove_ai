"""向本机运维写排空回执；不新增 HTTP 运维权限或自动修复生产状态。"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import re
import time
import uuid

from src.services.deployment_drain import DeploymentGate
from src.services.deployment_preflight import DrainBlocker, collect_persisted_blockers

_LOGGER = logging.getLogger(__name__)


def collect_resource_blockers(execution_root: Path) -> tuple[DrainBlocker, ...]:
    """只查执行器的固定账本与 Host 目录，不递归扫描上传或业务文件。"""
    pending = 0
    try:
        execution_root = execution_root.resolve(strict=True)
        if not execution_root.is_dir():
            raise ValueError("missing_execution_root")
        for runtime_root in (execution_root, execution_root / "capability-validations"):
            ledger = runtime_root / ".pi-lifecycle"
            if ledger.is_symlink():
                raise ValueError("linked_ledger")
            if not ledger.exists():
                continue
            for entry in ledger.iterdir():
                if entry.is_symlink() or not entry.is_dir() or re.fullmatch(r"[0-9a-f]{64}", entry.name) is None:
                    raise ValueError("invalid_ledger")
                # 任一未收口账本即阻断，避免把没有 .creating 的未知资源当成已清理。
                if any((entry / name).exists() or (entry / name).is_symlink() for name in ("resources.json", "resources.tmp")):
                    pending += 1
        for host_root in (execution_root / "capability-hosts", execution_root / "capability-validations"):
            if host_root.is_symlink():
                raise ValueError("linked_host_root")
            if host_root.exists():
                for entry in host_root.iterdir():
                    if re.fullmatch(r"[0-9a-f]{12}", entry.name):
                        if entry.is_symlink():
                            raise ValueError("linked_host")
                        marker = entry / ".creating"
                        if marker.exists() or marker.is_symlink():
                            pending += 1
    except (OSError, ValueError):
        return (DrainBlocker("resources.unreadable", None, "managed_resource_state_unreadable"),)
    return (DrainBlocker("resources.unconfirmed", pending, "managed_resources_not_confirmed_stopped"),) if pending else ()


def write_private_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def publish_drain_report(
    gate: DeploymentGate, *, webui_db: Path, scheduler_db: Path,
    execution_root: Path, runtime_root: Path, stopping: asyncio.Event,
) -> None:
    process_token = uuid.uuid4().hex
    while not stopping.is_set():
        first = gate.snapshot()
        if first["phase"] != "open":
            try:
                blockers = ()
                if not first["total_active"]:
                    blockers = await asyncio.to_thread(collect_persisted_blockers, webui_db, scheduler_db)
                    blockers += await asyncio.to_thread(collect_resource_blockers, execution_root)
                final = gate.snapshot()
                stable = (first["phase"], first["request_id"]) == (final["phase"], final["request_id"])
                report = {
                    "version": 1, **final, "pid": os.getpid(), "process_token": process_token,
                    "runtime_root": str(runtime_root.resolve()), "updated_at": time.time(),
                    "blockers": [asdict(item) for item in blockers],
                    "quiet": stable and final["phase"] in {"draining", "sealed"}
                    and not first["total_active"] and not final["total_active"] and not blockers,
                }
                # 仅运维已创建的目录内落盘；普通启动和普通请求不创建控制文件。
                write_private_json(gate.state_dir / "status.json", report)
            except Exception:
                # 失败保留旧回执；运维必须同时校验 nonce、阶段、PID 和新鲜度。
                _LOGGER.error("部署排空回执写入失败，禁止据旧回执激活")
        try:
            await asyncio.wait_for(stopping.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass
