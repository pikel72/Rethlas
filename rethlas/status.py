from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .problems import ProblemPaths
from .events import latest_events


@dataclass(frozen=True)
class ProblemStatus:
    problem: ProblemPaths
    log_exists: bool
    memory_exists: bool
    result_exists: bool
    draft_exists: bool
    verified_exists: bool
    latest_log_line: Optional[str]
    memory_files: List[Path]
    result_files: List[Path]
    latest_events: List[dict]


def _latest_nonempty_line(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    latest: Optional[str] = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.replace("\x00", "").strip()
            if stripped:
                latest = stripped
    return latest


def inspect_problem_status(problem: ProblemPaths) -> ProblemStatus:
    memory_files = sorted(path for path in problem.memory_dir.rglob("*") if path.is_file()) if problem.memory_dir.is_dir() else []
    result_files = sorted(path for path in problem.result_dir.rglob("*") if path.is_file()) if problem.result_dir.is_dir() else []
    return ProblemStatus(
        problem=problem,
        log_exists=problem.log_file.is_file(),
        memory_exists=problem.memory_dir.is_dir(),
        result_exists=problem.result_dir.is_dir(),
        draft_exists=(problem.result_dir / "blueprint.md").is_file(),
        verified_exists=(problem.result_dir / "blueprint_verified.md").is_file(),
        latest_log_line=_latest_nonempty_line(problem.log_file),
        memory_files=memory_files,
        result_files=result_files,
        latest_events=latest_events(problem.log_dir),
    )
