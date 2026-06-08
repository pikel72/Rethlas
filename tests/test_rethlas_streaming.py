from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from rethlas.agent_loop import (
    _extract_stream_delta,
    _merge_streaming_tool_calls,
    _message_to_dict_assistant,
    _native_generation_user_prompt,
    _run_litellm_tool_loop,
)
from rethlas.config import load_config
from rethlas.events import append_event, iter_events
from rethlas.problems import normalize_problem
from rethlas.references import ReferencePreparation
from rethlas.runtime import build_request


def test_extract_stream_delta_returns_text_from_delta_content():
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))]
    )
    assert _extract_stream_delta(chunk) == "hello"


def test_extract_stream_delta_returns_empty_when_no_choices():
    chunk = SimpleNamespace(choices=[])
    assert _extract_stream_delta(chunk) == ""


def test_extract_stream_delta_handles_content_list():
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=[{"text": "ab"}, {"text": "cd"}]))]
    )
    assert _extract_stream_delta(chunk) == "abcd"


def test_extract_stream_delta_handles_missing_delta():
    chunk = SimpleNamespace(choices=[SimpleNamespace(delta=None)])
    assert _extract_stream_delta(chunk) == ""


def test_merge_streaming_tool_calls_accumulates_by_index():
    incoming_a = [
        SimpleNamespace(index=0, id="abc", function=SimpleNamespace(name="memory_init", arguments="{\"a\"")),
    ]
    incoming_b = [
        SimpleNamespace(index=0, function=SimpleNamespace(arguments=":1}")),
    ]
    merged = _merge_streaming_tool_calls([], incoming_a)
    merged = _merge_streaming_tool_calls(merged, incoming_b)
    assert len(merged) == 1
    assert merged[0]["function"]["name"] == "memory_init"
    assert merged[0]["function"]["arguments"] == '{"a":1}'
    assert merged[0]["id"] == "abc"


def test_message_to_dict_assistant_serializes_streamed_tool_calls():
    streamed = [
        {"_index": 0, "id": "abc", "type": "function", "function": {"name": "x", "arguments": "{}"}},
    ]
    payload = _message_to_dict_assistant("hi", streamed)
    assert payload["role"] == "assistant"
    assert payload["content"] == "hi"
    assert payload["tool_calls"][0]["function"]["name"] == "x"


def test_litellm_tool_loop_uses_streaming_and_emits_model_delta(monkeypatch, tmp_path):
    """When streaming is requested, the tool loop should consume a streamed
    response, emit one ``model_delta`` event per text chunk, and write
    a final ``model_finished`` event."""
    captured: dict = {}

    def fake_completion(**kwargs):
        captured["stream"] = kwargs.get("stream")
        def gen():
            for token in ["draft ", "proof"]:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=token), finish_reason=None)],
                )
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=""), finish_reason="stop")],
            )
        return gen()

    class FakeLiteLLM:
        completion = staticmethod(fake_completion)

    class FakeRegistry:
        def schemas(self):
            return None

    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)

    config = load_config()
    base_problem = normalize_problem("example", config.paths.generation_dir)
    # Use a tmp_path log_dir so the test does not pollute the real log.
    from dataclasses import replace
    problem = replace(base_problem, log_dir=tmp_path / "logs", log_file=tmp_path / "logs" / "x.md")
    runtime_request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "log.md",
        model_name="mock-generation",
    )
    # The mock model advertises supports_streaming=false because the mock
    # backend has no streaming endpoint. For this test we want to exercise
    # the streaming code path, so override the flag.
    object.__setattr__(runtime_request.model, "supports_streaming", True)
    draft = _run_litellm_tool_loop(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        runtime_request,
        FakeRegistry(),
        stream=False,
    )
    assert draft == "draft proof"
    assert captured["stream"] is True

    deltas = [e for e in iter_events(problem.log_dir) if e["event_type"] == "model_delta"]
    assert [d["delta"] for d in deltas] == ["draft ", "proof"]
    finished = [e for e in iter_events(problem.log_dir) if e["event_type"] == "model_finished"]
    assert finished
    assert finished[-1]["chars"] == len("draft proof")


def test_native_generation_user_prompt_omits_resume_note_by_default():
    config = load_config()
    problem = normalize_problem("example", config.paths.generation_dir)
    prompt = _native_generation_user_prompt(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
    )
    assert "resuming a previous attempt" not in prompt


def test_native_generation_user_prompt_includes_resume_note_when_resume_true():
    config = load_config()
    problem = normalize_problem("example", config.paths.generation_dir)
    prompt = _native_generation_user_prompt(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        resume=True,
    )
    assert "resuming a previous attempt" in prompt
    assert "memory tools" in prompt


def test_litellm_tool_loop_falls_back_when_streaming_fails(monkeypatch, tmp_path):
    """If streaming raises (e.g. provider doesn't support it), the loop
    should fall back to a single-shot completion and still complete the
    run."""
    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            if kwargs.get("stream"):
                raise RuntimeError("streaming unsupported in this region")
            message = SimpleNamespace(content="fallback draft", tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeRegistry:
        def schemas(self):
            return None

    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)

    config = load_config()
    base_problem = normalize_problem("example", config.paths.generation_dir)
    from dataclasses import replace
    problem = replace(base_problem, log_dir=tmp_path / "logs", log_file=tmp_path / "logs" / "x.md")
    runtime_request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "log.md",
        model_name="mock-generation",
    )
    object.__setattr__(runtime_request.model, "supports_streaming", True)
    draft = _run_litellm_tool_loop(
        config,
        problem,
        ReferencePreparation(reference_dir=problem.reference_dir, exists=False),
        runtime_request,
        FakeRegistry(),
        stream=False,
    )
    assert draft == "fallback draft"
    fallback = [
        e for e in iter_events(problem.log_dir)
        if e["event_type"] == "model_delta_fallback"
    ]
    assert fallback
    assert "streaming unsupported" in fallback[0]["error"]
