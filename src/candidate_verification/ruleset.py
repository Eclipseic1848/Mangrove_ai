# -*- coding: utf-8 -*-
"""ADR-0033 当前 VerifierRuleset 的失败关闭解析器。"""
from __future__ import annotations

import ast
import hashlib
from importlib import metadata
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys

from .models import VerifierRulesetBinding


_SCHEMA_VERSION = 1
_ALLOWLIST_VERSION = "adr-0033-v1"
_SOURCE_ALLOWLIST: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("src/agentic_runtime/candidate_verifier.py", None),
    (
        "src/agentic_runtime/models.py",
        (
            "PermissionProfile",
            "VerificationStatus",
            "SourceInput",
            "PiRuntimeRequest",
            "CandidateArtifact",
            "VerificationCheck",
            "SemanticDecision",
            "VerificationReport",
        ),
    ),
    (
        "src/delivery_publishing/models.py",
        ("FrozenModel", "TableOutputContract"),
    ),
)
_DEPENDENCIES = (
    "httpx",
    "instructor",
    "openai",
    "openpyxl",
    "pdfplumber",
    "python-docx",
    "pydantic",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _git_text(repository_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository_root), *arguments),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("无法读取 VerifierRuleset 的 Git 执行身份")
    return completed.stdout


def _selected_nodes(
    source: str,
    symbols: tuple[str, ...] | None,
) -> tuple[tuple[str, ast.AST], ...]:
    try:
        module = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError("VerifierRuleset 相关源码无法解析") from exc
    if symbols is None:
        return (("<module>", module),)
    definitions: dict[str, list[ast.AST]] = {}
    local_names: set[str] = set()
    for node in module.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(node.name, []).append(node)
            local_names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            local_names.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    if any(len(definitions.get(name, ())) != 1 for name in symbols):
        raise RuntimeError("VerifierRuleset 允许列表符号缺失或重复")
    selected = set(symbols)
    for name in symbols:
        referenced = {
            node.id
            for node in ast.walk(definitions[name][0])
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        if (referenced & local_names) - selected:
            raise RuntimeError("VerifierRuleset 存在未覆盖的本地契约")
    return tuple((name, definitions[name][0]) for name in symbols)


def _source_entries(
    repository_root: Path,
    commit: str,
) -> tuple[dict[str, str], ...]:
    entries: list[dict[str, str]] = []
    for relative_path, symbols in _SOURCE_ALLOWLIST:
        current_source = (repository_root / relative_path).read_text(
            encoding="utf-8"
        )
        committed_source = _git_text(
            repository_root,
            "show",
            f"{commit}:{relative_path}",
        )
        current_nodes = _selected_nodes(current_source, symbols)
        committed_nodes = dict(_selected_nodes(committed_source, symbols))
        for symbol, current_node in current_nodes:
            current_dump = ast.dump(current_node, include_attributes=False)
            committed_dump = ast.dump(
                committed_nodes[symbol],
                include_attributes=False,
            )
            if current_dump != committed_dump:
                raise RuntimeError("VerifierRuleset 相关源码存在未提交语义变化")
            entries.append(
                {
                    "path": relative_path,
                    "symbol": symbol,
                    "strategy": "python_ast_without_attributes",
                    "ast_sha256": _sha256_text(current_dump),
                }
            )
    return tuple(sorted(entries, key=lambda item: (item["path"], item["symbol"])))


def _prompt_and_config_entries(repository_root: Path) -> tuple[list[dict], list[dict]]:
    path = repository_root / "src/agentic_runtime/candidate_verifier.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    prompts: list[dict] = []
    for index, value in enumerate(
        node.value
        for node in ast.walk(module)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value.strip()) >= 20
    ):
        prompts.append(
            {
                "id": f"candidate_verifier:string:{index:04d}",
                "sha256": _sha256_text(value),
            }
        )
    configs: list[dict] = []
    for node in module.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and target.id.startswith("_")
                and target.id.upper() == target.id
                and value is not None
            ):
                configs.append(
                    {
                        "id": target.id,
                        "ast_sha256": _sha256_text(
                            ast.dump(value, include_attributes=False)
                        ),
                    }
                )
    return prompts, sorted(configs, key=lambda item: item["id"])


def _dependency_entries(
    repository_root: Path,
    commit: str,
) -> tuple[dict[str, str], ...]:
    current = (repository_root / "requirements.txt").read_text(encoding="utf-8")
    committed = _git_text(repository_root, "show", f"{commit}:requirements.txt")

    def declarations(source: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_line in source.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?(.*)$", line)
            if match:
                result[_normalized_name(match.group(1))] = line
        return result

    current_declarations = declarations(current)
    committed_declarations = declarations(committed)
    entries: list[dict[str, str]] = []
    for package in _DEPENDENCIES:
        normalized = _normalized_name(package)
        declaration = current_declarations.get(normalized)
        if (
            declaration is None
            or committed_declarations.get(normalized) != declaration
        ):
            raise RuntimeError("VerifierRuleset 依赖声明缺失或存在未提交变化")
        try:
            installed_version = metadata.version(package)
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError("VerifierRuleset 直接依赖未安装") from exc
        entries.append(
            {
                "name": normalized,
                "declaration": declaration,
                "installed_version": installed_version,
            }
        )
    return tuple(sorted(entries, key=lambda item: item["name"]))


class CurrentVerifierRulesetResolver:
    """只为当前实际进程生成可审计且可比较的 Ruleset 身份。"""

    def __init__(self, repository_root: str | Path) -> None:
        self._repository_root = Path(repository_root).resolve()

    def resolve(self, verifier: object) -> VerifierRulesetBinding:
        verifier_type = type(verifier)
        expected_source = (
            self._repository_root / "src/agentic_runtime/candidate_verifier.py"
        ).resolve()
        try:
            actual_source = inspect.getsourcefile(verifier_type)
        except (OSError, TypeError):
            actual_source = None
        if (
            verifier_type.__module__ != "src.agentic_runtime.candidate_verifier"
            or verifier_type.__qualname__ != "CandidateVerifier"
            or actual_source is None
            or Path(actual_source).resolve() != expected_source
        ):
            raise RuntimeError("实际 Verifier 实例与冻结 Ruleset 不一致")
        return self.resolve_target()

    def resolve_target(self) -> VerifierRulesetBinding:
        """解析当前可执行规则身份，不构造 Verifier 或发起 Provider 调用。"""

        commit = _git_text(
            self._repository_root,
            "rev-parse",
            "HEAD",
        ).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise RuntimeError("VerifierRuleset Git commit 身份无效")
        source_entries = _source_entries(self._repository_root, commit)
        prompt_entries, config_entries = _prompt_and_config_entries(
            self._repository_root
        )
        dependency_entries = _dependency_entries(self._repository_root, commit)
        source_payload = {
            "source_entries": source_entries,
            "prompt_entries": prompt_entries,
            "config_entries": config_entries,
            "dependency_entries": dependency_entries,
        }
        source_hash = _sha256_text(_canonical_json(source_payload))
        ruleset_hash = _sha256_text(
            _canonical_json(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "allowlist_version": _ALLOWLIST_VERSION,
                    "verifier_source_hash": source_hash,
                }
            )
        )
        python_identity = {
            "implementation": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag,
            "version": list(sys.version_info[:3]),
        }
        execution_hash = _sha256_text(
            _canonical_json(
                {
                    "code_commit": commit,
                    "verifier_ruleset_hash": ruleset_hash,
                    "python": python_identity,
                }
            )
        )
        manifest = {
            "schema_version": _SCHEMA_VERSION,
            "allowlist_version": _ALLOWLIST_VERSION,
            "code_commit": commit,
            **source_payload,
            "verifier_source_hash": source_hash,
            "verifier_ruleset_hash": ruleset_hash,
            "python": python_identity,
            "execution_identity_hash": execution_hash,
        }
        return VerifierRulesetBinding(
            verifier_ruleset_hash=ruleset_hash,
            verifier_code_commit=commit,
            verifier_source_hash=source_hash,
            verifier_execution_identity_hash=execution_hash,
            verifier_ruleset_manifest_json=_canonical_json(manifest),
        )
