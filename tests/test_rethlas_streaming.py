from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from rethlas.agent_loop import (
    _extract_stream_delta,
    _maybe_trigger_summarizer,
    _merge_streaming_tool_calls,
    _message_to_dict_assistant,
    _native_generation_system_prompt,
    _native_generation_user_prompt,
    _run_litellm_tool_loop,
    _run_summarizer,
    _stream_lock,
    _SummarizerState,
    _summarizer_litellm_kwargs,
    _summarizer_model_name,
    THINKING_SUMMARIZER_MAX_INFLIGHT,
    THINKING_SUMMARIZER_TRIGGER_CHARS,
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


def test_message_to_dict_assistant_serializes_object_tool_calls():
    tool_call = SimpleNamespace(
        id="abc",
        type="function",
        function=SimpleNamespace(name="memory_search", arguments='{"query":"x"}'),
    )
    payload = _message_to_dict_assistant("", [tool_call])
    assert payload["tool_calls"][0]["id"] == "abc"
    assert payload["tool_calls"][0]["function"]["name"] == "memory_search"
    assert payload["tool_calls"][0]["function"]["arguments"] == '{"query":"x"}'


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
    assert "read_run_context" in prompt
    assert "read_problem_reference" in prompt
    assert "search_math_results" in prompt
    assert "fetch_math_source" in prompt
    assert "record_math_note" in prompt
    assert "search_memory" in prompt


def test_native_generation_system_prompt_uses_compact_native_policy():
    prompt = _native_generation_system_prompt(load_config())
    assert "native Rethlas mathematical proof agent" in prompt
    assert "read_run_context" in prompt
    assert "search_math_results" in prompt
    assert "fetch_math_source" in prompt
    assert "record_math_note" in prompt
    assert "Python controller writes blueprint.md" in prompt
    assert "$search-math-results" not in prompt
    assert "recursive sub-agent" not in prompt.lower()


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


# ── Thinking summarizer tests ──────────────────────────────────────────


class TestSummarizerModelName:
    def test_returns_none_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("RETHLAS_THINKING_SUMMARIZER_MODEL", raising=False)
        assert _summarizer_model_name(None) is None

    def test_returns_none_when_env_is_empty_string(self, monkeypatch):
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "")
        assert _summarizer_model_name(None) is None

    def test_returns_value_when_env_is_set(self, monkeypatch):
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        assert _summarizer_model_name(None) == "deepseek-chat"


class TestSummarizerState:
    def test_initial_state(self):
        s = _SummarizerState()
        assert s.accumulated_chars == 0
        assert s.next_trigger_at == THINKING_SUMMARIZER_TRIGGER_CHARS
        assert s._in_flight == 0


class TestMaybeTriggerSummarizer:
    def _fake_request(self):
        from rethlas.config import load_config
        from rethlas.runtime import build_request
        config = load_config()
        return build_request(
            config, role="generation", cwd=config.repo_root,
            prompt="test", log_path=config.repo_root / "log.md",
            model_name="mock-generation",
        )

    def test_no_op_below_threshold(self, monkeypatch):
        """When accumulated chars haven't reached the trigger, nothing happens."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        state = _SummarizerState()
        state.accumulated_chars = THINKING_SUMMARIZER_TRIGGER_CHARS - 100
        state.next_trigger_at = THINKING_SUMMARIZER_TRIGGER_CHARS
        _maybe_trigger_summarizer(None, state, "x" * 50, 1, "test")
        assert state._in_flight == 0  # nothing fired

    def test_skips_when_in_flight_at_max(self, monkeypatch):
        """When max in-flight calls are already running, don't start another."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        state = _SummarizerState()
        state._in_flight = THINKING_SUMMARIZER_MAX_INFLIGHT
        state.accumulated_chars = THINKING_SUMMARIZER_TRIGGER_CHARS
        state.next_trigger_at = THINKING_SUMMARIZER_TRIGGER_CHARS
        _maybe_trigger_summarizer(None, state, "x" * 100, 1, "test")
        assert state._in_flight == THINKING_SUMMARIZER_MAX_INFLIGHT  # unchanged

    def test_skips_when_kwargs_raise(self, monkeypatch):
        """When model resolution fails, silently skip the summarizer call."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        state = _SummarizerState()
        state.accumulated_chars = THINKING_SUMMARIZER_TRIGGER_CHARS + 1
        state.next_trigger_at = THINKING_SUMMARIZER_TRIGGER_CHARS
        # Pass None as request → _summarizer_litellm_kwargs will fail
        _maybe_trigger_summarizer(None, state, "x" * 500, 1, "test")
        assert state._in_flight == 0  # silent skip, no crash

    def test_fires_thread_and_advances_trigger(self, monkeypatch):
        """When threshold is crossed and in-flight < max, launch a thread."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        state = _SummarizerState()
        old = THINKING_SUMMARIZER_TRIGGER_CHARS
        state.accumulated_chars = old
        state.next_trigger_at = old
        req = self._fake_request()
        # The function adds len(reasoning_text_recent) to accumulated_chars
        # first, then sets next_trigger = new_accumulated + trigger
        delta = THINKING_SUMMARIZER_TRIGGER_CHARS
        _maybe_trigger_summarizer(req, state, "x" * delta, 1, "test")
        assert state._in_flight == 1
        # accumulated grew by delta, so next_trigger = (old + delta) + trigger
        assert state.next_trigger_at == old + delta + THINKING_SUMMARIZER_TRIGGER_CHARS


class TestRunSummarizer:
    def test_decrements_in_flight_on_success(self, monkeypatch):
        """Even with a fake successful call, in_flight should be decremented."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        state = _SummarizerState()
        state._in_flight = 1
        kwargs: dict = {"model": "deepseek-chat", "max_tokens": 128, "temperature": 0.3}
        _run_summarizer(kwargs, state, "some reasoning text", 1, "test")
        # Without a real LiteLLM module, this should catch the exception
        # and still decrement.
        assert state._in_flight == 0

    def test_decrements_in_flight_on_failure(self, monkeypatch):
        """On any failure, the silent fallback must still decrement the counter."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        state = _SummarizerState()
        state._in_flight = 2
        # Empty kwargs, no real API — will fail, but in_flight must recover.
        _run_summarizer({}, state, "text", 1, "test")
        assert state._in_flight == 1

    def test_does_not_crash_when_litellm_unavailable(self):
        """Graceful no-op when litellm isn't even importable."""
        state = _SummarizerState()
        state._in_flight = 1
        kwargs: dict = {"model": "deepseek-chat"}
        _run_summarizer(kwargs, state, "text", 1, "test")
        assert state._in_flight == 0

    def test_successful_summary_prints_to_stdout(self, capsys, monkeypatch):
        """A successful LiteLLM call prints the thinking summary."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        import litellm
        import sys as _sys

        class FakeMessage:
            content = "the model is analyzing the prime factorization lemma"

        class FakeChoice:
            message = FakeMessage()

        class FakeResponse:
            choices = [FakeChoice()]

        original = getattr(litellm, "completion", None)
        try:
            litellm.completion = lambda **kw: FakeResponse()
            state = _SummarizerState()
            state._in_flight = 1
            kwargs = {"model": "deepseek-chat"}
            _run_summarizer(kwargs, state, "test reasoning" * 100, 1, "ns/ns")
            out = capsys.readouterr().out
            assert "Thinking:" in out
            assert "prime factorization lemma" in out
            assert "[ns/ns iter 1]" in out
        finally:
            if original is not None:
                litellm.completion = original
            else:
                del litellm.completion

    def test_silent_fallback_on_summary_api_error(self, monkeypatch):
        """If the summary API returns empty content, no output is printed."""
        monkeypatch.setenv("RETHLAS_THINKING_SUMMARIZER_MODEL", "deepseek-chat")
        import litellm

        class FakeMessage:
            content = ""

        class FakeChoice:
            message = FakeMessage()

        class FakeResponse:
            choices = [FakeChoice()]

        original = getattr(litellm, "completion", None)
        try:
            litellm.completion = lambda **kw: FakeResponse()
            state = _SummarizerState()
            state._in_flight = 1
            kwargs = {"model": "deepseek-chat"}
            _run_summarizer(kwargs, state, "test", 1, "test")
            assert state._in_flight == 0
        finally:
            if original is not None:
                litellm.completion = original
            else:
                del litellm.completion
