# -*- coding: utf-8 -*-
"""Phase 4B 关键 LangChain/LangGraph 依赖不得再次与锁定文件漂移。"""
from __future__ import annotations

from importlib import metadata
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRITICAL_PACKAGES = (
    "langchain",
    "langchain-community",
    "langchain-core",
    "langchain-openai",
    "langchain-text-splitters",
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-sqlite",
    "langgraph-prebuilt",
    "langgraph-sdk",
    "langsmith",
    "pydantic",
    "pydantic-settings",
    "pydantic_core",
)


def _locked_versions() -> dict[str, str]:
    locked: dict[str, str] = {}
    for raw_line in (PROJECT_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        locked[name.lower()] = version
    return locked


def test_phase4b_critical_dependency_stack_matches_requirements():
    locked = _locked_versions()
    missing_pins = [name for name in CRITICAL_PACKAGES if name not in locked]
    assert not missing_pins, f"关键依赖必须精确锁定：{missing_pins}"

    drift = {
        name: {"required": locked[name], "installed": metadata.version(name)}
        for name in CRITICAL_PACKAGES
        if metadata.version(name) != locked[name]
    }
    assert not drift, f"Phase 4B 关键依赖发生漂移：{drift}"
