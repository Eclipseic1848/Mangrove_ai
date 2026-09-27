# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agentic_runtime.candidate_verifier import CandidateVerifier
from src.candidate_verification import CurrentVerifierRulesetResolver
from src.candidate_verification.ruleset import _selected_nodes, _SOURCE_ALLOWLIST, _ALLOWLIST_VERSION


def test_current_ruleset_resolver_is_deterministic_and_ignores_unrelated_worktree(
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    resolver = CurrentVerifierRulesetResolver(repository_root)
    verifier = CandidateVerifier.__new__(CandidateVerifier)

    first = resolver.resolve(verifier)
    second = resolver.resolve(verifier)

    assert first == second
    manifest = json.loads(first.verifier_ruleset_manifest_json)
    assert manifest["verifier_ruleset_hash"] == first.verifier_ruleset_hash
    assert manifest["verifier_source_hash"] == first.verifier_source_hash
    assert manifest["execution_identity_hash"] == (
        first.verifier_execution_identity_hash
    )
    assert manifest["source_entries"]
    assert all(
        not item["path"].startswith("evals/")
        for item in manifest["source_entries"]
    )


def test_ruleset_rejects_unbound_verifier_and_incomplete_symbol_contracts() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    resolver = CurrentVerifierRulesetResolver(repository_root)

    with pytest.raises(RuntimeError, match="实际 Verifier"):
        resolver.resolve(object())
    with pytest.raises(RuntimeError, match="缺失或重复"):
        _selected_nodes("class Contract: pass\nclass Contract: pass\n", ("Contract",))
    with pytest.raises(RuntimeError, match="未覆盖的本地契约"):
        _selected_nodes(
            "class LocalBase: pass\nclass Contract(LocalBase): pass\n",
            ("Contract",),
        )


def test_ruleset_covers_lesson_assessment_symbol_closure() -> None:
    root = Path(__file__).resolve().parents[1]
    symbols = dict(_SOURCE_ALLOWLIST)["src/agentic_runtime/models.py"]
    assert "LessonAssessment" in symbols
    assert _ALLOWLIST_VERSION == "adr-0033-v3"
    source = (root / "src/agentic_runtime/models.py").read_text(encoding="utf-8")
    assert dict(_selected_nodes(source, symbols))["LessonAssessment"]
    with pytest.raises(RuntimeError, match="未覆盖的本地契约"):
        _selected_nodes(source, tuple(name for name in symbols if name != "LessonAssessment"))


def test_lesson_rule_identity_rejects_uncommitted_changes(tmp_path, monkeypatch) -> None:
    import src.candidate_verification.ruleset as ruleset
    root = Path(__file__).resolve().parents[1]
    committed = {}
    for relative, _ in _SOURCE_ALLOWLIST:
        committed[relative] = (root / relative).read_text(encoding="utf-8")
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(committed[relative], encoding="utf-8")
    monkeypatch.setattr(ruleset, "_git_text", lambda _root, _show, ref: committed[ref.split(":", 1)[1]])
    before = ruleset._source_entries(tmp_path, "test-commit")
    relative = "src/agentic_runtime/models.py"
    changed = committed[relative].replace("candidate_quote: str = Field(max_length=120)", "candidate_quote: str = Field(max_length=121)")
    assert changed != committed[relative]
    (tmp_path / relative).write_text(changed, encoding="utf-8")
    with pytest.raises(RuntimeError, match="未提交语义变化"):
        ruleset._source_entries(tmp_path, "test-commit")
    committed[relative] = changed
    after = ruleset._source_entries(tmp_path, "test-commit")
    assert after != before
    (tmp_path / relative).write_text(changed + "\nclass Unrelated: pass\n", encoding="utf-8")
    assert ruleset._source_entries(tmp_path, "test-commit") == after


def test_html_parser_version_is_bound_and_uncommitted_declaration_rejected(tmp_path, monkeypatch):
    import src.candidate_verification.ruleset as ruleset
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(requirements, encoding="utf-8")
    monkeypatch.setattr(ruleset, "_git_text", lambda *_args: requirements)
    versions = {name: "1.0" for name in (*ruleset._DEPENDENCIES, "beautifulsoup4")}
    monkeypatch.setattr(ruleset.metadata, "version", versions.__getitem__)
    before = ruleset._dependency_entries(tmp_path, "fixture-commit")
    assert "beautifulsoup4" in {entry["name"] for entry in before}
    versions["beautifulsoup4"] = "2.0"
    assert ruleset._dependency_entries(tmp_path, "fixture-commit") != before
    # 网页解析库同样会影响核验输入，未提交的依赖声明必须拒绝。
    changed = "\n".join(line for line in requirements.splitlines() if not line.startswith("beautifulsoup4"))
    assert changed != requirements
    (tmp_path / "requirements.txt").write_text(changed, encoding="utf-8")
    with pytest.raises(RuntimeError, match="依赖声明"):
        ruleset._dependency_entries(tmp_path, "fixture-commit")


def test_ruleset_covers_actual_protocol_transport_and_resolver():
    import src.candidate_verification.ruleset as ruleset
    paths = dict(ruleset._SOURCE_ALLOWLIST)
    assert {"src/candidate_verification/ruleset.py", "src/model_connections/text_protocol.py",
            "src/model_connections/broker.py", "src/model_connections/contracts.py",
            "src/model_connections/pinned_transport.py", "src/model_connections/catalog.py"} <= paths.keys()
    assert "httpcore" in ruleset._DEPENDENCIES


def test_shared_methods_bind_constants_and_reject_missing_callee():
    import ast
    source = "LIMIT=3\nclass Gateway:\n def call(self): return self.parse(LIMIT)\n def parse(self, x): return x\n def label(self): return '展示'\n"
    symbols = ("LIMIT", "Gateway.call", "Gateway.parse")
    def identity(text):
        return [(name, ast.dump(node, include_attributes=False)) for name, node in _selected_nodes(text, symbols)]
    assert identity(source) == identity(source.replace("展示", "其他展示"))
    assert identity(source) != identity(source.replace("LIMIT=3", "LIMIT=4"))
    assert identity(source) != identity(source.replace("class Gateway:", "class Gateway(Base):"))
    with pytest.raises(RuntimeError, match="未覆盖"):
        _selected_nodes(source, ("LIMIT", "Gateway.call"))
    with pytest.raises(RuntimeError, match="缺失或重复"):
        _selected_nodes(source + "\nLIMIT=5\n", symbols)


def test_catalog_display_changes_do_not_change_execution_identity():
    import ast
    import src.candidate_verification.ruleset as ruleset
    source = (Path(__file__).resolve().parents[1] / "src/model_connections/catalog.py").read_text(encoding="utf-8")
    symbols = dict(ruleset._SOURCE_ALLOWLIST)["src/model_connections/catalog.py"]
    def identity(text):
        return [(name, ast.dump(node, include_attributes=False))
                for name, node in ruleset._selected_nodes(text, symbols, catalog_runtime=True)]
    display = source.replace("DeepSeek V4.1 Flash", "新的展示名称").replace("适合中文、推理和通用 Agent 任务", "新的描述")
    assert display != source and identity(display) == identity(source)
    assert identity(source.replace("max_output_tokens=384_000", "max_output_tokens=123")) != identity(source)
    assert identity(source.replace("https://api.deepseek.com\"", "https://different.invalid\"")) != identity(source)


def test_selected_symbol_identity_includes_its_import_binding():
    import ast
    source = "from parser_a import parse\nfrom elsewhere import unused\ndef verify(value): return parse(value)\n"
    def identity(text):
        return [(name, ast.dump(node, include_attributes=False)) for name, node in _selected_nodes(text, ("verify",))]
    assert identity(source) != identity(source.replace("parser_a", "parser_b"))
    assert identity(source) == identity(source.replace("elsewhere", "unrelated"))


def test_selected_method_rejects_replaced_owner_class():
    source = "class Gateway:\n def call(self): return 1\nclass Gateway:\n def label(self): return '展示'\n"
    with pytest.raises(RuntimeError, match="缺失或重复"):
        _selected_nodes(source, ("Gateway.call",))
