# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agentic_runtime.candidate_verifier import CandidateVerifier
from src.candidate_verification import CurrentVerifierRulesetResolver
from src.candidate_verification.ruleset import _selected_nodes


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
