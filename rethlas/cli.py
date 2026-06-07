from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Optional

from .config import load_config
from .presets import BUILTIN_PRESETS, base_url_env_name
from .agent_loop import run_native_generation
from .events import append_event
from .problems import normalize_problem
from .references import prepare_references
from .runtime import backend_for, build_plan, build_request, missing_runtime_dependencies
from .status import inspect_problem_status
from .tools import build_generation_tool_registry
from .subagents import SubAgentRunner, SubAgentTask


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
        status = "ready" if (preset.key_optional or key_set) else f"missing {preset.key_env}"
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
    if args.verbose and "custom" in BUILTIN_PRESETS:
        custom = BUILTIN_PRESETS["custom"]
        custom_key = bool(os.getenv(custom.key_env))
        custom_base = os.getenv("CUSTOM_API_BASE")
        custom_compat = os.getenv("CUSTOM_COMPAT")
        print(
            f"  custom: key={custom_key} base={custom_base or '(none)'} "
            f"compat={custom_compat or '(none)'}"
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


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    problem, refs, request = _generation_request(config, args)
    plan = build_plan(config, request)
    missing = missing_runtime_dependencies(plan)

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
        result = run_native_generation(
            config,
            problem,
            refs,
            request,
            stream=not args.no_live_log,
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
    python = str(python_exe) if python_exe.exists() else sys.executable
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
    run.add_argument("--allow-incomplete-backend", action="store_true")
    run.set_defaults(func=cmd_run, role="generation")

    status = subparsers.add_parser("status", help="Inspect logs, memory, and result files for a problem")
    status.add_argument("problem", nargs="?", default="example")
    status.set_defaults(func=cmd_status)

    subagent_check = subparsers.add_parser("subagent-check", help="Run a deterministic sub-agent constraint check")
    subagent_check.set_defaults(func=cmd_subagent_check)

    plan = subparsers.add_parser("plan", help="Print the selected runtime plan")
    plan.add_argument("--role", choices=["generation", "verification"], default="generation")
    plan.add_argument("--problem", default="example")
    plan.add_argument("--model", default=None, help="Model profile from rethlas.toml")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
