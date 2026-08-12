# -*- coding: utf-8 -*-
"""用真实 Docker 网络验证 Pi Egress PolicyGate。"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agentic_runtime.egress_policy import (
    EgressPolicy,
    SmokescreenEgressController,
)
from src.config.settings import settings


async def _docker(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _wait_running(container_name: str) -> None:
    for _ in range(80):
        code, stdout, _ = await _docker(
            "inspect",
            "--format",
            "{{.State.Running}}",
            container_name,
        )
        if code == 0 and stdout.strip() == "true":
            return
        await asyncio.sleep(0.25)
    raise AssertionError("Smokescreen sidecar 未进入运行态")


async def _probe(
    *,
    network_name: str,
    proxy_url: str,
    shell: str,
) -> tuple[int, str, str]:
    env_args: list[str] = []
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    ):
        env_args.extend(("--env", f"{key}={proxy_url}"))
    env_args.extend(("--env", "NODE_USE_ENV_PROXY=1"))
    return await _docker(
        "run",
        "--rm",
        "--network",
        network_name,
        *env_args,
        settings.pi_runtime_image,
        "sh",
        "-lc",
        shell,
    )


async def _assert_removed(name: str, *, kind: str) -> None:
    command = ("network", "inspect", name) if kind == "network" else (
        "inspect",
        name,
    )
    code, _, _ = await _docker(*command)
    if code == 0:
        raise AssertionError(f"Egress {kind} 未清理：{name}")


async def _run(egress_image: str) -> None:
    evidence_root = PROJECT_ROOT / ".pytest-tmp" / "pi-runtime-pg05-egress"
    evidence_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(
        tempfile.mkdtemp(prefix="run-", dir=evidence_root)
    )
    controller = SmokescreenEgressController(image=egress_image)
    container_base_url = settings.llm_base_url.rstrip("/")

    dependency_lease = await controller.start(
        policy=EgressPolicy.for_dependency_acquisition(
            model_base_url=container_base_url,
        ),
        user_id="pg05-egress-user",
        task_id="pg05-egress-dependency",
        revision=1,
        run_id="pi_run_dependency_probe",
        policy_dir=run_root / "dependency-policy",
    )
    try:
        await _wait_running(dependency_lease.proxy_container_name)
        code, stdout, stderr = await _probe(
            network_name=dependency_lease.network_name,
            proxy_url=dependency_lease.proxy_url,
            shell=(
                "curl -fsS --max-time 30 "
                "https://registry.npmjs.org/npm/latest >/dev/null"
            ),
        )
        if code != 0:
            raise AssertionError(
                f"批准的 npm 依赖出口失败：{stdout}{stderr}"
            )
        for label, command in (
            (
                "未批准域名",
                "curl -fsS --max-time 8 https://example.com >/dev/null",
            ),
            (
                "云元数据地址",
                "curl -fsS --max-time 8 "
                "http://169.254.169.254/latest/meta-data/ >/dev/null",
            ),
            (
                "直连旁路",
                "curl --noproxy '*' -fsS --max-time 8 "
                "https://registry.npmjs.org/npm/latest >/dev/null",
            ),
        ):
            code, _, _ = await _probe(
                network_name=dependency_lease.network_name,
                proxy_url=dependency_lease.proxy_url,
                shell=command,
            )
            if code == 0:
                raise AssertionError(f"{label}没有被 Egress PolicyGate 阻断")
    finally:
        await controller.stop(dependency_lease)
    await _assert_removed(
        dependency_lease.proxy_container_name,
        kind="container",
    )
    await _assert_removed(dependency_lease.network_name, kind="network")

    business_lease = await controller.start(
        policy=EgressPolicy.for_business_execution(
            model_base_url=container_base_url,
        ),
        user_id="pg05-egress-user",
        task_id="pg05-egress-business",
        revision=1,
        run_id="pi_run_business_probe",
        policy_dir=run_root / "business-policy",
    )
    try:
        await _wait_running(business_lease.proxy_container_name)
        model_url = f"{container_base_url}/models"
        code, stdout, stderr = await _probe(
            network_name=business_lease.network_name,
            proxy_url=business_lease.proxy_url,
            shell=(
                "node -e \"fetch(process.argv[1]).then(r=>{"
                "if(!r.ok)process.exit(2);console.log(r.status)"
                "}).catch(e=>{console.error(e);process.exit(3)})\" "
                f"'{model_url}'"
            ),
        )
        if code != 0 or "200" not in stdout:
            raise AssertionError(
                f"业务阶段无法通过代理访问本地模型：{stdout}{stderr}"
            )
        code, _, _ = await _probe(
            network_name=business_lease.network_name,
            proxy_url=business_lease.proxy_url,
            shell=(
                "curl -fsS --max-time 8 "
                "https://registry.npmjs.org/npm/latest >/dev/null"
            ),
        )
        if code == 0:
            raise AssertionError("业务阶段仍可访问公共依赖站点")
    finally:
        await controller.stop(business_lease)
    await _assert_removed(
        business_lease.proxy_container_name,
        kind="container",
    )
    await _assert_removed(business_lease.network_name, kind="network")

    logs = (
        (dependency_lease.policy_dir / "egress.log").read_text(
            encoding="utf-8",
            errors="replace",
        )
        + (business_lease.policy_dir / "egress.log").read_text(
            encoding="utf-8",
            errors="replace",
        )
    )
    if "CANONICAL-PROXY-DECISION" not in logs:
        raise AssertionError("代理日志缺少结构化准入决定")
    print(
        f"PG05_EGRESS_OK evidence={run_root}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--egress-image",
        default="mangrove/smokescreen:da4840c9",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.egress_image))


if __name__ == "__main__":
    main()
