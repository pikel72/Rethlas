from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rethlas.agent_loop import _run_litellm_tool_loop
from rethlas.config import ModelConfig, load_config
from rethlas.problems import normalize_problem
from rethlas.references import ReferencePreparation
from rethlas.runtime import (
    _extract_json_object,
    _normalize_verification_payload,
    _validate_verification_payload,
    build_request,
)
from rethlas.subagents import SubAgentRunner, SubAgentTask
from rethlas.tools import build_generation_tool_registry


def test_problem_normalization_short_id():
    config = load_config()
    problem = normalize_problem("ns/ns", config.paths.generation_dir)
    assert problem.problem_path == "data/ns/ns.md"
    assert problem.problem_id == "ns/ns"


def test_runtime_config_has_multi_model_profiles(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-5")
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
    assert openai.model == "gpt-5"
    claude = config.resolve_model("claude")
    assert claude.provider == "litellm"
    assert claude.compat == "anthropic"
    assert claude.model == "claude-opus-4-5"


def test_litellm_model_id_prefixes_openai_compat():
    """openai-compat vendors (deepseek/qwen/glm/kimi/doubao/siliconflow)
    need the `openai/` prefix so LiteLLM routes to the openai-compatible
    provider instead of bailing with "LLM Provider NOT provided"."""
    from rethlas.runtime import litellm_model_id
    model = ModelConfig(name="deepseek", provider="litellm", model="deepseek-v4-pro", compat="openai")
    assert litellm_model_id(model) == "openai/deepseek-v4-pro"


def test_litellm_model_id_prefixes_anthropic_compat():
    from rethlas.runtime import litellm_model_id
    model = ModelConfig(name="claude", provider="litellm", model="claude-opus-4.8", compat="anthropic")
    assert litellm_model_id(model) == "anthropic/claude-opus-4.8"


def test_litellm_model_id_preserves_user_prefix():
    """OpenRouter users typically write `anthropic/claude-opus-4.8` (vendor/model)
    in `OPENROUTER_MODEL`. We must not double-prefix."""
    from rethlas.runtime import litellm_model_id
    model = ModelConfig(
        name="openrouter",
        provider="litellm",
        model="anthropic/claude-opus-4.8",
        compat="openai",
    )
    assert litellm_model_id(model) == "anthropic/claude-opus-4.8"


def test_litellm_model_id_passthrough_without_compat():
    """When `compat` is None (e.g. some custom config), pass the model name
    through unchanged."""
    from rethlas.runtime import litellm_model_id
    model = ModelConfig(name="x", provider="litellm", model="gpt-5.5", compat=None)
    assert litellm_model_id(model) == "gpt-5.5"


def test_native_litellm_tool_loop_uses_shared_completion_kwargs(monkeypatch, tmp_path):
    captured = {}

    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content="draft proof", tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeRegistry:
        def schemas(self):
            return [
                {
                    "type": "function",
                    "function": {"name": "memory_init", "parameters": {"type": "object"}},
                }
            ]

    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_API_BASE", "https://proxy.example.com/v1")

    config = load_config()
    problem = normalize_problem("example", config.paths.generation_dir)
    runtime_request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "log.md",
        model_name="deepseek",
    )
    draft = _run_litellm_tool_loop(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        runtime_request,
        FakeRegistry(),
        stream=False,
    )

    assert draft == "draft proof"
    assert captured["model"] == "openai/deepseek-chat"
    assert captured["api_key"] == "sk-test"
    assert captured["api_base"] == "https://proxy.example.com/v1"
    assert captured["tools"][0]["function"]["name"] == "memory_init"


def test_verification_json_validation():
    payload = _extract_json_object(
        'prefix {"verification_report":{"summary":"ok","critical_errors":[],"gaps":[]},"verdict":"correct","repair_hints":""} suffix'
    )
    _validate_verification_payload(payload)
    assert payload["verdict"] == "correct"


def test_verification_payload_normalizes_empty_repair_hint_list():
    payload = {
        "verification_report": {"summary": "ok", "critical_errors": [], "gaps": []},
        "verdict": "correct",
        "repair_hints": [],
    }
    _normalize_verification_payload(payload)
    _validate_verification_payload(payload)
    assert payload["repair_hints"] == ""


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
