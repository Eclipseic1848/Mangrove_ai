# -*- coding: utf-8 -*-
"""使用真实本地 Qwen 和 Pi 工具循环验证最小生产纵切面。"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agentic_runtime.models import (
    PermissionProfile,
    PiRuntimeRequest,
    RuntimeEvent,
    SourceInput,
    VerificationStatus,
)
from src.agentic_runtime.pi_runtime import PiRuntime
from src.config.settings import settings


async def _run() -> None:
    source = (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "semantic_harness"
        / "public"
        / "batch0"
        / "workload_filter"
        / "source.csv"
    ).resolve()
    smoke_root = PROJECT_ROOT / ".pytest-tmp" / "pi-runtime-live"
    smoke_root.mkdir(parents=True, exist_ok=True)
    execution_root = Path(
        tempfile.mkdtemp(prefix="run-", dir=smoke_root)
    )
    request = PiRuntimeRequest(
        user_id="live-smoke-admin",
        task_id="workspace_pi_live_smoke",
        revision=1,
        objective_text=(
            "读取来源 CSV，只保留姓名为“示例人员乙”的原始记录，"
            "不要汇总其他人员，只输出一张 CSV，其他内容都不要。"
        ),
        requested_output_formats=("csv",),
        sources=(
            SourceInput(
                upload_id="fixture-source",
                original_name="工作量明细.csv",
                host_path=source,
                sha256=hashlib.sha256(
                    source.read_bytes()
                ).hexdigest(),
                media_type="text/csv",
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
        model=settings.llm_model_name,
        base_url=settings.llm_base_url,
        api_key="local-runtime",
    )

    async def event_sink(event: RuntimeEvent) -> None:
        print(f"{event.event_type}: {event.summary}", flush=True)

    result = await PiRuntime(
        execution_root=execution_root,
        timeout_seconds=300,
    ).start(request, on_event=event_sink)
    if len(result.candidates) != 1:
        raise AssertionError(
            f"必须只生成一个候选文件，实际 {len(result.candidates)}"
        )
    candidate = result.candidates[0]
    with candidate.host_path.open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 2:
        raise AssertionError(
            f"必须保留两条示例人员乙记录，实际 {len(rows)} 条"
        )
    if any(row.get("姓名") != "示例人员乙" for row in rows):
        raise AssertionError("候选 CSV 混入了其他人员")
    if (
        result.verification is None
        or result.verification.status is not VerificationStatus.PASSED
    ):
        raise AssertionError(
            "候选必须通过独立来源与语义验证："
            + (
                result.verification.model_dump_json()
                if result.verification
                else "未生成验证报告"
            )
        )
    trace = json.loads(
        (result.workspace_root / "trace" / "docker-command.json").read_text(
            encoding="utf-8",
        )
    )
    argv = tuple(trace["argv"])
    if trace.get("egress_phase") != "business_execution":
        raise AssertionError("真实主链没有记录业务 Egress 阶段")
    if "--network" not in argv or not any(
        str(value).startswith(
            "HTTPS_PROXY=http://mangrove-pi-proxy-"
        )
        for value in argv
    ):
        raise AssertionError("真实 Pi 容器没有强制接入任务级代理")
    egress_log = (
        result.workspace_root
        / "trace"
        / "egress-business"
        / "egress.log"
    ).read_text(encoding="utf-8")
    if "CANONICAL-PROXY-DECISION" not in egress_log:
        raise AssertionError("真实主链没有保留代理准入日志")
    network_name = argv[argv.index("--network") + 1]
    inspect = await asyncio.create_subprocess_exec(
        "docker",
        "network",
        "inspect",
        network_name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await inspect.wait()
    if inspect.returncode == 0:
        raise AssertionError("任务结束后仍残留 Egress 网络")
    print(f"PASS: {candidate.host_path}", flush=True)


if __name__ == "__main__":
    asyncio.run(_run())
