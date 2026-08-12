# -*- coding: utf-8 -*-
"""把任务网络授权编译为 Smokescreen 的失败关闭配置。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
import hashlib
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    """Docker CLI 边界的最小可测试结果。"""

    returncode: int
    stdout: str
    stderr: str


DockerCommandRunner = Callable[
    [tuple[str, ...]],
    Awaitable[DockerCommandResult],
]


@dataclass(frozen=True, slots=True)
class EgressLease:
    """当前 Run 独占的网络和代理身份。"""

    phase: EgressPhase
    network_name: str
    proxy_container_name: str
    proxy_url: str
    policy_dir: Path


class EgressPhase(str, Enum):
    """同一任务中互斥的网络阶段。"""

    DEPENDENCY_ACQUISITION = "dependency_acquisition"
    BUSINESS_EXECUTION = "business_execution"


_DEPENDENCY_DOMAINS = (
    "github.com",
    "*.github.com",
    "githubusercontent.com",
    "*.githubusercontent.com",
    "registry.npmjs.org",
    "*.npmjs.org",
    "pypi.org",
    "*.pypi.org",
    "pythonhosted.org",
    "*.pythonhosted.org",
    "deb.debian.org",
    "security.debian.org",
)


def _normalized_host(value: str) -> str:
    host = value.strip().rstrip(".").lower()
    if not host or any(char in host for char in "/:@"):
        raise ValueError(f"无效的 Egress 目标主机：{value!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        host = host.encode("idna").decode("ascii")
    return host


def _local_model_destination(
    model_base_url: str,
) -> tuple[str, tuple[str, ...]]:
    parsed = urlsplit(model_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("本地模型地址必须是有效的 HTTP(S) URL")
    host = _normalized_host(parsed.hostname)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Smokescreen 默认阻断解析后的私网地址。首期只接受固定 LAN IP，
        # 避免为了 localhost/域名而放开整段私网；动态 Relay 留待后续门。
        raise ValueError(
            "受控 Egress 当前要求本地模型使用固定局域网 IP；"
            "域名或外部模型必须单独授权"
        )
    else:
        if not (address.is_private or address.is_loopback):
            raise ValueError(
                "业务阶段只允许本地或局域网模型；外部模型必须单独授权"
            )
        allow_addresses = (f"{host}:{port}",)
    return host, allow_addresses


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """冻结一次容器阶段可访问的目标集合。"""

    phase: EgressPhase
    allowed_domains: tuple[str, ...]
    allow_addresses: tuple[str, ...]
    mount_sources: bool

    @classmethod
    def for_dependency_acquisition(
        cls,
        *,
        model_base_url: str,
        additional_domains: tuple[str, ...] = (),
    ) -> "EgressPolicy":
        """依赖下载可以联网，但绝不能同时看到用户来源。"""

        host, allow_addresses = _local_model_destination(model_base_url)
        domains = list(_DEPENDENCY_DOMAINS)
        for value in additional_domains:
            domain = _normalized_host(value)
            if domain not in domains:
                domains.append(domain)
        return cls(
            phase=EgressPhase.DEPENDENCY_ACQUISITION,
            allowed_domains=(*domains, host),
            allow_addresses=allow_addresses,
            mount_sources=False,
        )

    @classmethod
    def for_business_execution(
        cls,
        *,
        model_base_url: str,
        additional_base_urls: tuple[str, ...] = (),
    ) -> "EgressPolicy":
        """业务阶段只开放冻结的精确私网服务，不开放公共依赖站点。"""

        host, allow_addresses = _local_model_destination(model_base_url)
        hosts = [host]
        addresses = list(allow_addresses)
        for value in additional_base_urls:
            extra_host, extra_addresses = _local_model_destination(value)
            if extra_host not in hosts:
                hosts.append(extra_host)
            for address in extra_addresses:
                if address not in addresses:
                    addresses.append(address)
        return cls(
            phase=EgressPhase.BUSINESS_EXECUTION,
            allowed_domains=tuple(hosts),
            allow_addresses=tuple(addresses),
            mount_sources=True,
        )


def render_smokescreen_acl(policy: EgressPolicy) -> str:
    """生成默认 enforce 的角色 ACL，不允许 report/open 混入生产。"""

    payload = {
        "version": "v1",
        "services": [],
        "default": {
            "project": "mangrove-agentic-runtime",
            "action": "enforce",
            "allowed_domains": list(policy.allowed_domains),
        },
    }
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
    )


def render_smokescreen_config(policy: EgressPolicy) -> str:
    """生成 sidecar 主配置；私网默认拒绝，仅精确放行本地模型。"""

    payload: dict[str, object] = {
        "ip": "0.0.0.0",
        "port": 4750,
        "acl_file": "/etc/smokescreen/acl.yaml",
        # 每个任务网络只有当前 Pi 客户端，首期不额外维护任务级 mTLS。
        "allow_missing_role": True,
        "network": "ip",
        "statsd_address": "",
        "unsafe_allow_private_ranges": False,
        "max_concurrent_requests": 16,
        "max_request_rate": 20,
        "max_request_burst": 40,
        "max_concurrent_connect_tunnels": 8,
    }
    if policy.allow_addresses:
        payload["allow_addresses"] = list(policy.allow_addresses)
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
    )


async def _run_docker(
    command: tuple[str, ...],
) -> DockerCommandResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return DockerCommandResult(
        returncode=int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


class SmokescreenEgressController:
    """用成熟代理 sidecar 管理任务级网络，不自行实现代理协议。"""

    def __init__(
        self,
        *,
        image: str,
        command_runner: DockerCommandRunner | None = None,
    ) -> None:
        self.image = image
        self._run = command_runner or _run_docker

    def lease_for(
        self,
        *,
        policy: EgressPolicy,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        policy_dir: Path,
    ) -> EgressLease:
        """确定性生成资源身份，供启动与崩溃恢复共用。"""

        identity = hashlib.sha256(
            f"{user_id}:{task_id}:{revision}:{run_id}:{policy.phase.value}".encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        safe_task = re.sub(r"[^a-z0-9-]", "-", task_id.lower())[-16:]
        network_name = f"mangrove-pi-net-{safe_task}-{identity}"[:63]
        proxy_name = f"mangrove-pi-proxy-{safe_task}-{identity}"[:63]
        return EgressLease(
            phase=policy.phase,
            network_name=network_name,
            proxy_container_name=proxy_name,
            proxy_url=f"http://{proxy_name}:4750",
            policy_dir=policy_dir,
        )

    async def start(
        self,
        *,
        policy: EgressPolicy,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        policy_dir: Path,
        replace_existing: bool = False,
    ) -> EgressLease:
        """创建内部网络和双网卡代理；恢复时可先撤销同一 Run 的旧资源。"""

        policy_dir.mkdir(parents=True, exist_ok=False)
        (policy_dir / "acl.yaml").write_text(
            render_smokescreen_acl(policy),
            encoding="utf-8",
        )
        (policy_dir / "config.yaml").write_text(
            render_smokescreen_config(policy),
            encoding="utf-8",
        )
        lease = self.lease_for(
            policy=policy,
            user_id=user_id,
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            policy_dir=policy_dir,
        )
        if replace_existing:
            # 名称由 owner、task、revision、run 和 phase 的摘要确定；恢复同一
            # Run 时只撤销该确定性身份，普通新启动仍保持“已存在即失败关闭”。
            await self.stop(lease)

        create = (
            "docker",
            "network",
            "create",
            "--internal",
            "--label",
            "mangrove.agentic-runtime=true",
            lease.network_name,
        )
        result = await self._run(create)
        if result.returncode != 0:
            raise RuntimeError(
                f"无法创建任务级内部网络：{result.stderr.strip()[:300]}"
            )
        try:
            mount = (
                f"type=bind,source={policy_dir.resolve()},"
                "target=/etc/smokescreen,readonly"
            )
            run = (
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                lease.proxy_container_name,
                "--network",
                lease.network_name,
                "--label",
                "mangrove.agentic-runtime=true",
                "--mount",
                mount,
                self.image,
                "--config-file",
                "/etc/smokescreen/config.yaml",
                "--disable-acl-policy-action",
                "open",
                "--disable-acl-policy-action",
                "report",
            )
            result = await self._run(run)
            if result.returncode != 0:
                raise RuntimeError(
                    f"无法启动 Egress sidecar：{result.stderr.strip()[:300]}"
                )
            connect = (
                "docker",
                "network",
                "connect",
                "bridge",
                lease.proxy_container_name,
            )
            result = await self._run(connect)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Egress sidecar 无法连接外部桥：{result.stderr.strip()[:300]}"
                )
            return lease
        except Exception:
            await self.stop(lease)
            raise

    async def stop(self, lease: EgressLease) -> None:
        """先保留结构化代理日志，再删除代理和任务网络。"""

        logs = await self._run(
            ("docker", "logs", lease.proxy_container_name)
        )
        if (logs.stdout or logs.stderr) and lease.policy_dir.is_dir():
            (lease.policy_dir / "egress.log").write_text(
                logs.stdout + logs.stderr,
                encoding="utf-8",
            )
        await self._run(
            ("docker", "rm", "-f", lease.proxy_container_name)
        )
        await self._run(
            ("docker", "network", "rm", lease.network_name)
        )
