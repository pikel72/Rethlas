from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ProblemPaths:
    problem_file: Path
    problem_path: str
    problem_id: str
    reference_dir: Path
    log_dir: Path
    log_file: Path
    memory_dir: Path
    result_dir: Path


def normalize_problem(problem: Optional[str], generation_dir: Path) -> ProblemPaths:
    problem_path = (problem or "example").replace("\\", "/").strip()
    if not problem_path:
        problem_path = "example"

    candidate = Path(problem_path)
    if candidate.is_absolute():
        raise ValueError(f"Problem path must be relative to agents/generation: {problem_path}")

    parts = [part for part in problem_path.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError(f"Problem path must not contain '..': {problem_path}")

    normalized = "/".join(parts)
    if not normalized.startswith("data/"):
        normalized = f"data/{normalized}"

    if not normalized.endswith(".md"):
        suffix = Path(normalized).suffix
        if suffix:
            raise ValueError(f"Problem path must be a markdown file under data/: {problem_path}")
        normalized = f"{normalized}.md"

    if not normalized.startswith("data/"):
        raise ValueError(f"Problem path must be under data/: {problem_path}")

    problem_file = (generation_dir / normalized).resolve()
    generation_root = generation_dir.resolve()
    if not problem_file.is_relative_to(generation_root):
        raise ValueError(f"Problem path resolves outside agents/generation: {problem_path}")
    if not problem_file.is_file():
        raise FileNotFoundError(f"Problem file not found: {problem_file}")

    problem_id = normalized[len("data/") : -len(".md")]
    problem_name = Path(normalized).stem
    reference_dir = generation_dir / f"data/{problem_id}.refs"
    log_dir = generation_dir / "logs" / problem_id

    return ProblemPaths(
        problem_file=problem_file,
        problem_path=normalized,
        problem_id=problem_id,
        reference_dir=reference_dir,
        log_dir=log_dir,
        log_file=log_dir / f"{problem_name}.md",
        memory_dir=generation_dir / "memory" / problem_id,
        result_dir=generation_dir / "results" / problem_id,
    )
