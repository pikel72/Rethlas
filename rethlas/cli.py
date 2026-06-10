from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Optional

from .config import find_repo_root, load_config, load_dotenv_from_repo_root
from .presets import BUILTIN_PRESETS, PresetSpec, base_url_env_name
from .agent_loop import run_native_generation
from .events import append_event, latest_events
from .follow import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    format_event_compact,
    format_event_json,
    iter_follow_events,
    summarize_watch_state,
)
from .jobs import (
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_STOPPED,
    JOB_STATUS_STOPPING,
    JOB_STATUS_SUCCEEDED,
    JobRegistry,
    default_jobs_dir,
    format_job_line,
    is_pid_alive,
    list_jobs as registry_list_jobs,
    start_job,
    stop_job,
    utc_now_iso,
)
from .problems import normalize_problem
from .references import prepare_references
from .runtime import backend_for, build_plan, build_request, missing_runtime_dependencies
from .status import inspect_problem_status
from .tools import build_generation_tool_registry
from .subagents import SubAgentRunner, SubAgentTask
from .viewer import build_results_viewer, serve_results_viewer


def build_generation_prompt(problem_path: str, problem_id: str, reference_prompt: str) -> str:
    return (
        f"Use AGENTS.md exactly to solve the math problem in {problem_path}. "
        f"Use problem_id={problem_id}. "
        f"{reference_prompt}"
    )


def verifier_health(url: str, timeout_seconds: int = 2) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def _print_plan(plan) -> None:
    print(f"role: {plan.role}")
    print(f"provider: {plan.provider_name} ({plan.provider_kind})")
    print(f"model profile: {plan.model_profile}")
    print(f"model: {plan.model}")
    print(f"cwd: {plan.cwd}")
    print(f"log: {plan.log_path}")
    print(f"implemented: {str(plan.implemented).lower()}")
    if plan.command:
        print(f"command: {plan.command_text()}")
    else:
        print(f"api base url: {plan.api_base_url}")
        print(f"api key env: {plan.api_key_env}")
    for note in plan.notes:
        print(f"note: {note}")


def _preset_status(name: str, preset: PresetSpec) -> str:
    missing: list[str] = []
    if not preset.key_optional and not os.getenv(preset.key_env):
        missing.append(preset.key_env)
    if name == "custom":
        if not os.getenv("CUSTOM_API_BASE"):
            missing.append("CUSTOM_API_BASE")
        compat = os.getenv("CUSTOM_COMPAT", "").strip().lower()
        if compat not in {"openai", "anthropic"}:
            missing.append("CUSTOM_COMPAT")
    if not os.getenv(preset.model_env_override):
        missing.append(preset.model_env_override)
    if missing:
        return "missing " + ", ".join(missing)
    return "ready"


def _generation_request(config, args):
    problem = normalize_problem(args.problem, config.paths.generation_dir)
    refs = prepare_references(
        problem.reference_dir,
        config.paths.generation_dir,
        extract_pdfs=not getattr(args, "no_prepare_refs", False),
    )
    prompt = build_generation_prompt(
        problem.problem_path,
        problem.problem_id,
        refs.prompt_suffix,
    )
    request = build_request(
        config,
        role="generation",
        cwd=config.paths.generation_dir,
        prompt=prompt,
        log_path=problem.log_file,
        model_name=args.model,
    )
    return problem, refs, request


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    print(f"repo: {config.repo_root}")
    print(f"default model: {config.runtime.default_model}")
    print(f"verification url: {config.verification.base_url}")
    print(
        "agents: "
        f"max_threads={config.agents.max_threads} "
        f"max_depth={config.agents.max_depth} "
        f"job_max_runtime_seconds={config.agents.job_max_runtime_seconds}"
    )
    print("")
    print("models:")
    for model in config.models.values():
        provider = config.providers.get(model.provider)
        provider_kind = provider.kind if provider else "<missing>"
        print(f"  {model.name}: {model.model} via {model.provider} ({provider_kind})")
        if args.verbose:
            print(
                "    "
                f"tools={model.supports_tools} streaming={model.supports_streaming} "
                f"max_tokens={model.max_tokens} temperature={model.temperature} "
                f"context_window={model.context_window}"
            )
    print("")
    print("env presets:")
    for name, preset in sorted(BUILTIN_PRESETS.items()):
        key_set = bool(os.getenv(preset.key_env))
        base_env = base_url_env_name(preset)
        base_override = os.getenv(base_env)
        status = _preset_status(name, preset)
        print(f"  {name} ({preset.display_name}): {status}")
        if args.verbose:
            model_set = bool(os.getenv(preset.model_env_override))
            print(
                f"    base_url={preset.base_url or '(none)'} "
                f"compat={preset.compat} "
                f"key_env={preset.key_env} "
                f"key_set={key_set} "
                f"base_override={base_override or '(none)'} "
                f"model_env={preset.model_env_override} "
                f"model_set={model_set}"
            )
    print("")
    print("providers:")
    for provider in config.providers.values():
        if provider.kind == "codex-cli":
            print(f"  {provider.name}: {provider.kind}, command={provider.command or 'codex'}")
        elif provider.kind == "litellm":
            print(f"  {provider.name}: {provider.kind}, package=litellm")
        elif provider.kind == "mock":
            print(f"  {provider.name}: {provider.kind}")
        else:
            print(
                f"  {provider.name}: {provider.kind}, "
                f"base_url={provider.base_url}, api_key_env={provider.api_key_env}"
            )
    print("")
    print("paths:")
    print(f"  generation: {config.paths.generation_dir}")
    print(f"  verification: {config.paths.verification_dir}")
    print("")
    print(f"verifier reachable: {str(verifier_health(config.verification.base_url)).lower()}")
    if args.tools:
        registry = build_generation_tool_registry(config)
        print("")
        print("generation tools:")
        for name in registry.names:
            print(f"  {name}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_config()
    if args.role == "generation":
        args.no_prepare_refs = True
        _problem, _refs, request = _generation_request(config, args)
    else:
        prompt = "Run_id: dry_run. Statement: <statement>. Proof:\n<proof>\n\nUse AGENTS.md to verify the above proof for the statement."
        request = build_request(
            config,
            role="verification",
            cwd=config.paths.verification_dir,
            prompt=prompt,
            log_path=config.paths.verification_dir / "results" / "dry_run" / "log.md",
            model_name=args.model,
        )

    plan = build_plan(config, request)
    if args.json:
        print(
            json.dumps(
                {
                    "role": plan.role,
                    "provider_name": plan.provider_name,
                    "provider_kind": plan.provider_kind,
                    "model_profile": plan.model_profile,
                    "model": plan.model,
                    "cwd": str(plan.cwd),
                    "log_path": str(plan.log_path),
                    "command": plan.command,
                    "api_base_url": plan.api_base_url,
                    "api_key_env": plan.api_key_env,
                    "implemented": plan.implemented,
                    "notes": plan.notes,
                    "missing_dependencies": missing_runtime_dependencies(plan),
                },
                indent=2,
            )
        )
    else:
        _print_plan(plan)
        missing = missing_runtime_dependencies(plan)
        if missing:
            print(f"missing dependencies: {', '.join(missing)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    problem = normalize_problem(args.problem, config.paths.generation_dir)
    status = inspect_problem_status(problem)
    print(f"problem id: {problem.problem_id}")
    print(f"problem file: {problem.problem_file}")
    print(f"log: {problem.log_file} ({'exists' if status.log_exists else 'missing'})")
    print(f"memory: {problem.memory_dir} ({len(status.memory_files)} files)")
    print(f"results: {problem.result_dir} ({len(status.result_files)} files)")
    print(f"draft blueprint: {str(status.draft_exists).lower()}")
    print(f"verified blueprint: {str(status.verified_exists).lower()}")
    if status.latest_log_line:
        print(f"latest log line: {status.latest_log_line}")
    if status.latest_events:
        print("latest events:")
        for event in status.latest_events:
            print(f"  {event.get('timestamp_utc')} {event.get('event_type')}")
    print(f"verifier reachable: {str(verifier_health(config.verification.base_url)).lower()}")
    return 0


def _resolve_watch_poll(args: argparse.Namespace) -> float:
    raw = getattr(args, "poll_interval", None)
    if raw is None or raw <= 0:
        return DEFAULT_POLL_INTERVAL_SECONDS
    return float(raw)


def cmd_tail(args: argparse.Namespace) -> int:
    """Follow events.jsonl for a problem and print compact lines.

    By default this prints everything the file has so far, then continues to
    follow new events until the process is interrupted (Ctrl+C) or the
    ``--max-events`` / ``--timeout`` limit is hit. With ``--no-follow``,
    existing events are printed and the command returns. With ``--json``,
    every event is printed as a JSON line (suitable for piping into other
    tools).
    """
    config = load_config()
    problem = normalize_problem(args.problem, config.paths.generation_dir)
    poll = _resolve_watch_poll(args)
    json_mode = bool(getattr(args, "json", False))
    show_deltas = bool(getattr(args, "deltas", False))
    max_events = getattr(args, "max_events", None)
    timeout = getattr(args, "timeout", None)
    follow = not bool(getattr(args, "no_follow", False))

    print(f"tailing {problem.log_dir / 'events.jsonl'}")
    try:
        count = 0
        stop_after = (max_events is not None)
        for event in iter_follow_events(
            problem.log_dir,
            from_beginning=True,
            poll_interval=poll,
            timeout_seconds=timeout if follow else 0.0,
        ):
            if json_mode:
                print(format_event_json(event))
            else:
                line = format_event_compact(event, show_deltas=show_deltas)
                if line is not None:
                    print(line)
            count += 1
            sys.stdout.flush()
            if stop_after and count >= max_events:
                return 0
    except KeyboardInterrupt:
        return 130
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Follow events.jsonl until the run is verifiably done.

    Exits 0 when ``blueprint_verified.md`` exists or the latest
    ``verification_finished`` event reports ``verdict=correct``. Exits
    non-zero on ``run_failed`` / ``verification_failed``. Continues to
    follow after the original run process has exited — a run that crashed
    or got interrupted is still useful to observe until the user gives up.
    """
    config = load_config()
    problem = normalize_problem(args.problem, config.paths.generation_dir)
    poll = _resolve_watch_poll(args)
    timeout = getattr(args, "timeout", None)
    show_deltas = bool(getattr(args, "deltas", False))

    print(f"watching {problem.log_dir / 'events.jsonl'}")
    try:
        for event in iter_follow_events(
            problem.log_dir,
            from_beginning=True,
            poll_interval=poll,
            timeout_seconds=timeout,
        ):
            line = format_event_compact(event, show_deltas=show_deltas)
            if line is not None:
                print(line)
            sys.stdout.flush()
            state = summarize_watch_state(problem.log_dir, problem.result_dir)
            if state.is_succeeded:
                print("watch: run succeeded (verified blueprint exists)")
                return 0
            if state.is_failed:
                print(f"watch: run failed: {state.last_error or 'see events.jsonl'}")
                return 1
    except KeyboardInterrupt:
        return 130
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    problem, refs, request = _generation_request(config, args)
    plan = build_plan(config, request)
    missing = missing_runtime_dependencies(plan)

    if getattr(args, "background", False):
        return _run_in_background(config, problem, plan, args)

    print("Rethlas run")
    print("-----------")
    print(f"problem id: {problem.problem_id}")
    print(f"problem file: {problem.problem_file}")
    print(f"reference dir: {problem.reference_dir} ({'exists' if refs.exists else 'missing'})")
    print(f"log: {problem.log_file}")
    print(f"memory: {problem.memory_dir}")
    print(f"results: {problem.result_dir}")
    print(f"verification url: {config.verification.base_url}")
    print(f"verifier reachable: {str(verifier_health(config.verification.base_url)).lower()}")
    _print_plan(plan)
    append_event(
        problem.log_dir,
        "run_planned",
        {"provider": plan.provider_name, "model": plan.model_profile, "dry_run": args.dry_run},
    )
    for warning in refs.warnings:
        print(f"warning: {warning}")
    if missing:
        print(f"missing dependencies: {', '.join(missing)}")
        if not args.dry_run:
            return 2

    if args.dry_run:
        append_event(problem.log_dir, "run_dry_run_finished", {"model": plan.model_profile})
        return 0

    if plan.provider_kind != "codex-cli" and args.role == "generation":
        if getattr(args, "json_events", False):
            _enable_json_events_stdout(problem.log_dir)
        # Transparent resume: when prior memory, logs, or a draft
        # blueprint exist, the next ``run`` automatically continues
        # from where the previous run left off.
        prior_blueprint = problem.result_dir / "blueprint.md"
        _resume = (
            problem.memory_dir.is_dir()
            or any(problem.log_dir.glob("*.jsonl"))
            or (prior_blueprint.is_file() and prior_blueprint.stat().st_size > 0)
        )
        if _resume:
            print("note: prior state found — continuing previous work")
        result = run_native_generation(
            config,
            problem,
            refs,
            request,
            stream=not args.no_live_log,
            resume=_resume,
        )
        print(result.message)
        print(f"draft: {result.draft_path}")
        if result.verified_path.exists():
            print(f"verified: {result.verified_path}")
        append_event(problem.log_dir, "run_finished", {"returncode": result.returncode, "message": result.message})
        return result.returncode

    backend = backend_for(request.provider)
    try:
        result = backend.run(request, stream=not args.no_live_log)
        if result.error:
            print(f"runtime error: {result.error}")
        append_event(problem.log_dir, "run_finished", {"returncode": result.returncode, "error": result.error})
        return result.returncode
    except Exception as exc:
        print(f"runtime failed: {exc}")
        append_event(problem.log_dir, "run_failed", {"error": str(exc)})
        return 1


def _build_background_command(args: argparse.Namespace) -> list[str]:
    """Build the argv used to re-invoke ``run`` in a child process.

    We strip ``--background`` so the child does not re-spawn, and we forward
    the relevant flags the user passed. New flags added to ``run`` should
    be considered here when they affect the run itself.
    """
    command = [sys.executable, "-m", "rethlas.cli", "run", args.problem]
    if getattr(args, "model", None):
        command.extend(["--model", args.model])
    if getattr(args, "no_live_log", False):
        command.append("--no-live-log")
    if getattr(args, "json_events", False):
        command.append("--json-events")
    return command


def _record_job_terminal_status(command: list[str], returncode: int) -> None:
    """If we are running as a background job, mark the registry terminal.

    The child sets ``RETHLAS_JOB_ID`` in its environment (the parent does
    this) so the child knows it is the inner process. On exit, the child
    updates the registry with the final status. This keeps the registry
    honest even if the parent never runs again.
    """
    job_id = os.environ.get("RETHLAS_JOB_ID")
    if not job_id:
        return
    try:
        repo_root = find_repo_root()
    except FileNotFoundError:
        return
    try:
        config = load_config(repo_root=repo_root)
    except Exception:
        return
    registry = JobRegistry(default_jobs_dir(config.paths.generation_dir))
    job = registry.get(job_id)
    if job is None or job.is_terminal:
        return
    events = latest_events(
        config.paths.generation_dir / "logs" / job.problem_id, limit=1
    )
    last = events[-1] if events else None
    if last and last.get("event_type") in {"run_failed", "verification_failed"}:
        status = JOB_STATUS_FAILED
    elif returncode == 0:
        status = JOB_STATUS_SUCCEEDED
    else:
        status = JOB_STATUS_FAILED
    registry.update(
        job_id,
        status=status,
        ended_at_utc=utc_now_iso(),
    )


def _enable_json_events_stdout(log_dir: Path) -> None:
    """Spawn a background tailer that mirrors events.jsonl to stdout.

    The tailer runs in a daemon thread; the main run process keeps writing
    events normally. When the run exits, the thread is left to be cleaned
    up at interpreter shutdown. We choose this approach over patching
    ``append_event`` so that the same code path (``append_event``) stays
    in charge of disk writes, which is what the rest of the system reads.
    """
    import threading

    stop = threading.Event()
    target = log_dir / "events.jsonl"

    def tailer() -> None:
        offset = 0
        while not stop.is_set():
            if target.is_file():
                try:
                    size = target.stat().st_size
                    if size > offset:
                        with target.open("rb") as handle:
                            handle.seek(offset)
                            chunk = handle.read(size - offset)
                        offset = size
                        for line in chunk.splitlines():
                            stripped = line.decode("utf-8", errors="replace").strip()
                            if stripped:
                                try:
                                    sys.stdout.write(stripped + "\n")
                                except UnicodeEncodeError:
                                    pass
                        sys.stdout.flush()
                except FileNotFoundError:
                    pass
            stop.wait(0.1)

    thread = threading.Thread(target=tailer, daemon=True, name="rethlas-events-tailer")
    thread.start()
    # Stash the stop event on the function so tests / a future SIGINT
    # handler can stop the tailer cleanly.
    _enable_json_events_stdout._stop = stop  # type: ignore[attr-defined]
    """If we are running as a background job, mark the registry terminal.

    The child sets ``RETHLAS_JOB_ID`` in its environment (the parent does
    this) so the child knows it is the inner process. On exit, the child
    updates the registry with the final status. This keeps the registry
    honest even if the parent never runs again.
    """
    job_id = os.environ.get("RETHLAS_JOB_ID")
    if not job_id:
        return
    try:
        repo_root = find_repo_root()
    except FileNotFoundError:
        return
    try:
        config = load_config(repo_root=repo_root)
    except Exception:
        return
    registry = JobRegistry(default_jobs_dir(config.paths.generation_dir))
    job = registry.get(job_id)
    if job is None or job.is_terminal:
        return
    events = latest_events(
        config.paths.generation_dir / "logs" / job.problem_id, limit=1
    )
    last = events[-1] if events else None
    if last and last.get("event_type") in {"run_failed", "verification_failed"}:
        status = JOB_STATUS_FAILED
    elif returncode == 0:
        status = JOB_STATUS_SUCCEEDED
    else:
        status = JOB_STATUS_FAILED
    registry.update(
        job_id,
        status=status,
        ended_at_utc=utc_now_iso(),
    )


def _run_in_background(config, problem, plan, args: argparse.Namespace) -> int:
    """Start a background job and return immediately."""
    registry = JobRegistry(default_jobs_dir(config.paths.generation_dir))
    if missing_runtime_dependencies(plan):
        print(f"missing dependencies: {', '.join(missing_runtime_dependencies(plan))}")
        return 2
    command = _build_background_command(args)
    child_env = {"RETHLAS_JOB_ID": problem.problem_id}
    job = start_job(
        registry,
        job_id=problem.problem_id,
        problem_id=problem.problem_id,
        role="generation",
        model=plan.model_profile,
        command=command,
        log_dir=problem.log_dir,
        result_dir=problem.result_dir,
        cwd=config.repo_root,
        env=child_env,
    )
    print(f"started background job {job.job_id} (pid={job.pid})")
    print(f"  log: {registry.stdout_path(job.job_id)}")
    print(f"  follow with: python -m rethlas.cli tail {job.job_id}")
    print(f"  stop with:   python -m rethlas.cli stop {job.job_id}")
    append_event(
        problem.log_dir,
        "run_started",
        {"pid": job.pid, "background": True, "model": plan.model_profile},
    )
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    config = load_config()
    registry = JobRegistry(default_jobs_dir(config.paths.generation_dir))
    # Reconcile: any "running"/"stopping" job whose pid is no longer alive
    # gets marked terminal here. This keeps the registry honest even if
    # the child process died before it could update itself.
    for job in registry.list():
        if job.is_terminal:
            continue
        if job.pid is not None and not is_pid_alive(job.pid):
            log_dir = config.paths.generation_dir / "logs" / job.problem_id
            events = latest_events(log_dir, limit=1)
            last = events[-1] if events else None
            if last and last.get("event_type") in {"run_failed", "verification_failed"}:
                status = JOB_STATUS_FAILED
            else:
                status = JOB_STATUS_SUCCEEDED
            registry.update(job.job_id, status=status, ended_at_utc=utc_now_iso())
    jobs = registry.list()
    if not jobs:
        print("(no jobs recorded)")
        return 0
    for job in jobs:
        print(format_job_line(job))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    config = load_config()
    registry = JobRegistry(default_jobs_dir(config.paths.generation_dir))
    problem_id = args.problem_or_job
    # First, try to use the argument as a job_id directly.
    job = registry.get(problem_id)
    if job is None:
        # Fall back to the convention job_id == problem_id, but resolve the
        # problem so we get the canonical id.
        try:
            problem = normalize_problem(problem_id, config.paths.generation_dir)
        except (FileNotFoundError, ValueError):
            print(f"unknown job or problem: {problem_id!r}")
            return 2
        job = registry.get(problem.problem_id)
        if job is None:
            print(f"unknown job or problem: {problem_id!r}")
            return 2
    if job.is_terminal:
        print(f"job {job.job_id!r} is already {job.status}")
        return 0
    method = stop_job(registry, job.job_id)
    append_event(
        config.paths.generation_dir / "logs" / job.problem_id,
        "run_interrupted",
        {"method": method, "requested_by": "cli", "job_id": job.job_id},
    )
    print(f"stopped job {job.job_id} via {method}")
    return 0


def _summarize_previous_state(problem, status: "inspect_problem_status") -> dict:
    """Build a JSON-friendly summary of the previous run state.

    Used by ``resume`` to inform the model (and the user) what artifacts
    are already on disk so it does not start from zero.
    """
    return {
        "problem_id": problem.problem_id,
        "draft_exists": status.draft_exists,
        "verified_exists": status.verified_exists,
        "memory_files": [str(path.relative_to(problem.memory_dir)) for path in status.memory_files if path.is_relative_to(problem.memory_dir)],
        "result_files": [str(path.relative_to(problem.result_dir)) for path in status.result_files if path.is_relative_to(problem.result_dir)],
        "latest_event_type": (status.latest_events[-1].get("event_type") if status.latest_events else None),
    }


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume a problem by inspecting existing memory/results/logs and
    kicking off a new run that continues rather than restarts.

    The CLI does the same ``_generation_request`` setup as ``run`` but
    records a ``run_resumed`` event first, then re-uses the standard run
    flow. The native generation agent loop already reads the memory and
    result directories, so the model naturally picks up the previous
    state.
    """
    config = load_config()
    problem, refs, request = _generation_request(config, args)
    plan = build_plan(config, request)
    missing = missing_runtime_dependencies(plan)

    print("Rethlas resume")
    print("--------------")
    print(f"problem id: {problem.problem_id}")
    print(f"log: {problem.log_file}")
    print(f"memory: {problem.memory_dir}")
    print(f"results: {problem.result_dir}")

    status = inspect_problem_status(problem)
    summary = _summarize_previous_state(problem, status)
    print(f"draft blueprint: {str(status.draft_exists).lower()}")
    print(f"verified blueprint: {str(status.verified_exists).lower()}")
    if summary["memory_files"]:
        print(f"memory files: {', '.join(summary['memory_files'])}")
    if summary["result_files"]:
        print(f"result files: {', '.join(summary['result_files'])}")
    if summary["latest_event_type"]:
        print(f"latest event: {summary['latest_event_type']}")

    previous_status = "succeeded" if status.verified_exists else (
        "drafted" if status.draft_exists else (
            "started" if status.memory_exists else "fresh"
        )
    )
    append_event(
        problem.log_dir,
        "run_resumed",
        {
            "previous_status": previous_status,
            "draft_exists": status.draft_exists,
            "verified_exists": status.verified_exists,
            "memory_files": len(status.memory_files),
            "result_files": len(status.result_files),
        },
    )

    if missing:
        print(f"missing dependencies: {', '.join(missing)}")
        if not args.dry_run:
            return 2
    if args.dry_run:
        append_event(problem.log_dir, "run_dry_run_finished", {"model": plan.model_profile, "resumed": True})
        return 0

    if getattr(args, "background", False):
        return _run_in_background(config, problem, plan, args)

    if plan.provider_kind != "codex-cli" and args.role == "generation":
        if getattr(args, "json_events", False):
            _enable_json_events_stdout(problem.log_dir)
        result = run_native_generation(
            config,
            problem,
            refs,
            request,
            stream=not args.no_live_log,
            resume=True,
        )
        print(result.message)
        print(f"draft: {result.draft_path}")
        if result.verified_path.exists():
            print(f"verified: {result.verified_path}")
        append_event(
            problem.log_dir,
            "run_finished",
            {"returncode": result.returncode, "message": result.message, "resumed": True},
        )
        return result.returncode

    backend = backend_for(request.provider)
    try:
        result = backend.run(request, stream=not args.no_live_log)
        if result.error:
            print(f"runtime error: {result.error}")
        append_event(
            problem.log_dir,
            "run_finished",
            {"returncode": result.returncode, "error": result.error, "resumed": True},
        )
        return result.returncode
    except Exception as exc:
        print(f"runtime failed: {exc}")
        append_event(
            problem.log_dir,
            "run_failed",
            {"error": str(exc), "resumed": True},
        )
        return 1


def cmd_setup(args: argparse.Namespace) -> int:
    config = load_config()
    targets = [
        (config.repo_root / ".venv", config.repo_root / "requirements.txt"),
        (config.paths.generation_dir / ".venv", config.paths.generation_dir / "mcp" / "requirements.txt"),
        (config.paths.verification_dir / ".venv", config.paths.verification_dir / "requirements.txt"),
    ]
    for venv_dir, requirements in targets:
        python_exe = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        print(f"venv: {venv_dir}")
        if args.dry_run:
            print(f"  would create if missing: {venv_dir}")
            if requirements.is_file():
                print(f"  would install: {requirements}")
            continue
        if not python_exe.exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        if requirements.is_file():
            subprocess.run([str(python_exe), "-m", "pip", "install", "-r", str(requirements)], check=True)
    return 0


def cmd_verify_server(args: argparse.Namespace) -> int:
    config = load_config()
    verification_dir = config.paths.verification_dir
    python_exe = verification_dir / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if python_exe.exists() and _python_can_import(python_exe, "uvicorn") and _python_can_import(python_exe, "litellm"):
        python = str(python_exe)
    else:
        python = sys.executable
    cmd = [
        python,
        "-m",
        "uvicorn",
        "api.server:app",
        "--host",
        args.host or config.verification.host,
        "--port",
        str(args.port or config.verification.port),
    ]
    print(" ".join(cmd))
    if args.dry_run:
        return 0
    completed = subprocess.run(cmd, cwd=verification_dir, check=False)
    return completed.returncode


def _python_can_import(python: Path, module: str) -> bool:
    completed = subprocess.run(
        [str(python), "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def cmd_subagent_check(args: argparse.Namespace) -> int:
    config = load_config()
    runner = SubAgentRunner(config)
    results = runner.run_mock_batch(
        [
            SubAgentTask(task_id="task-1", prompt="prove subgoal 1", depth=1),
            SubAgentTask(task_id="too-deep", prompt="prove too deep", depth=config.agents.max_depth + 1),
        ]
    )
    for result in results:
        print(f"{result.task_id}: ok={result.ok} depth={result.depth} summary={result.summary}")
    return 0


def cmd_results_site(args: argparse.Namespace) -> int:
    config = load_config()
    if args.sync_only:
        build = build_results_viewer(config)
        print(f"Synced {build.page_count} result page(s) into {build.output_dir}")
        print(f"Open {build.output_dir / 'index.html'}")
        return 0
    serve_results_viewer(config, port=args.port, open_browser=args.open)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rethlas")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Print runtime configuration")
    doctor.add_argument("--tools", action="store_true", help="List generation tool registry")
    doctor.add_argument("--verbose", action="store_true", help="Print model capability details")
    doctor.set_defaults(func=cmd_doctor)

    setup = subparsers.add_parser("setup", help="Create venvs and install dependencies")
    setup.add_argument("--dry-run", action="store_true")
    setup.set_defaults(func=cmd_setup)

    verify_server = subparsers.add_parser("verify-server", help="Start the verification HTTP service")
    verify_server.add_argument("--host", default=None)
    verify_server.add_argument("--port", type=int, default=None)
    verify_server.add_argument("--dry-run", action="store_true")
    verify_server.set_defaults(func=cmd_verify_server)

    run = subparsers.add_parser("run", help="Run the generation agent for a problem")
    run.add_argument("problem", nargs="?", default="example")
    run.add_argument("--model", default=None, help="Model profile from rethlas.toml")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-live-log", action="store_true")
    run.add_argument("--background", action="store_true", help="Start the run in the background and return immediately")
    run.add_argument("--json-events", action="store_true", help="Emit each event as JSON to stdout (foreground only)")
    run.set_defaults(func=cmd_run, role="generation")

    status = subparsers.add_parser("status", help="Inspect logs, memory, and result files for a problem")
    status.add_argument("problem", nargs="?", default="example")
    status.set_defaults(func=cmd_status)

    tail = subparsers.add_parser("tail", help="Follow a problem's events.jsonl as compact lines")
    tail.add_argument("problem", nargs="?", default="example")
    tail.add_argument("--json", action="store_true", help="Emit raw JSON lines instead of compact text")
    tail.add_argument("--deltas", action="store_true", help="Include streamed model_delta events")
    tail.add_argument("--no-follow", action="store_true", help="Print existing events and exit")
    tail.add_argument("--max-events", type=int, default=None, help="Stop after N events")
    tail.add_argument("--timeout", type=float, default=None, help="Stop after N seconds")
    tail.add_argument("--poll-interval", type=float, default=None, help="Polling interval in seconds")
    tail.set_defaults(func=cmd_tail)

    watch = subparsers.add_parser("watch", help="Wait until a problem's run is verifiably done")
    watch.add_argument("problem", nargs="?", default="example")
    watch.add_argument("--deltas", action="store_true", help="Include streamed model_delta events")
    watch.add_argument("--timeout", type=float, default=None, help="Stop waiting after N seconds")
    watch.add_argument("--poll-interval", type=float, default=None, help="Polling interval in seconds")
    watch.set_defaults(func=cmd_watch)

    jobs = subparsers.add_parser("jobs", help="List known background jobs")
    jobs.set_defaults(func=cmd_jobs)

    stop = subparsers.add_parser("stop", help="Stop a running background job (SIGINT -> terminate -> kill)")
    stop.add_argument("problem_or_job", help="Problem id or job id of the background run to stop")
    stop.set_defaults(func=cmd_stop)

    resume = subparsers.add_parser(
        "resume",
        help="Resume a problem from its existing memory/results/logs (records run_resumed)",
    )
    resume.add_argument("problem", nargs="?", default="example")
    resume.add_argument("--model", default=None, help="Model profile from rethlas.toml")
    resume.add_argument("--dry-run", action="store_true")
    resume.add_argument("--no-live-log", action="store_true")
    resume.add_argument("--background", action="store_true", help="Start the resumed run in the background")
    resume.add_argument("--json-events", action="store_true", help="Emit each event as JSON to stdout")
    resume.set_defaults(func=cmd_resume, role="generation")

    subagent_check = subparsers.add_parser("subagent-check", help="Run a deterministic sub-agent constraint check")
    subagent_check.set_defaults(func=cmd_subagent_check)

    results_site = subparsers.add_parser("results-site", help="Serve generated proof results in a browser")
    results_site.add_argument("--port", type=int, default=3264)
    results_site.add_argument("--open", action="store_true", help="Open the results page in the default browser")
    results_site.add_argument("--sync-only", action="store_true", help="Build static HTML without starting a server")
    results_site.set_defaults(func=cmd_results_site)

    plan = subparsers.add_parser("plan", help="Print the selected runtime plan")
    plan.add_argument("--role", choices=["generation", "verification"], default="generation")
    plan.add_argument("--problem", default="example")
    plan.add_argument("--model", default=None, help="Model profile from rethlas.toml")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    # Auto-load .env from the repo root so users don't have to `set -a; source .env`
    # manually before every command. Existing shell env wins (we only set keys that
    # are not already in os.environ). Library users calling `load_config` directly
    # can call `load_dotenv_from_repo_root` themselves.
    try:
        repo_root = find_repo_root()
    except FileNotFoundError:
        repo_root = None
    if repo_root is not None:
        load_dotenv_from_repo_root(repo_root)
    parser = build_parser()
    args = parser.parse_args(argv)
    returncode = args.func(args)
    _record_job_terminal_status(getattr(args, "command", "") or [], returncode)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
