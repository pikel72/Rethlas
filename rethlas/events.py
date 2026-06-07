from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def event_path(log_dir: Path) -> Path:
    return log_dir / "events.jsonl"


def append_event(log_dir: Path, event_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
    }
    if payload:
        event.update(payload)
    target = event_path(log_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def iter_events(log_dir: Path) -> Iterable[Dict[str, Any]]:
    target = event_path(log_dir)
    if not target.is_file():
        return
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                yield loaded


def latest_events(log_dir: Path, limit: int = 5) -> List[Dict[str, Any]]:
    events = list(iter_events(log_dir))
    return events[-limit:]
