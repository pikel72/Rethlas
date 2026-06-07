from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import RethlasConfig
from .events import append_event
from .problems import ProblemPaths
from .references import ReferencePreparation
from .runtime import RuntimeRequest, backend_for
from .tools import build_generation_tool_registry


@dataclass(frozen=True)
class NativeGenerationResult:
    returncode: int
    draft_path: Path
    verified_path: Path
    message: str


def run_native_generation(
    config: RethlasConfig,
    problem: ProblemPaths,
    refs: ReferencePreparation,
    request: RuntimeRequest,
    *,
    stream: bool = True,
) -> NativeGenerationResult:
    registry = build_generation_tool_registry(config)
    append_event(
        problem.log_dir,
        "native_generation_started",
        {"provider": request.provider.name, "model": request.model.name},
    )
    registry.call(
        "memory_init",
        {
            "problem_id": problem.problem_id,
            "meta": {
                "problem_file": problem.problem_path,
                "runtime_provider": request.provider.name,
                "runtime_model": request.model.name,
                "native_loop": True,
            },
        },
    )
    registry.call(
        "memory_append",
        {
            "problem_id": problem.problem_id,
            "channel": "events",
            "record": {"event_type": "native_generation_started", "model": request.model.name},
        },
    )

    problem.result_dir.mkdir(parents=True, exist_ok=True)
    problem.log_dir.mkdir(parents=True, exist_ok=True)
    draft_path = problem.result_dir / "blueprint.md"
    verified_path = problem.result_dir / "blueprint_verified.md"

    if request.provider.kind == "mock":
        proof = _mock_blueprint(problem)
        draft_path.write_text(proof, encoding="utf-8")
        verified_path.write_text(proof, encoding="utf-8")
        request.log_path.write_text("mock native generation completed\n", encoding="utf-8")
        registry.call(
            "memory_append",
            {
                "problem_id": problem.problem_id,
                "channel": "events",
                "record": {
                    "event_type": "artifact_written",
                    "draft_path": str(draft_path),
                    "verified_path": str(verified_path),
                },
            },
        )
        append_event(
            problem.log_dir,
            "artifact_written",
            {"draft_path": str(draft_path), "verified_path": str(verified_path), "mock": True},
        )
        return NativeGenerationResult(
            returncode=0,
            draft_path=draft_path,
            verified_path=verified_path,
            message="mock native generation completed",
        )

    if request.provider.kind != "litellm":
        return NativeGenerationResult(
            returncode=2,
            draft_path=draft_path,
            verified_path=verified_path,
            message=f"native generation does not support provider kind {request.provider.kind}",
        )

    prompt = _native_generation_prompt(config, problem, refs)
    runtime_request = RuntimeRequest(
        role="native-generation",
        cwd=request.cwd,
        prompt=prompt,
        log_path=request.log_path,
        model=request.model,
        provider=request.provider,
        timeout_seconds=request.timeout_seconds,
    )
    runtime_result = backend_for(request.provider).run(runtime_request, stream=stream)
    if runtime_result.returncode != 0:
        append_event(
            problem.log_dir,
            "run_failed",
            {"returncode": runtime_result.returncode, "error": runtime_result.error},
        )
        return NativeGenerationResult(
            returncode=runtime_result.returncode,
            draft_path=draft_path,
            verified_path=verified_path,
            message=runtime_result.error or "native generation model call failed",
        )

    draft_path.write_text(runtime_result.output_text, encoding="utf-8")
    registry.call(
        "memory_append",
        {
            "problem_id": problem.problem_id,
            "channel": "events",
            "record": {"event_type": "artifact_written", "draft_path": str(draft_path)},
        },
    )
    append_event(problem.log_dir, "artifact_written", {"draft_path": str(draft_path)})
    return NativeGenerationResult(
        returncode=0,
        draft_path=draft_path,
        verified_path=verified_path,
        message="native generation wrote blueprint.md; verification loop is not implemented for LiteLLM generation yet",
    )


def _native_generation_prompt(
    config: RethlasConfig,
    problem: ProblemPaths,
    refs: ReferencePreparation,
) -> str:
    agent_instructions = (config.paths.generation_dir / "AGENTS.md").read_text(encoding="utf-8")
    problem_text = problem.problem_file.read_text(encoding="utf-8")
    reference_text = _read_reference_text(problem.reference_dir)
    return (
        "You are running Rethlas native generation without Codex CLI.\n\n"
        "Follow these agent instructions as policy:\n"
        f"{agent_instructions}\n\n"
        f"Problem id: {problem.problem_id}\n"
        f"Problem file: {problem.problem_path}\n"
        f"Reference policy: {refs.prompt_suffix}\n\n"
        "Problem statement:\n"
        f"{problem_text}\n\n"
        "Reference excerpts:\n"
        f"{reference_text}\n\n"
        "Write a markdown proof blueprint. Return only the markdown content for blueprint.md."
    )


def _read_reference_text(reference_dir: Path, max_chars: int = 12000) -> str:
    if not reference_dir.is_dir():
        return ""
    chunks: list[str] = []
    remaining = max_chars
    for path in sorted(reference_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".tex"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > remaining:
            text = text[:remaining]
        chunks.append(f"\n--- {path.relative_to(reference_dir)} ---\n{text}")
        remaining -= len(text)
        if remaining <= 0:
            break
    return "\n".join(chunks)


def _mock_blueprint(problem: ProblemPaths) -> str:
    statement = problem.problem_file.read_text(encoding="utf-8")
    return (
        "# theorem mock-main\n\n"
        "## statement\n"
        f"{statement.strip()}\n\n"
        "## proof\n"
        "This is a deterministic mock proof blueprint used to test the native generation loop.\n"
    )
