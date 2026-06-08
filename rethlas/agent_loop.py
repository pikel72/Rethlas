from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional

from .config import RethlasConfig
from .events import append_event
from .problems import ProblemPaths
from .references import ReferencePreparation
from .runtime import RuntimeRequest, litellm_completion_kwargs
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

    try:
        draft = _run_litellm_tool_loop(config, problem, refs, request, registry, stream=stream)
    except Exception as exc:
        append_event(
            problem.log_dir,
            "run_failed",
            {"returncode": 1, "error": str(exc)},
        )
        return NativeGenerationResult(
            returncode=1,
            draft_path=draft_path,
            verified_path=verified_path,
            message=f"native generation model call failed: {exc}",
        )

    if verified_path.exists():
        verified_path.unlink()
    draft_path.write_text(draft, encoding="utf-8")
    verification = registry.call(
        "verify_proof_service",
        {"statement": problem.problem_file.read_text(encoding="utf-8"), "proof": draft},
    )
    if verification.ok:
        report = verification.result
        verdict = report.get("verdict") if isinstance(report, dict) else None
        append_event(problem.log_dir, "verification_finished", {"verdict": verdict})
        if verdict == "correct":
            verified_path.write_text(draft, encoding="utf-8")
    else:
        append_event(problem.log_dir, "verification_failed", {"error": verification.error})
    registry.call(
        "memory_append",
        {
            "problem_id": problem.problem_id,
            "channel": "events",
            "record": {"event_type": "artifact_written", "draft_path": str(draft_path)},
        },
    )
    if verified_path.exists():
        message = "native generation wrote blueprint.md and verifier accepted blueprint_verified.md"
    elif verification.ok:
        message = "native generation wrote blueprint.md; verifier did not accept it yet"
    else:
        message = f"native generation wrote blueprint.md; verification was unavailable: {verification.error}"
    append_event(problem.log_dir, "artifact_written", {"draft_path": str(draft_path)})
    return NativeGenerationResult(
        returncode=0,
        draft_path=draft_path,
        verified_path=verified_path,
        message=message,
    )


def _run_litellm_tool_loop(
    config: RethlasConfig,
    problem: ProblemPaths,
    refs: ReferencePreparation,
    request: RuntimeRequest,
    registry,
    *,
    stream: bool,
    max_iterations: int = 8,
) -> str:
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError("LiteLLM backend selected, but the 'litellm' package is not installed.") from exc

    messages: list[dict] = [
        {"role": "system", "content": _native_generation_system_prompt(config)},
        {"role": "user", "content": _native_generation_user_prompt(config, problem, refs)},
    ]
    transcript: list[str] = []
    tools = registry.schemas() if request.model.supports_tools else None

    for iteration in range(1, max_iterations + 1):
        append_event(problem.log_dir, "model_started", {"iteration": iteration, "model": request.model.name})
        completion_kwargs = litellm_completion_kwargs(request)
        completion_kwargs["messages"] = messages
        if tools is not None:
            completion_kwargs["tools"] = tools
        response = litellm.completion(**completion_kwargs)
        message = response.choices[0].message
        content = getattr(message, "content", None) or ""
        tool_calls = getattr(message, "tool_calls", None) or []
        transcript.append(f"\n\n## iteration {iteration}\n{content}")
        if content and stream:
            print(content)

        if not tool_calls:
            request.log_path.parent.mkdir(parents=True, exist_ok=True)
            request.log_path.write_text("\n".join(transcript), encoding="utf-8")
            return content

        messages.append(_message_to_dict(message))
        for tool_call in tool_calls:
            function = tool_call.function
            name = function.name
            args_json = function.arguments or "{}"
            append_event(problem.log_dir, "tool_started", {"iteration": iteration, "tool": name})
            result = registry.call_json(name, args_json)
            payload = result.result if result.ok else {"error": result.error}
            append_event(
                problem.log_dir,
                "tool_finished",
                {"iteration": iteration, "tool": name, "ok": result.ok},
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            )
            transcript.append(f"\n\n### tool {name}\n{json.dumps(payload, ensure_ascii=False)}")

    raise RuntimeError(f"native generation exceeded {max_iterations} model iterations")


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


def _native_generation_system_prompt(config: RethlasConfig) -> str:
    return (config.paths.generation_dir / "AGENTS.md").read_text(encoding="utf-8")


def _native_generation_user_prompt(
    config: RethlasConfig,
    problem: ProblemPaths,
    refs: ReferencePreparation,
) -> str:
    problem_text = problem.problem_file.read_text(encoding="utf-8")
    reference_text = _read_reference_text(problem.reference_dir)
    return (
        f"Problem id: {problem.problem_id}\n"
        f"Problem file: {problem.problem_path}\n"
        f"Reference policy: {refs.prompt_suffix}\n\n"
        "Use the available tools for memory and verification. "
        "When you have a candidate proof blueprint, return only markdown for blueprint.md.\n\n"
        "Problem statement:\n"
        f"{problem_text}\n\n"
        "Reference excerpts:\n"
        f"{reference_text}\n"
    )


def _message_to_dict(message) -> dict:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return message
    payload = {"role": "assistant", "content": getattr(message, "content", "")}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = [
            call.model_dump(exclude_none=True) if hasattr(call, "model_dump") else call
            for call in tool_calls
        ]
    return payload


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
