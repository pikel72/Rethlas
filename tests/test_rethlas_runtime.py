from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from rethlas.config import load_config
from rethlas.problems import normalize_problem
from rethlas.runtime import _extract_json_object, _validate_verification_payload
from rethlas.subagents import SubAgentRunner, SubAgentTask
from rethlas.tools import build_generation_tool_registry


def test_problem_normalization_short_id():
    config = load_config()
    problem = normalize_problem("ns/ns", config.paths.generation_dir)
    assert problem.problem_path == "data/ns/ns.md"
    assert problem.problem_id == "ns/ns"


def test_runtime_config_has_multi_model_profiles(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    config = load_config()
    # toml-only profiles: codex + mock
    assert config.models["mock-verification-correct"].provider == "mock"
    assert "gpt-5.5" in config.models
    assert "codex-fast" in config.models
    assert "codex-deep" in config.models
    # env presets: resolved through the new BUILTIN_PRESETS path
    openai = config.resolve_model("openai")
    assert openai.provider == "litellm"
    assert openai.compat == "openai"
    claude = config.resolve_model("claude")
    assert claude.provider == "litellm"
    assert claude.compat == "anthropic"


def test_verification_json_validation():
    payload = _extract_json_object(
        'prefix {"verification_report":{"summary":"ok","critical_errors":[],"gaps":[]},"verdict":"correct","repair_hints":""} suffix'
    )
    _validate_verification_payload(payload)
    assert payload["verdict"] == "correct"


def test_generation_tool_registry_memory_roundtrip():
    config = load_config()
    registry = build_generation_tool_registry(config)
    result = registry.call("memory_init", {"problem_id": "pytest_runtime", "meta": {"source": "pytest"}})
    assert result.ok
    result = registry.call(
        "memory_append",
        {
            "problem_id": "pytest_runtime",
            "channel": "events",
            "record": {"event_type": "pytest"},
        },
    )
    assert result.ok


def test_subagent_depth_constraint():
    config = load_config()
    runner = SubAgentRunner(config)
    results = runner.run_mock_batch([SubAgentTask("too-deep", "x", depth=config.agents.max_depth + 1)])
    assert not results[0].ok


def test_mock_verification_api_paths(monkeypatch):
    monkeypatch.setenv("RETHLAS_MODEL", "mock-verification-correct")
    from agents.verification.api.server import run_runtime_verification

    payload = run_runtime_verification("pytest_mock_correct", "S", "P")
    assert payload["verdict"] == "correct"


def test_mock_verification_malformed_rejected(monkeypatch):
    monkeypatch.setenv("RETHLAS_MODEL", "mock-verification-malformed")
    from agents.verification.api.server import run_runtime_verification

    with pytest.raises(HTTPException):
        run_runtime_verification("pytest_mock_malformed", "S", "P")
