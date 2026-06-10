"""Follow Rethlas events.jsonl streams and surface them in a compact, human
readable form.

This module is the smallest useful layer of the job control / streaming
plan: it does not require a job registry, a background process, or any
process group juggling. It only needs an events.jsonl path.

Public surface:

- ``iter_follow_events(log_dir, *, from_beginning=True, stop_when=...)``
    yields parsed event dicts as they appear, polling the file.
- ``format_event_compact(event)`` returns a single compact line (``[HH:MM:SS] ...``)
    suitable for live tailing, or ``None`` to skip noisy / unknown events.
- ``format_event_json(event)`` returns the raw JSON for ``--json-events`` mode.
- ``summarize_watch_state(log_dir, result_dir)`` describes the latest known state
    for the ``watch`` exit decision.
- ``find_log_dir(problem_id, generation_dir)`` resolves the log directory for a
    problem id.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional

from .events import event_path, iter_events


DEFAULT_POLL_INTERVAL_SECONDS = 0.25


@dataclass(frozen=True)
class WatchState:
    """Compact view of the latest known state of a problem's run.

    The ``watch`` command uses this to decide when to exit. It is also the
    shape that ``status --watch`` and the results page status badge need.
    """

    last_event: Optional[dict]
    verified_exists: bool
    draft_exists: bool
    process_alive: bool
    pid: Optional[int]
    last_error: Optional[str]

    @property
    def is_succeeded(self) -> bool:
        if not self.verified_exists:
            return False
        if self.last_event is None:
            return True
        event_type = self.last_event.get("event_type")
        if event_type == "verification_finished":
            verdict = self.last_event.get("verdict")
            return verdict == "correct"
        return True

    @property
    def is_failed(self) -> bool:
        if self.last_event is None:
            return False
        if self.last_event.get("event_type") == "run_finished":
            returncode = self.last_event.get("returncode")
            return isinstance(returncode, int) and returncode != 0
        return self.last_event.get("event_type") in {"run_failed", "verification_failed"}


def find_log_dir(problem_id: str, generation_dir: Path) -> Path:
    """Resolve the events directory for a given problem id.

    We accept the same problem id forms the CLI accepts (bare slug, slug/slug,
    ``ns/ns``). The caller is expected to have already normalized the problem
    id; this helper just constructs the path.
    """
    return generation_dir / "logs" / problem_id


def iter_follow_events(
    log_dir: Path,
    *,
    from_beginning: bool = True,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    stop_when: Optional[Callable[[], bool]] = None,
    timeout_seconds: Optional[float] = None,
) -> Iterator[dict]:
    """Yield events from ``events.jsonl`` as they appear.

    Handles three real-world cases that show up after interruptions:

    - the file does not exist yet (parent process is still starting) — we
      wait for it.
    - the file exists but no new content is appended — we poll instead of
      blocking, so the caller can also observe ``stop_when`` / timeouts.
    - the file is appended to in chunks — we seek to the last known byte
      offset and read the new tail.

    ``stop_when`` is checked between polls; if it returns ``True`` the
    generator stops. ``timeout_seconds`` is a hard upper bound; ``None``
    means wait forever.
    """
    target = event_path(log_dir)
    offset = 0
    if not from_beginning and target.is_file():
        offset = target.stat().st_size
    started = time.monotonic()

    while True:
        if target.is_file():
            current_size = target.stat().st_size
            if current_size < offset:
                # File was truncated/rotated. Start over from the top.
                offset = 0
            if current_size > offset:
                with target.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read(current_size - offset)
                offset = current_size
                for line in chunk.splitlines():
                    try:
                        text = line.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    stripped = text.strip()
                    if not stripped:
                        continue
                    try:
                        yield json.loads(stripped)
                    except json.JSONDecodeError:
                        # Skip malformed lines so we don't get stuck.
                        continue
        if stop_when is not None and stop_when():
            return
        if timeout_seconds is not None and (time.monotonic() - started) >= timeout_seconds:
            return
        time.sleep(poll_interval)


def format_event_compact(event: dict, *, show_deltas: bool = False) -> Optional[str]:
    """Format a single event as a compact one-line tail message.

    Returns ``None`` for events we want to suppress in the default
    live-terminal mode (e.g. ``model_delta`` which is too noisy; events
    without a timestamp; or unknown event types). Callers can pass through
    ``None`` results silently.

    ``show_deltas=True`` is for ``tail --verbose``-style modes that want
    the streamed text deltas surfaced. By default we skip them so the
    terminal isn't drowned in token-by-token noise.
    """
    event_type = event.get("event_type")
    if not isinstance(event_type, str):
        return None
    timestamp = _short_timestamp(event.get("timestamp_utc"))
    if event_type == "model_delta":
        if not show_deltas:
            return None
        delta = event.get("delta") or ""
        if not delta:
            return None
        # Replace newlines so each chunk stays on one tail line.
        return f"[{timestamp}] delta: {delta.replace(chr(10), ' ').rstrip()}"
    if event_type == "model_delta_fallback":
        return f"[{timestamp}] streaming fallback: {event.get('error') or '?'}"
    if event_type == "run_planned":
        provider = event.get("provider") or "?"
        model = event.get("model") or "?"
        return f"[{timestamp}] run planned: provider={provider} model={model}"
    if event_type == "run_started":
        return f"[{timestamp}] run started"
    if event_type == "run_resumed":
        return f"[{timestamp}] run resumed: previous_status={event.get('previous_status')}"
    if event_type == "native_generation_started":
        return f"[{timestamp}] native generation started: {event.get('model') or '?'}"
    if event_type == "model_started":
        iteration = event.get("iteration")
        model = event.get("model") or "?"
        if iteration is not None:
            return f"[{timestamp}] model iteration {iteration} started: {model}"
        return f"[{timestamp}] model started: {model}"
    if event_type == "model_finished":
        iteration = event.get("iteration")
        if iteration is not None:
            return f"[{timestamp}] model iteration {iteration} finished"
        return f"[{timestamp}] model finished"
    if event_type == "tool_started":
        iteration = event.get("iteration")
        name = event.get("tool") or "?"
        if iteration is not None:
            return f"[{timestamp}] tool {name} started (iteration {iteration})"
        return f"[{timestamp}] tool {name} started"
    if event_type == "tool_finished":
        iteration = event.get("iteration")
        name = event.get("tool") or "?"
        ok = event.get("ok")
        status = "ok" if ok is True else ("failed" if ok is False else "done")
        if iteration is not None:
            return f"[{timestamp}] tool {name} {status} (iteration {iteration})"
        return f"[{timestamp}] tool {name} {status}"
    if event_type == "verification_started":
        return f"[{timestamp}] verification started"
    if event_type == "verification_finished":
        verdict = event.get("verdict")
        return f"[{timestamp}] verification {verdict or '?'}"
    if event_type == "verification_failed":
        return f"[{timestamp}] verification failed: {event.get('error') or '?'}"
    if event_type == "artifact_written":
        draft = event.get("draft_path")
        verified = event.get("verified_path")
        if verified:
            return f"[{timestamp}] artifact written (draft + verified)"
        if draft:
            return f"[{timestamp}] artifact written (draft)"
        return f"[{timestamp}] artifact written"
    if event_type == "run_interrupted":
        return f"[{timestamp}] run interrupted: {event.get('method') or '?'}"
    if event_type == "run_failed":
        return f"[{timestamp}] run failed: {event.get('error') or '?'}"
    if event_type == "run_finished":
        returncode = event.get("returncode")
        return f"[{timestamp}] run finished: returncode={returncode}"
    if event_type == "run_dry_run_finished":
        return f"[{timestamp}] dry run finished"
    # Unknown event types: still show them so the user can spot regressions.
    return f"[{timestamp}] {event_type}"


def format_event_json(event: dict) -> str:
    """Format a single event as a JSON line for ``--json-events``."""
    return json.dumps(event, ensure_ascii=False)


def latest_event(log_dir: Path) -> Optional[dict]:
    """Return the most recent event in the events log, or ``None``."""
    latest: Optional[dict] = None
    for event in iter_events(log_dir):
        latest = event
    return latest


def summarize_watch_state(
    log_dir: Path,
    result_dir: Path,
    *,
    pid: Optional[int] = None,
    process_alive: Optional[bool] = None,
) -> WatchState:
    """Build a ``WatchState`` for the latest known run state.

    ``pid`` and ``process_alive`` are optional: when omitted, the
    summarizer infers ``process_alive=False`` (we cannot tell).
    """
    last = latest_event(log_dir)
    last_error: Optional[str] = None
    if last is not None:
        event_type = last.get("event_type")
        if event_type in {"run_failed", "verification_failed"}:
            last_error = last.get("error")
    return WatchState(
        last_event=last,
        verified_exists=(result_dir / "blueprint_verified.md").is_file(),
        draft_exists=(result_dir / "blueprint.md").is_file(),
        process_alive=bool(process_alive) if process_alive is not None else bool(pid and _pid_alive(pid)),
        pid=pid,
        last_error=last_error,
    )


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform check for whether ``pid`` is running.

    We use this only for the optional ``process_alive`` field in
    ``WatchState``. ``None`` is the safe default when we cannot tell.
    """
    if pid <= 0:
        return False
    try:
        import os

        if hasattr(os, "kill"):
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False
    return False


def _short_timestamp(iso_timestamp: Optional[str]) -> str:
    """Return the ``HH:MM:SS`` part of an ISO-8601 timestamp, or ``--:--:--``."""
    if not isinstance(iso_timestamp, str):
        return "--:--:--"
    if "T" not in iso_timestamp:
        return iso_timestamp[:8] if len(iso_timestamp) >= 8 else iso_timestamp
    time_part = iso_timestamp.split("T", 1)[1]
    return time_part[:8]
