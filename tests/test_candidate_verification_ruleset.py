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
    assert _ALLOWLIST_VERSION == "adr-0033-v2"
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
