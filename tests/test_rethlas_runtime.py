from __future__ import annotations

import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rethlas.agent_loop import _run_litellm_tool_loop, run_native_generation
from rethlas.config import ModelConfig, load_config
from rethlas.problems import normalize_problem
from rethlas.references import ReferencePreparation
from rethlas.runtime import (
    _extract_json_object,
    _normalize_verification_payload,
    _validate_verification_payload,
    build_request,
    missing_runtime_dependencies,
    RuntimePlan,
)
from rethlas.events import iter_events
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


def test_missing_runtime_dependencies_imports_litellm(monkeypatch, tmp_path):
    def fake_import_module(name: str):
        if name == "litellm":
            raise ImportError("No module named 'openai'")
        raise AssertionError(name)

    monkeypatch.setattr("rethlas.runtime.import_module", fake_import_module)
    plan = RuntimePlan(
        role="generation",
        provider_name="litellm",
        provider_kind="litellm",
        model_profile="deepseek",
        model="deepseek-v4-pro",
        cwd=tmp_path,
        log_path=tmp_path / "log.md",
        command=None,
        api_base_url="https://api.deepseek.com/v1",
        api_key_env=None,
        implemented=True,
    )

    assert missing_runtime_dependencies(plan) == [
        "python package: litellm (No module named 'openai')"
    ]


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


def test_native_generation_repairs_after_wrong_verdict(monkeypatch, tmp_path):
    completions: list[str] = []
    prompts: list[str] = []

    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            prompts.append(kwargs["messages"][-1]["content"])
            content = "bad proof" if len(completions) == 0 else "fixed proof with $x \\in X$"
            completions.append(content)
            message = SimpleNamespace(content=content, tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeRegistry:
        def __init__(self):
            self.verifications = 0
            self.memory_records = []

        def call(self, name, arguments):
            if name in {"memory_init", "memory_append"}:
                if name == "memory_append":
                    self.memory_records.append(arguments)
                return SimpleNamespace(ok=True, result={"ok": True}, error="")
            if name == "verify_proof_service":
                self.verifications += 1
                if self.verifications == 1:
                    return SimpleNamespace(
                        ok=True,
                        result={
                            "verification_report": {
                                "summary": "gap",
                                "critical_errors": [],
                                "gaps": [{"location": "proof", "issue": "missing step"}],
                            },
                            "verdict": "wrong",
                            "repair_hints": "Fill the missing step.",
                        },
                        error="",
                    )
                return SimpleNamespace(
                    ok=True,
                    result={
                        "verification_report": {"summary": "ok", "critical_errors": [], "gaps": []},
                        "verdict": "correct",
                        "repair_hints": "",
                    },
                    error="",
                )
            raise AssertionError(f"unexpected tool {name}")

        def schemas(self):
            return None

    registry = FakeRegistry()
    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    monkeypatch.setattr("rethlas.agent_loop.build_generation_tool_registry", lambda config: registry)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    config = load_config()
    base_problem = normalize_problem("example", config.paths.generation_dir)
    problem = replace(
        base_problem,
        log_dir=tmp_path / "logs",
        log_file=tmp_path / "logs" / "example.md",
        result_dir=tmp_path / "results",
        memory_dir=tmp_path / "memory",
    )
    request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "model.md",
        model_name="deepseek",
    )
    object.__setattr__(request.model, "supports_streaming", False)

    result = run_native_generation(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        request,
        stream=False,
        max_attempts=2,
    )

    assert result.returncode == 0
    assert registry.verifications == 2
    assert problem.result_dir.joinpath("blueprint_verified.md").read_text(encoding="utf-8") == "fixed proof with $x \\in X$"
    assert "Verification report" in prompts[1]
    assert "strictly as LaTeX math" in prompts[0]
    assert "strictly as LaTeX math" in prompts[1]
    assert "$x \\in A$" in prompts[0]
    assert "$$ ... $$" in prompts[0]
    assert "\\(x \\in A\\)" not in prompts[0]
    event_types = [event["event_type"] for event in iter_events(problem.log_dir)]
    assert event_types.count("native_attempt_started") == 2
    assert "native_attempt_failed" in event_types


def test_native_generation_exhausts_after_repeated_wrong_verdicts(monkeypatch, tmp_path):
    class FakeLiteLLM:
        calls = 0

        @staticmethod
        def completion(**kwargs):
            FakeLiteLLM.calls += 1
            message = SimpleNamespace(content=f"still wrong {FakeLiteLLM.calls}", tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeRegistry:
        def __init__(self):
            self.verifications = 0

        def call(self, name, arguments):
            if name in {"memory_init", "memory_append"}:
                return SimpleNamespace(ok=True, result={"ok": True}, error="")
            if name == "verify_proof_service":
                self.verifications += 1
                return SimpleNamespace(
                    ok=True,
                    result={
                        "verification_report": {
                            "summary": "wrong",
                            "critical_errors": [{"location": "proof", "issue": "false claim"}],
                            "gaps": [],
                        },
                        "verdict": "wrong",
                        "repair_hints": "Remove the false claim.",
                    },
                    error="",
                )
            raise AssertionError(f"unexpected tool {name}")

        def schemas(self):
            return None

    registry = FakeRegistry()
    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    monkeypatch.setattr("rethlas.agent_loop.build_generation_tool_registry", lambda config: registry)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    config = load_config()
    base_problem = normalize_problem("example", config.paths.generation_dir)
    problem = replace(
        base_problem,
        log_dir=tmp_path / "logs",
        log_file=tmp_path / "logs" / "example.md",
        result_dir=tmp_path / "results",
        memory_dir=tmp_path / "memory",
    )
    request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "model.md",
        model_name="deepseek",
    )
    object.__setattr__(request.model, "supports_streaming", False)

    result = run_native_generation(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        request,
        stream=False,
        max_attempts=2,
    )

    assert result.returncode == 1
    assert registry.verifications == 2
    assert problem.result_dir.joinpath("blueprint.md").exists()
    assert not problem.result_dir.joinpath("blueprint_verified.md").exists()
    event_types = [event["event_type"] for event in iter_events(problem.log_dir)]
    assert "native_generation_exhausted" in event_types
    assert event_types[-1] == "run_failed"


def test_native_generation_empty_draft_keeps_previous_candidate(monkeypatch, tmp_path):
    completions = ["bad proof with a gap", "", "fixed proof"]
    prompts: list[str] = []

    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            prompts.append(kwargs["messages"][-1]["content"])
            message = SimpleNamespace(content=completions.pop(0), tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeRegistry:
        def __init__(self):
            self.verifications = 0

        def call(self, name, arguments):
            if name in {"memory_init", "memory_append"}:
                return SimpleNamespace(ok=True, result={"ok": True}, error="")
            if name == "verify_proof_service":
                self.verifications += 1
                if self.verifications == 1:
                    return SimpleNamespace(
                        ok=True,
                        result={
                            "verification_report": {
                                "summary": "gap",
                                "critical_errors": [],
                                "gaps": [{"location": "proof", "issue": "missing step"}],
                            },
                            "verdict": "wrong",
                            "repair_hints": "Fill the missing step.",
                        },
                        error="",
                    )
                return SimpleNamespace(
                    ok=True,
                    result={
                        "verification_report": {"summary": "ok", "critical_errors": [], "gaps": []},
                        "verdict": "correct",
                        "repair_hints": "",
                    },
                    error="",
                )
            raise AssertionError(f"unexpected tool {name}")

        def schemas(self):
            return None

    registry = FakeRegistry()
    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    monkeypatch.setattr("rethlas.agent_loop.build_generation_tool_registry", lambda config: registry)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    config = load_config()
    base_problem = normalize_problem("example", config.paths.generation_dir)
    problem = replace(
        base_problem,
        log_dir=tmp_path / "logs",
        log_file=tmp_path / "logs" / "example.md",
        result_dir=tmp_path / "results",
        memory_dir=tmp_path / "memory",
    )
    request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "model.md",
        model_name="deepseek",
    )
    object.__setattr__(request.model, "supports_streaming", False)

    result = run_native_generation(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        request,
        stream=False,
        max_attempts=3,
    )

    assert result.returncode == 0
    assert registry.verifications == 2
    assert problem.result_dir.joinpath("blueprint.md").read_text(encoding="utf-8") == "fixed proof"
    assert problem.result_dir.joinpath("blueprint_verified.md").read_text(encoding="utf-8") == "fixed proof"
    assert "Previous candidate proof:\nbad proof with a gap" in prompts[2]
    events = list(iter_events(problem.log_dir))
    skipped = [event for event in events if event["event_type"] == "empty_draft_skipped"]
    assert skipped
    assert skipped[0]["kept_previous_draft"] is True


def test_native_generation_defaults_to_eight_attempts_and_passes_timeout(monkeypatch, tmp_path):
    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            message = SimpleNamespace(content="wrong proof", tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeRegistry:
        def __init__(self):
            self.verification_timeouts = []

        def call(self, name, arguments):
            if name in {"memory_init", "memory_append"}:
                return SimpleNamespace(ok=True, result={"ok": True}, error="")
            if name == "verify_proof_service":
                self.verification_timeouts.append(arguments.get("timeout_seconds"))
                return SimpleNamespace(
                    ok=True,
                    result={
                        "verification_report": {
                            "summary": "wrong",
                            "critical_errors": [],
                            "gaps": [{"location": "proof", "issue": "gap"}],
                        },
                        "verdict": "wrong",
                        "repair_hints": "Repair the gap.",
                    },
                    error="",
                )
            raise AssertionError(f"unexpected tool {name}")

        def schemas(self):
            return None

    registry = FakeRegistry()
    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    monkeypatch.setattr("rethlas.agent_loop.build_generation_tool_registry", lambda config: registry)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    config = load_config()
    base_problem = normalize_problem("example", config.paths.generation_dir)
    problem = replace(
        base_problem,
        log_dir=tmp_path / "logs",
        log_file=tmp_path / "logs" / "example.md",
        result_dir=tmp_path / "results",
        memory_dir=tmp_path / "memory",
    )
    request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "model.md",
        model_name="deepseek",
    )
    object.__setattr__(request.model, "supports_streaming", False)
    object.__setattr__(request, "timeout_seconds", 60)

    result = run_native_generation(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        request,
        stream=False,
    )

    assert result.returncode == 1
    assert len(registry.verification_timeouts) == 8
    assert all(isinstance(value, int) and 1 <= value <= 60 for value in registry.verification_timeouts)


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


def test_rethlas_verification_model_overrides_rethlas_model(monkeypatch):
    """RETHLAS_VERIFICATION_MODEL must take precedence over RETHLAS_MODEL for
    the verifier role. This is the env-var users were promised in .env.example
    and README.md but that previously had no effect because nothing read it."""
    monkeypatch.setenv("RETHLAS_MODEL", "mock-verification-correct")
    monkeypatch.setenv("RETHLAS_VERIFICATION_MODEL", "mock-verification-wrong")
    from agents.verification.api.server import run_runtime_verification

    payload = run_runtime_verification("pytest_ver_model_wins", "S", "P")
    assert payload["verdict"] == "wrong"


def test_verification_falls_back_to_rethlas_model_when_verification_model_unset(monkeypatch):
    """When RETHLAS_VERIFICATION_MODEL is unset, the verifier should fall back
    to RETHLAS_MODEL (preserving the prior behavior for users who don't set the
    new env)."""
    monkeypatch.delenv("RETHLAS_VERIFICATION_MODEL", raising=False)
    monkeypatch.setenv("RETHLAS_MODEL", "mock-verification-correct")
    from agents.verification.api.server import run_runtime_verification

    payload = run_runtime_verification("pytest_ver_model_fallback", "S", "P")
    assert payload["verdict"] == "correct"


def test_health_endpoint_reports_active_model(monkeypatch):
    """``/health`` must report which model the verifier will actually use, so
    ``cmd_run`` (and humans) can detect a stale verifier process that was
    started with a different ``RETHLAS_MODEL`` than the current env."""
    monkeypatch.delenv("RETHLAS_VERIFICATION_MODEL", raising=False)
    monkeypatch.setenv("RETHLAS_MODEL", "mock-verification-correct")
    from agents.verification.api.server import health

    payload = health()
    assert payload["status"] == "ok"
    assert payload["model_profile"] == "mock-verification-correct"
    assert payload["provider"] == "mock"
    assert payload["provider_kind"] == "mock"


def test_health_endpoint_prefers_verification_model_env(monkeypatch):
    """When ``RETHLAS_VERIFICATION_MODEL`` is set, ``/health`` must reflect it
    (so we don't print a stale generation model in the run banner)."""
    monkeypatch.setenv("RETHLAS_MODEL", "mock-verification-correct")
    monkeypatch.setenv("RETHLAS_VERIFICATION_MODEL", "mock-verification-wrong")
    from agents.verification.api.server import health

    payload = health()
    assert payload["model_profile"] == "mock-verification-wrong"


def test_health_endpoint_stays_ok_when_model_unresolvable(monkeypatch):
    """If the verifier was started with a misconfigured model (e.g. a preset
    whose API key is missing), ``/health`` must still return 200 with
    ``status=ok`` so the liveness probe works — but flag the resolution
    failure in ``model_error`` so cmd_run can warn instead of pretending."""
    monkeypatch.setenv("RETHLAS_VERIFICATION_MODEL", "does-not-exist")
    from agents.verification.api.server import health

    payload = health()
    assert payload["status"] == "ok"
    assert payload["model_profile"] is None
    assert "does-not-exist" in (payload.get("model_error") or "")
