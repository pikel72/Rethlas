from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from rethlas.events import append_event
from rethlas.follow import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    format_event_compact,
    format_event_json,
    iter_follow_events,
    latest_event,
    summarize_watch_state,
)


def test_format_event_compact_handles_common_event_types():
    timestamp = "2026-06-08T14:50:18+00:00"
    assert format_event_compact({"event_type": "run_planned", "timestamp_utc": timestamp, "provider": "mock", "model": "mock-gen"}) == \
        "[14:50:18] run planned: provider=mock model=mock-gen"
    assert format_event_compact({"event_type": "model_started", "timestamp_utc": timestamp, "iteration": 3, "model": "deepseek"}) == \
        "[14:50:18] model iteration 3 started: deepseek"
    assert format_event_compact({"event_type": "tool_started", "timestamp_utc": timestamp, "iteration": 2, "tool": "memory_append"}) == \
        "[14:50:18] tool memory_append started (iteration 2)"
    assert format_event_compact({"event_type": "tool_finished", "timestamp_utc": timestamp, "iteration": 2, "tool": "memory_append", "ok": True}) == \
        "[14:50:18] tool memory_append ok (iteration 2)"
    assert format_event_compact({"event_type": "tool_finished", "timestamp_utc": timestamp, "tool": "x", "ok": False}) == \
        "[14:50:18] tool x failed"
    assert format_event_compact({"event_type": "verification_finished", "timestamp_utc": timestamp, "verdict": "correct"}) == \
        "[14:50:18] verification correct"
    assert format_event_compact({"event_type": "run_failed", "timestamp_utc": timestamp, "error": "boom"}) == \
        "[14:50:18] run failed: boom"
    assert format_event_compact({"event_type": "run_finished", "timestamp_utc": timestamp, "returncode": 0}) == \
        "[14:50:18] run finished: returncode=0"


def test_format_event_compact_skips_unknown_with_fallback():
    line = format_event_compact({"event_type": "future_event_type", "timestamp_utc": "2026-06-08T01:02:03+00:00"})
    assert line == "[01:02:03] future_event_type"


def test_format_event_compact_returns_none_for_missing_type():
    assert format_event_compact({}) is None
    assert format_event_compact({"event_type": None}) is None


def test_format_event_compact_skips_model_delta_by_default():
    assert format_event_compact({"event_type": "model_delta", "delta": "hello"}) is None


def test_format_event_compact_includes_model_delta_when_requested():
    line = format_event_compact(
        {"event_type": "model_delta", "delta": "hello", "timestamp_utc": "2026-06-08T03:04:05+00:00"},
        show_deltas=True,
    )
    assert line == "[03:04:05] delta: hello"


def test_format_event_compact_collapses_newlines_in_delta():
    line = format_event_compact(
        {"event_type": "model_delta", "delta": "line1\nline2", "timestamp_utc": "2026-06-08T03:04:05+00:00"},
        show_deltas=True,
    )
    assert "\n" not in line
    assert "line1" in line and "line2" in line


def test_format_event_compact_handles_model_delta_fallback():
    line = format_event_compact(
        {"event_type": "model_delta_fallback", "error": "stream unsupported", "timestamp_utc": "2026-06-08T03:04:05+00:00"}
    )
    assert line == "[03:04:05] streaming fallback: stream unsupported"


def test_format_event_compact_handles_model_finished():
    line = format_event_compact(
        {"event_type": "model_finished", "iteration": 2, "timestamp_utc": "2026-06-08T03:04:05+00:00"}
    )
    assert line == "[03:04:05] model iteration 2 finished"


def test_format_event_json_roundtrip():
    event = {"event_type": "run_started", "timestamp_utc": "2026-06-08T00:00:00+00:00"}
    rendered = format_event_json(event)
    assert json.loads(rendered) == event


def test_iter_follow_events_reads_existing_events(tmp_path):
    append_event(tmp_path, "run_planned", {"provider": "mock", "model": "mock-gen"})
    append_event(tmp_path, "run_started", {})
    append_event(tmp_path, "run_finished", {"returncode": 0})

    events = list(iter_follow_events(tmp_path, from_beginning=True, timeout_seconds=0.5))
    assert [e["event_type"] for e in events] == [
        "run_planned",
        "run_started",
        "run_finished",
    ]


def test_iter_follow_events_yields_new_events_as_they_appear(tmp_path):
    append_event(tmp_path, "run_started", {})

    seen: list[dict] = []
    stop = threading.Event()

    def append_later() -> None:
        time.sleep(0.1)
        append_event(tmp_path, "model_started", {"iteration": 1})
        time.sleep(0.05)
        append_event(tmp_path, "model_finished", {"iteration": 1})
        time.sleep(0.05)
        stop.set()

    worker = threading.Thread(target=append_later, daemon=True)
    worker.start()

    for event in iter_follow_events(
        tmp_path,
        from_beginning=True,
        poll_interval=0.02,
        stop_when=lambda: stop.is_set(),
        timeout_seconds=2.0,
    ):
        seen.append(event)
    worker.join(timeout=2.0)

    assert [e["event_type"] for e in seen] == [
        "run_started",
        "model_started",
        "model_finished",
    ]


def test_iter_follow_events_from_beginning_false_skips_existing(tmp_path):
    append_event(tmp_path, "run_started", {})
    append_event(tmp_path, "run_finished", {"returncode": 0})

    events = list(
        iter_follow_events(
            tmp_path,
            from_beginning=False,
            poll_interval=0.02,
            timeout_seconds=0.1,
        )
    )
    assert events == []


def test_iter_follow_events_respects_stop_when(tmp_path):
    append_event(tmp_path, "run_started", {})
    container: list[dict] = []
    seen = list(
        iter_follow_events(
            tmp_path,
            poll_interval=0.01,
            stop_when=lambda: len(container) >= 1,
            timeout_seconds=1.0,
        )
    )
    container.extend(seen)
    assert len(seen) == 1
    assert seen[0]["event_type"] == "run_started"


def test_iter_follow_events_waits_for_file_to_appear(tmp_path):
    target = tmp_path / "later"
    seen: list[str] = []

    def append_later() -> None:
        time.sleep(0.1)
        append_event(target, "run_started", {})

    worker = threading.Thread(target=append_later, daemon=True)
    worker.start()
    try:
        for event in iter_follow_events(
            target,
            poll_interval=0.02,
            stop_when=lambda: len(seen) >= 1,
            timeout_seconds=2.0,
        ):
            seen.append(event)
    finally:
        worker.join(timeout=2.0)
    assert [e["event_type"] for e in seen] == ["run_started"]


def test_iter_follow_events_handles_truncation(tmp_path):
    append_event(tmp_path, "run_started", {})
    append_event(tmp_path, "run_finished", {"returncode": 0})

    # Truncate by overwriting with fewer events.
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"event_type": "model_started", "iteration": 1, "timestamp_utc": "2026-06-08T00:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    append_event(tmp_path, "model_finished", {"iteration": 1})

    events = list(
        iter_follow_events(
            tmp_path,
            from_beginning=True,
            poll_interval=0.02,
            timeout_seconds=0.2,
        )
    )
    assert [e["event_type"] for e in events] == ["model_started", "model_finished"]


def test_latest_event_returns_most_recent(tmp_path):
    append_event(tmp_path, "run_started", {})
    append_event(tmp_path, "model_started", {"iteration": 1})
    append_event(tmp_path, "model_finished", {"iteration": 1})
    latest = latest_event(tmp_path)
    assert latest is not None
    assert latest["event_type"] == "model_finished"


def test_latest_event_returns_none_when_missing(tmp_path):
    assert latest_event(tmp_path) is None


def test_summarize_watch_state_succeeded_when_verified_exists(tmp_path):
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "blueprint_verified.md").write_text("# ok", encoding="utf-8")
    append_event(tmp_path, "verification_finished", {"verdict": "correct"})
    state = summarize_watch_state(tmp_path, result_dir)
    assert state.is_succeeded
    assert state.verified_exists
    assert state.last_error is None


def test_summarize_watch_state_failed_when_run_failed(tmp_path):
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    append_event(tmp_path, "run_failed", {"error": "boom"})
    state = summarize_watch_state(tmp_path, result_dir)
    assert state.is_failed
    assert state.last_error == "boom"


def test_summarize_watch_state_pending_when_no_terminal_event(tmp_path):
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    append_event(tmp_path, "model_started", {"iteration": 1})
    state = summarize_watch_state(tmp_path, result_dir)
    assert not state.is_succeeded
    assert not state.is_failed
    assert state.last_event is not None


def test_default_poll_interval_is_positive():
    assert DEFAULT_POLL_INTERVAL_SECONDS > 0
