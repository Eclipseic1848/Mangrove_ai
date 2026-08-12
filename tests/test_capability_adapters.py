# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from src.capability_adapters import (
    CapabilityRuntimeManifest,
    CliAdapter,
    CommandCapabilityAdapter,
    LocalMcpAdapter,
    NodeAdapter,
    PythonAdapter,
    RuntimeCommand,
    SkillAdapter,
    load_runtime_manifests,
)


def test_runtime_command_rejects_shell_and_path_escape() -> None:
    with pytest.raises(ValidationError, match="Shell"):
        RuntimeCommand(program="bash", arguments=("-lc", "curl example.com"))

    with pytest.raises(ValidationError, match="越界"):
        RuntimeCommand(program="../bin/tool")

    with pytest.raises(ValidationError, match="Secret"):
        RuntimeCommand(program="bin/tool", environment=(("API_KEY", "secret"),))


def test_python_adapter_requires_lock_and_builds_frozen_uv_plan(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='1.0.0'\nrequires-python='==3.12.*'\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / ".python-version").write_text("3.12.11\n", encoding="utf-8")

    plan = PythonAdapter().prepare(tmp_path)

    assert plan.commands[0].argv == ("uv", "lock", "--check")
    assert plan.commands[1].argv == (
        "uv",
        "sync",
        "--frozen",
        "--no-dev",
        "--no-editable",
    )
    assert plan.runtime_identity == "cpython@3.12.11"

    (tmp_path / "uv.lock").unlink()
    with pytest.raises(ValueError, match="uv.lock"):
        PythonAdapter().prepare(tmp_path)


def test_node_adapter_uses_npm_ci_without_lifecycle_scripts(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"sample","version":"1.0.0"}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        '{"name":"sample","lockfileVersion":3,"packages":{}}',
        encoding="utf-8",
    )

    plan = NodeAdapter(node_version="22.22.1", npm_version="10.9.4").prepare(
        tmp_path
    )

    assert tuple(command.argv for command in plan.commands) == (
        ("npm", "ci", "--ignore-scripts"),
    )
    assert plan.runtime_identity == "node@22.22.1/npm@10.9.4"


def test_cli_adapter_verifies_exact_asset_digest_and_entrypoint(tmp_path: Path) -> None:
    asset = tmp_path / "fd.zip"
    asset.write_bytes(b"official-release-asset")
    asset_digest = "sha256:" + hashlib.sha256(asset.read_bytes()).hexdigest()
    binary = tmp_path / "fd"
    binary.write_bytes(b"frozen-cli")
    digest = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()

    prepared = CliAdapter().prepare(
        tmp_path,
        entrypoint="fd",
        expected_digest=digest,
        platform="linux",
        architecture="x86_64",
        source_ref="sharkdp/fd@v10.3.0/fd-v10.3.0-x86_64-unknown-linux-gnu.tar.gz",
        asset_path=asset,
        expected_asset_digest=asset_digest,
    )

    assert prepared.entrypoint == Path("fd")
    assert prepared.source_ref.endswith(".tar.gz")

    with pytest.raises(ValueError, match="digest"):
        CliAdapter().prepare(
            tmp_path,
            entrypoint="fd",
            expected_digest="sha256:" + "0" * 64,
            platform="linux",
            architecture="x86_64",
            source_ref="sharkdp/fd@v10.3.0/fd.tar.gz",
            asset_path=asset,
            expected_asset_digest=asset_digest,
        )

    with pytest.raises(ValueError, match="Release 资产 digest"):
        CliAdapter().prepare(
            tmp_path,
            entrypoint="fd",
            expected_digest=digest,
            platform="linux",
            architecture="x86_64",
            source_ref="sharkdp/fd@v10.3.0/fd.tar.gz",
            asset_path=asset,
            expected_asset_digest="sha256:" + "0" * 64,
        )


def test_skill_adapter_enforces_agent_skills_layout_and_script_gate(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "invoice-fields"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: invoice-fields\ndescription: 提取固定发票字段\n---\n\n按需读取引用。\n",
        encoding="utf-8",
    )

    prepared = SkillAdapter().prepare(skill)

    assert prepared.skill_file == Path("SKILL.md")
    assert prepared.name == "invoice-fields"

    scripts = skill / "scripts"
    scripts.mkdir()
    (scripts / "run.py").write_text("print('x')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="数据文件"):
        SkillAdapter().prepare(skill)

    (scripts / "run.py").unlink()
    (skill / "references").mkdir()
    (skill / "references" / "hidden.js").write_text(
        "console.log('x')\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="数据文件"):
        SkillAdapter().prepare(skill)
    (skill / "references" / "hidden.js").unlink()
    (skill / "references" / "oversized.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="文件超过"):
        SkillAdapter().prepare(skill)


def test_runtime_manifest_loads_multiple_packs_without_leaking_remote_secret(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    skill = second / "invoice-fields"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: invoice-fields\ndescription: 约束发票字段\n---\n",
        encoding="utf-8",
    )
    (first / "mangrove-capability.json").write_text(
        """{
          "schema_version": 1,
          "name": "pygmentize",
          "version": "2.19.2",
          "kind": "python",
          "purpose": "语法高亮",
          "entrypoint": {"program": ".venv/bin/pygmentize"},
          "healthcheck": {"program": ".venv/bin/pygmentize", "arguments": ["-V"]}
        }""",
        encoding="utf-8",
    )
    (second / "mangrove-capability.json").write_text(
        """{
          "schema_version": 1,
          "name": "invoice-fields",
          "version": "1.0.0",
          "kind": "skill",
          "purpose": "约束发票字段",
          "skill_path": "invoice-fields"
        }""",
        encoding="utf-8",
    )

    manifests = load_runtime_manifests((first, second))

    assert [item.manifest.name for item in manifests] == [
        "pygmentize",
        "invoice-fields",
    ]
    assert manifests[1].container_skill_path == (
        "/workspace/capabilities/2/invoice-fields"
    )

    with pytest.raises(ValidationError, match="secret_ref"):
        CapabilityRuntimeManifest.model_validate(
            {
                "schema_version": 1,
                "name": "remote",
                "version": "1.0.0",
                "kind": "mcp_remote",
                "purpose": "远程能力",
                "connection_ref": "conn-1",
                "secret_ref": "literal-secret",
            }
        )

    with pytest.raises(ValidationError, match="权限"):
        CapabilityRuntimeManifest.model_validate(
            {
                "schema_version": 1,
                "name": "malicious",
                "version": "1.0.0",
                "kind": "cli",
                "purpose": "恶意权限负例",
                "entrypoint": {"program": "bin/tool"},
                "permissions": ["docker:socket"],
            }
        )


@pytest.mark.asyncio
async def test_command_adapter_runs_without_shell_and_cancels_active_call(
    tmp_path: Path,
) -> None:
    script = tmp_path / "tool.py"
    script.write_text(
        "import sys, time\n"
        "if sys.argv[1] == 'sleep': time.sleep(30)\n"
        "else: print(sys.argv[1].upper())\n",
        encoding="utf-8",
    )
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "uppercase",
            "version": "1.0.0",
            "kind": "python",
            "purpose": "转大写",
            "entrypoint": {"program": "python", "arguments": ["tool.py"]},
            "healthcheck": {
                "program": "python",
                "arguments": ["tool.py", "healthy"],
            },
        }
    )
    adapter = CommandCapabilityAdapter(
        tmp_path,
        manifest,
        runtime_aliases={"python": sys.executable},
    )

    await adapter.prepare()
    assert (await adapter.health()).stdout.strip() == "HEALTHY"
    assert (await adapter.invoke(("hello",))).stdout.strip() == "HELLO"

    task = asyncio.create_task(adapter.invoke(("sleep",)))
    await asyncio.sleep(0.1)
    await adapter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await adapter.cleanup()
    assert adapter.active_pid is None


@pytest.mark.asyncio
async def test_command_adapter_does_not_inherit_host_secret_or_buffer_unbounded_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "boundary.py"
    script.write_text(
        "import os, sys\n"
        "if sys.argv[1] == 'env': print(os.getenv('MANGROVE_HOST_SECRET', 'absent'))\n"
        "else: sys.stdout.write('x' * (1024 * 1024 + 1))\n",
        encoding="utf-8",
    )
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "boundary",
            "version": "1.0.0",
            "kind": "python",
            "purpose": "验证运行边界",
            "entrypoint": {"program": "python", "arguments": ["boundary.py"]},
        }
    )
    adapter = CommandCapabilityAdapter(
        tmp_path,
        manifest,
        runtime_aliases={"python": sys.executable},
    )
    monkeypatch.setenv("MANGROVE_HOST_SECRET", "must-not-leak")

    assert (await adapter.invoke(("env",))).stdout.strip() == "absent"
    with pytest.raises(RuntimeError, match="1 MiB"):
        await adapter.invoke(("large",))
    assert adapter.active_pid is None


@pytest.mark.asyncio
async def test_local_mcp_adapter_reuses_one_session_and_cleans_up(
    tmp_path: Path,
) -> None:
    server = tmp_path / "server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('fixture')\n"
        "@server.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return text\n"
        "if __name__ == '__main__':\n"
        "    server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    manifest = CapabilityRuntimeManifest.model_validate(
        {
            "schema_version": 1,
            "name": "fixture-mcp",
            "version": "1.0.0",
            "kind": "mcp_local",
            "purpose": "协议验证",
            "entrypoint": {"program": "python", "arguments": ["server.py"]},
        }
    )
    adapter = LocalMcpAdapter(
        tmp_path,
        manifest,
        runtime_aliases={"python": sys.executable},
    )

    await adapter.prepare()
    assert await adapter.health() is True
    assert "echo" in await adapter.list_tools()
    first_session = adapter.session_identity
    result = await adapter.invoke("echo", {"text": "mangrove"})
    assert "mangrove" in str(result)
    assert adapter.session_identity == first_session

    await adapter.cancel()
    await adapter.cleanup()
    assert adapter.session_identity is None
