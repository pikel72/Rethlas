from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
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
    resume: bool = False,
    max_attempts: int = 4,
) -> NativeGenerationResult:
    registry = build_generation_tool_registry(config)
    append_event(
        problem.log_dir,
        "native_generation_started",
        {"provider": request.provider.name, "model": request.model.name, "resumed": resume},
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
                "resumed": resume,
            },
        },
    )
    registry.call(
        "memory_append",
        {
            "problem_id": problem.problem_id,
            "channel": "events",
            "record": {
                "event_type": "native_generation_started",
                "model": request.model.name,
                "resumed": resume,
            },
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

    if verified_path.exists():
        verified_path.unlink()

    previous_draft: Optional[str] = None
    previous_verification: Optional[dict] = None
    statement = problem.problem_file.read_text(encoding="utf-8")
    attempts = max(1, max_attempts)

    for attempt in range(1, attempts + 1):
        phase = "repair" if previous_verification is not None else "draft"
        append_event(
            problem.log_dir,
            "native_attempt_started",
            {"attempt": attempt, "max_attempts": attempts, "phase": phase},
        )
        try:
            draft = _run_litellm_tool_loop(
                config,
                problem,
                refs,
                request,
                registry,
                stream=stream,
                resume=resume,
                attempt=attempt,
                previous_draft=previous_draft,
                previous_verification=previous_verification,
            )
        except Exception as exc:
            append_event(
                problem.log_dir,
                "run_failed",
                {"returncode": 1, "attempt": attempt, "error": str(exc)},
            )
            return NativeGenerationResult(
                returncode=1,
                draft_path=draft_path,
                verified_path=verified_path,
                message=f"native generation model call failed: {exc}",
            )

        previous_draft = draft
        draft_path.write_text(draft, encoding="utf-8")
        append_event(
            problem.log_dir,
            "candidate_written",
            {"attempt": attempt, "draft_path": str(draft_path), "chars": len(draft)},
        )
        registry.call(
            "memory_append",
            {
                "problem_id": problem.problem_id,
                "channel": "events",
                "record": {
                    "event_type": "candidate_written",
                    "attempt": attempt,
                    "draft_path": str(draft_path),
                    "chars": len(draft),
                },
            },
        )

        append_event(problem.log_dir, "verification_started", {"attempt": attempt})
        verification = registry.call(
            "verify_proof_service",
            {"statement": statement, "proof": draft},
        )
        if not verification.ok:
            append_event(
                problem.log_dir,
                "verification_failed",
                {"attempt": attempt, "error": verification.error},
            )
            append_event(
                problem.log_dir,
                "run_failed",
                {"returncode": 1, "attempt": attempt, "error": verification.error},
            )
            return NativeGenerationResult(
                returncode=1,
                draft_path=draft_path,
                verified_path=verified_path,
                message=f"native generation wrote blueprint.md; verification was unavailable: {verification.error}",
            )

        report = verification.result if isinstance(verification.result, dict) else {}
        verdict = report.get("verdict")
        previous_verification = report
        append_event(
            problem.log_dir,
            "verification_finished",
            {"attempt": attempt, "verdict": verdict},
        )
        registry.call(
            "memory_append",
            {
                "problem_id": problem.problem_id,
                "channel": "verification_reports",
                "record": {
                    "attempt": attempt,
                    "verdict": verdict,
                    "verification_report": report.get("verification_report", {}),
                    "repair_hints": report.get("repair_hints", ""),
                },
            },
        )

        if verdict == "correct":
            verified_path.write_text(draft, encoding="utf-8")
            registry.call(
                "memory_append",
                {
                    "problem_id": problem.problem_id,
                    "channel": "events",
                    "record": {
                        "event_type": "artifact_written",
                        "attempt": attempt,
                        "draft_path": str(draft_path),
                        "verified_path": str(verified_path),
                    },
                },
            )
            append_event(
                problem.log_dir,
                "artifact_written",
                {
                    "attempt": attempt,
                    "draft_path": str(draft_path),
                    "verified_path": str(verified_path),
                },
            )
            return NativeGenerationResult(
                returncode=0,
                draft_path=draft_path,
                verified_path=verified_path,
                message="native generation wrote blueprint.md and verifier accepted blueprint_verified.md",
            )

        append_event(
            problem.log_dir,
            "native_attempt_failed",
            {"attempt": attempt, "verdict": verdict, "remaining_attempts": attempts - attempt},
        )

    append_event(
        problem.log_dir,
        "native_generation_exhausted",
        {"attempts": attempts, "last_verdict": (previous_verification or {}).get("verdict")},
    )
    append_event(
        problem.log_dir,
        "run_failed",
        {
            "returncode": 1,
            "error": f"native generation exhausted {attempts} verification attempt(s) without acceptance",
        },
    )
    return NativeGenerationResult(
        returncode=1,
        draft_path=draft_path,
        verified_path=verified_path,
        message=f"native generation exhausted {attempts} verification attempt(s) without acceptance",
    )


def _run_litellm_tool_loop(
    config: RethlasConfig,
    problem: ProblemPaths,
    refs: ReferencePreparation,
    request: RuntimeRequest,
    registry,
    *,
    stream: bool,
    max_iterations: int = 16,
    use_provider_streaming: bool = True,
    resume: bool = False,
    attempt: int = 1,
    previous_draft: Optional[str] = None,
    previous_verification: Optional[dict] = None,
) -> str:
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError("LiteLLM backend selected, but the 'litellm' package is not installed.") from exc

    user_prompt = (
        _native_generation_repair_prompt(
            config,
            problem,
            refs,
            previous_draft=previous_draft or "",
            previous_verification=previous_verification or {},
            attempt=attempt,
        )
        if previous_verification is not None
        else _native_generation_user_prompt(config, problem, refs, resume=resume)
    )
    messages: list[dict] = [
        {"role": "system", "content": _native_generation_system_prompt(config)},
        {"role": "user", "content": user_prompt},
    ]
    transcript: list[str] = []
    tools = registry.schemas() if request.model.supports_tools else None
    # Track consecutive tool-only iterations to detect a model that keeps
    # searching instead of committing to a final blueprint. After the model
    # has spent this many iterations on tool calls without writing substantial
    # final content, force the next call to skip tools so the model has to
    # emit a blueprint.
    search_iterations = 0
    MAX_SEARCH_ITERATIONS = 6
    CONTENT_THRESHOLD = 200

    try:
        for iteration in range(1, max_iterations + 1):
            convergence = search_iterations >= MAX_SEARCH_ITERATIONS
            if convergence:
                append_event(
                    problem.log_dir,
                    "convergence_pressure",
                    {"iteration": iteration, "search_iterations": search_iterations},
                )
                # Inject a user-visible directive that the model should commit
                # to a blueprint now rather than call more tools.
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have been searching and exploring for a while. "
                            "Stop calling tools NOW. Write the final proof blueprint "
                            "in markdown format as your response. Use everything you "
                            "have gathered. The blueprint must start with "
                            "'# theorem <name>' and contain '## statement' and "
                            "'## proof' sections."
                        ),
                    }
                )
            append_event(problem.log_dir, "model_started", {"iteration": iteration, "model": request.model.name})
            completion_kwargs = litellm_completion_kwargs(request)
            completion_kwargs["messages"] = messages
            if tools is not None:
                completion_kwargs["tools"] = tools

            content = ""
            tool_calls: list = []
            finish_reason = None
            should_stream = use_provider_streaming and request.model.supports_streaming
            if should_stream:
                try:
                    completion_kwargs["stream"] = True
                    chunks = litellm.completion(**completion_kwargs)
                    for chunk in chunks:
                        delta = _extract_stream_delta(chunk)
                        if delta:
                            content += delta
                            append_event(
                                problem.log_dir,
                                "model_delta",
                                {"iteration": iteration, "delta": delta},
                            )
                            if stream:
                                _stream_text(delta, end="")
                        if getattr(chunk, "choices", None):
                            choice = chunk.choices[0]
                            finish_reason = getattr(choice, "finish_reason", None)
                            stream_tool_calls = getattr(choice.delta, "tool_calls", None) if hasattr(choice, "delta") else None
                            if stream_tool_calls:
                                tool_calls = _merge_streaming_tool_calls(tool_calls, stream_tool_calls)
                except Exception as exc:
                    # Some providers don't support streaming + tools together
                    # (or the user's proxy doesn't). Fall back to a single
                    # message-level call so the run still completes.
                    append_event(
                        problem.log_dir,
                        "model_delta_fallback",
                        {"iteration": iteration, "error": str(exc)},
                    )
                    completion_kwargs.pop("stream", None)
                    response = litellm.completion(**completion_kwargs)
                    message = response.choices[0].message
                    content = getattr(message, "content", None) or ""
                    tool_calls = getattr(message, "tool_calls", None) or []
            else:
                response = litellm.completion(**completion_kwargs)
                message = response.choices[0].message
                content = getattr(message, "content", None) or ""
                tool_calls = getattr(message, "tool_calls", None) or []

            if stream and content and should_stream:
                # Close the streamed line so the next tool/iteration starts cleanly.
                _stream_text("\n", end="")
            elif content and stream:
                _stream_text(content)

            transcript.append(f"\n\n## iteration {iteration}\n{content}")
            append_event(
                problem.log_dir,
                "model_finished",
                {"iteration": iteration, "finish_reason": finish_reason, "chars": len(content)},
            )

            if not tool_calls:
                content = _strip_tool_markup(content)
                _write_transcript(request.log_path, transcript)
                return content

            if len(content.strip()) >= CONTENT_THRESHOLD:
                search_iterations = 0
            else:
                search_iterations += 1

            messages.append(_message_to_dict_assistant(content, tool_calls))
            for tool_call in tool_calls:
                call_id, name, args_json = _tool_call_parts(tool_call)
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
                        "tool_call_id": call_id,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )
                transcript.append(f"\n\n### tool {name}\n{json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        _write_transcript(request.log_path, transcript)
        raise

    _write_transcript(request.log_path, transcript)
    raise RuntimeError(f"native generation exceeded {max_iterations} model iterations")


def _extract_stream_delta(chunk) -> str:
    """Best-effort extraction of a single streamed text delta from a chunk.

    LiteLLM normalizes most providers, but a few still ship raw ``deltas`` or
    ``text`` payloads. We try the common shapes and return an empty string
    when the chunk carries no text.
    """
    try:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            return ""
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict)
            )
    except (AttributeError, IndexError):
        return ""
    return ""


def _merge_streaming_tool_calls(accumulated: list, incoming) -> list:
    """Accumulate tool call deltas from a streamed response.

    Some providers send tool calls split across many chunks; each one may
    only carry the function name, the id, or a slice of the arguments. We
    keep a stable list keyed by index so the caller can iterate once at the
    end of the stream.
    """
    by_index: dict[int, dict] = {}
    for entry in accumulated:
        by_index[entry["_index"]] = entry
    for call in incoming:
        index = getattr(call, "index", None)
        if index is None:
            index = len(by_index)
        entry = by_index.setdefault(
            index,
            {
                "_index": index,
                "id": None,
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        call_id = getattr(call, "id", None)
        if call_id:
            entry["id"] = call_id
        function = getattr(call, "function", None)
        if function is not None:
            name = getattr(function, "name", None)
            if name:
                entry["function"]["name"] = name
            arguments = getattr(function, "arguments", None)
            if arguments:
                entry["function"]["arguments"] += arguments
    return [by_index[key] for key in sorted(by_index)]


def _message_to_dict_assistant(content: str, tool_calls: list) -> dict:
    """Serialize an assistant turn (possibly from streaming) for the message log."""
    payload: dict = {"role": "assistant", "content": content}
    if tool_calls:
        serialized = []
        for entry in tool_calls:
            if isinstance(entry, dict) and entry.get("_index") is not None:
                serialized.append(
                    {
                        "id": entry.get("id"),
                        "type": entry.get("type", "function"),
                        "function": {
                            "name": entry["function"]["name"],
                            "arguments": entry["function"]["arguments"],
                        },
                    }
                )
            elif hasattr(entry, "model_dump"):
                serialized.append(entry.model_dump(exclude_none=True))
            elif isinstance(entry, dict):
                serialized.append(entry)
            else:
                function = getattr(entry, "function", None)
                serialized.append(
                    {
                        "id": getattr(entry, "id", None),
                        "type": getattr(entry, "type", "function"),
                        "function": {
                            "name": getattr(function, "name", "") if function is not None else "",
                            "arguments": getattr(function, "arguments", "{}") if function is not None else "{}",
                        },
                    }
                )
        payload["tool_calls"] = serialized
    return payload


def _strip_tool_markup(content: str) -> str:
    """Remove tool-call markup that some models emit in their text.

    DeepSeek's chat model uses an internal ``<|DSML|...|>`` tool syntax that
    it can leak into a final text response when tools are disabled. We strip
    well-known markers so the blueprint is clean markdown; if the entire
    content was tool markup we return a small placeholder so the file is
    never empty.
    """
    if not content:
        return content
    import re

    # DSML blocks and similar tool markup (consume the surrounding tags).
    # DeepSeek chat leaks internal tool markup like
    # <｜｜DSML｜｜tool_calls>...</｜｜DSML｜｜tool_calls> when its tool
    # channel is disabled. Strip any <...> block that contains DSML,
    # tool_call, or <tool_call> markers.
    patterns = [
        r"<[^<>]*DSML[^<>]*>",
        r"<[^<>]*</tool_call>[^<>]*>",
        r"<[^<>]*</tool_call>",
        r"<tool_call>[\s\S]*?</tool_call>",
    ]
    cleaned = content
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return (
            "# theorem auto-converged\n\n"
            "## note\n"
            "The model converged without producing a textual blueprint. "
            "Please re-run with a higher max-iterations budget or a different "
            "model if a real proof blueprint is required.\n"
        )
    return cleaned


def _tool_call_parts(tool_call) -> tuple:
    """Return (call_id, function_name, arguments_json) from a tool call.

    Tool calls may arrive as pydantic objects (non-streaming path) or as
    plain dicts accumulated by ``_merge_streaming_tool_calls`` during the
    streaming path. Both shapes are normalized here so the loop body can
    iterate without caring which path produced them.
    """
    if isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
        if isinstance(function, dict):
            name = function.get("name") or ""
            args_json = function.get("arguments") or "{}"
        else:
            name = getattr(function, "name", "") or ""
            args_json = getattr(function, "arguments", None) or "{}"
        call_id = tool_call.get("id")
    else:
        function = getattr(tool_call, "function", None)
        if function is None:
            name = ""
            args_json = "{}"
        else:
            name = getattr(function, "name", "") or ""
            args_json = getattr(function, "arguments", None) or "{}"
        call_id = getattr(tool_call, "id", None)
    if not call_id:
        call_id = f"call_{abs(hash((name, args_json))) % 10_000_000}"
    return call_id, name, args_json


def _write_transcript(log_path: Path, transcript: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(transcript), encoding="utf-8")


def _stream_text(text: str, *, end: str = "\n") -> None:
    try:
        print(text, end=end, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.buffer.write((text + end).encode(encoding, errors="replace"))
        sys.stdout.buffer.flush()


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
    *,
    resume: bool = False,
) -> str:
    problem_text = problem.problem_file.read_text(encoding="utf-8")
    reference_text = _read_reference_text(problem.reference_dir)
    resume_note = (
        "\n\nThis run is resuming a previous attempt. Before producing new content, "
        "use the memory tools to inspect the existing memory, results, and log "
        "directories for this problem. Build on what is already there; do not "
        "restart from scratch."
        if resume
        else ""
    )
    return (
        f"Problem id: {problem.problem_id}\n"
        f"Problem file: {problem.problem_path}\n"
        f"Reference policy: {refs.prompt_suffix}\n\n"
        "Use the available tools for memory and verification. "
        "When you have a candidate proof blueprint, return only markdown for blueprint.md.\n"
        f"{_latex_output_policy()}\n\n"
        "Problem statement:\n"
        f"{problem_text}\n\n"
        "Reference excerpts:\n"
        f"{reference_text}\n"
        f"{resume_note}"
    )


def _native_generation_repair_prompt(
    config: RethlasConfig,
    problem: ProblemPaths,
    refs: ReferencePreparation,
    *,
    previous_draft: str,
    previous_verification: dict,
    attempt: int,
) -> str:
    problem_text = problem.problem_file.read_text(encoding="utf-8")
    reference_text = _read_reference_text(problem.reference_dir)
    verification_text = json.dumps(previous_verification, ensure_ascii=False, indent=2)
    return (
        f"Problem id: {problem.problem_id}\n"
        f"Problem file: {problem.problem_path}\n"
        f"Repair attempt: {attempt}\n"
        f"Reference policy: {refs.prompt_suffix}\n\n"
        "The previous candidate proof did not pass verification. Use the "
        "verification report as the control signal: fix critical_errors first, "
        "then gaps, then address repair_hints. If the report requires a "
        "strategy change, revise the proof globally rather than making a local "
        "patch. Return a complete replacement markdown proof for blueprint.md, "
        "not a diff and not commentary about the changes.\n"
        f"{_latex_output_policy()}\n\n"
        "Problem statement:\n"
        f"{problem_text}\n\n"
        "Previous candidate proof:\n"
        f"{previous_draft}\n\n"
        "Verification report:\n"
        f"{verification_text}\n\n"
        "Reference excerpts:\n"
        f"{reference_text}\n"
    )


def _latex_output_policy() -> str:
    return (
        "Output formatting policy: write mathematical symbols and expressions "
        "strictly as LaTeX math. Use inline math like \\(x \\in A\\) and display "
        "math like \\[ ... \\]. Do not use raw Unicode math symbols such as ∀, "
        "∃, ∈, ≤, ≥, →, or Greek letters outside LaTeX math delimiters."
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
