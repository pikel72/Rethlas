from __future__ import annotations

import os
import json
import re
import shlex
import shutil
import subprocess
from importlib.util import find_spec
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ModelConfig, ProviderConfig, RethlasConfig


@dataclass(frozen=True)
class RuntimeRequest:
    role: str
    cwd: Path
    prompt: str
    log_path: Path
    model: ModelConfig
    provider: ProviderConfig
    timeout_seconds: Optional[int]


@dataclass(frozen=True)
class RuntimeResult:
    returncode: int
    started_at: str
    ended_at: str
    log_path: Path
    output_text: str = ""
    usage: Optional[Dict[str, Any]] = None
    provider_metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass(frozen=True)
class RuntimePlan:
    role: str
    provider_name: str
    provider_kind: str
    model_profile: str
    model: str
    cwd: Path
    log_path: Path
    command: Optional[List[str]]
    api_base_url: Optional[str]
    api_key_env: Optional[str]
    implemented: bool
    notes: List[str] = field(default_factory=list)

    def command_text(self) -> str:
        if self.command is None:
            return "<api runtime>"
        return shlex.join(self.command)


class RuntimeBackend:
    implemented = False

    def build_plan(self, request: RuntimeRequest) -> RuntimePlan:
        raise NotImplementedError

    def run(self, request: RuntimeRequest, *, stream: bool = True) -> RuntimeResult:
        raise NotImplementedError


class CodexCliBackend(RuntimeBackend):
    implemented = True

    def _command(self, request: RuntimeRequest) -> List[str]:
        command = request.provider.command or "codex"
        args = [
            command,
            "exec",
            "-C",
            str(request.cwd),
            "-m",
            request.model.model,
        ]
        if request.model.reasoning_effort:
            args.extend(["--config", f"model_reasoning_effort=\"{request.model.reasoning_effort}\""])
        args.extend(["--dangerously-bypass-approvals-and-sandbox", request.prompt])
        return args

    def build_plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(
            role=request.role,
            provider_name=request.provider.name,
            provider_kind=request.provider.kind,
            model_profile=request.model.name,
            model=request.model.model,
            cwd=request.cwd,
            log_path=request.log_path,
            command=self._command(request),
            api_base_url=None,
            api_key_env=None,
            implemented=True,
        )

    def run(self, request: RuntimeRequest, *, stream: bool = True) -> RuntimeResult:
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._command(request)
        started_at = _utc_now()
        with request.log_path.open("w", encoding="utf-8") as log_handle:
            log_handle.write(f"started_at_utc: {started_at}\n")
            log_handle.write(f"provider: {request.provider.name} ({request.provider.kind})\n")
            log_handle.write(f"model_profile: {request.model.name}\n")
            log_handle.write(f"model: {request.model.model}\n")
            log_handle.write(f"command: {shlex.join(command)}\n\n")
            log_handle.flush()
            if stream:
                output_parts: List[str] = []
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=request.cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    assert process.stdout is not None
                    for line in process.stdout:
                        print(line, end="")
                        log_handle.write(line)
                        output_parts.append(line)
                    returncode = process.wait(timeout=request.timeout_seconds)
                    return RuntimeResult(
                        returncode=returncode,
                        started_at=started_at,
                        ended_at=_utc_now(),
                        log_path=request.log_path,
                        output_text="".join(output_parts),
                        provider_metadata={"command": command},
                    )
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    return RuntimeResult(
                        returncode=124,
                        started_at=started_at,
                        ended_at=_utc_now(),
                        log_path=request.log_path,
                        output_text="".join(output_parts),
                        provider_metadata={"command": command},
                        error=f"Runtime timed out after {exc.timeout} seconds",
                    )

            completed = subprocess.run(
                command,
                cwd=request.cwd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
            return RuntimeResult(
                returncode=completed.returncode,
                started_at=started_at,
                ended_at=_utc_now(),
                log_path=request.log_path,
                provider_metadata={"command": command},
            )


class ApiCompatibleBackend(RuntimeBackend):
    def build_plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(
            role=request.role,
            provider_name=request.provider.name,
            provider_kind=request.provider.kind,
            model_profile=request.model.name,
            model=request.model.model,
            cwd=request.cwd,
            log_path=request.log_path,
            command=None,
            api_base_url=request.provider.base_url,
            api_key_env=request.provider.api_key_env,
            implemented=False,
            notes=["Native provider API runtime is planned but not implemented yet."],
        )

    def run(self, request: RuntimeRequest, *, stream: bool = True) -> RuntimeResult:
        raise NotImplementedError(
            f"Provider kind '{request.provider.kind}' is planned but not implemented yet"
        )


class LiteLLMBackend(RuntimeBackend):
    implemented = True

    def _api_key_env(self, request: RuntimeRequest) -> Optional[str]:
        return request.model.api_key_env or request.provider.api_key_env

    def build_plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(
            role=request.role,
            provider_name=request.provider.name,
            provider_kind=request.provider.kind,
            model_profile=request.model.name,
            model=request.model.model,
            cwd=request.cwd,
            log_path=request.log_path,
            command=None,
            api_base_url=request.provider.base_url,
            api_key_env=self._api_key_env(request),
            implemented=True,
            notes=[
                "LiteLLM backend supports plain model calls.",
                "Full Rethlas tool/MCP loop integration is not implemented yet.",
            ],
        )

    def run(self, request: RuntimeRequest, *, stream: bool = True) -> RuntimeResult:
        try:
            import litellm
        except ImportError as exc:
            raise RuntimeError(
                "LiteLLM backend selected, but the 'litellm' package is not installed."
            ) from exc

        started_at = _utc_now()
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        response = litellm.completion(
            model=request.model.model,
            messages=[{"role": "user", "content": request.prompt}],
            timeout=request.timeout_seconds,
        )
        content = response.choices[0].message.content or ""
        request.log_path.write_text(content, encoding="utf-8")
        if stream:
            print(content)
        usage = getattr(response, "usage", None)
        return RuntimeResult(
            returncode=0,
            started_at=started_at,
            ended_at=_utc_now(),
            log_path=request.log_path,
            output_text=content,
            usage=usage if isinstance(usage, dict) else None,
            provider_metadata={"provider": request.provider.name, "model": request.model.model},
        )


class MockBackend(RuntimeBackend):
    implemented = True

    def build_plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(
            role=request.role,
            provider_name=request.provider.name,
            provider_kind=request.provider.kind,
            model_profile=request.model.name,
            model=request.model.model,
            cwd=request.cwd,
            log_path=request.log_path,
            command=None,
            api_base_url=None,
            api_key_env=None,
            implemented=True,
            notes=["Mock backend writes deterministic local outputs for tests."],
        )

    def run(self, request: RuntimeRequest, *, stream: bool = True) -> RuntimeResult:
        started_at = _utc_now()
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        mode = str(request.model.extra.get("mode", "generation"))
        output_text = f"mock runtime mode={mode}\n"
        request.log_path.write_text(output_text, encoding="utf-8")

        if request.role == "verification":
            run_id = _extract_run_id(request.prompt)
            payload: Any
            if mode == "verification-wrong":
                payload = {
                    "verification_report": {
                        "summary": "Mock verifier found a gap.",
                        "critical_errors": [],
                        "gaps": [{"location": "proof", "issue": "Mock gap for testing."}],
                    },
                    "verdict": "wrong",
                    "repair_hints": "Address the mock gap.",
                }
            elif mode == "verification-malformed":
                payload = {"verdict": "correct"}
            else:
                payload = {
                    "verification_report": {
                        "summary": "Mock verifier accepted the proof.",
                        "critical_errors": [],
                        "gaps": [],
                    },
                    "verdict": "correct",
                    "repair_hints": "",
                }
            output_path = request.cwd / "results" / run_id / "verification.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        return RuntimeResult(
            returncode=0,
            started_at=started_at,
            ended_at=_utc_now(),
            log_path=request.log_path,
            output_text=output_text,
            provider_metadata={"mode": mode},
        )


def backend_for(provider: ProviderConfig) -> RuntimeBackend:
    if provider.kind == "codex-cli":
        return CodexCliBackend()
    if provider.kind == "litellm":
        return LiteLLMBackend()
    if provider.kind == "mock":
        return MockBackend()
    if provider.kind in {"openai-compatible", "anthropic-compatible"}:
        return ApiCompatibleBackend()
    raise ValueError(f"Unsupported provider kind: {provider.kind}")


def build_request(
    config: RethlasConfig,
    *,
    role: str,
    cwd: Path,
    prompt: str,
    log_path: Path,
    model_name: Optional[str] = None,
) -> RuntimeRequest:
    model = config.resolve_model(model_name)
    provider = config.resolve_provider(model)
    return RuntimeRequest(
        role=role,
        cwd=cwd,
        prompt=prompt,
        log_path=log_path,
        model=model,
        provider=provider,
        timeout_seconds=config.runtime.timeout_seconds,
    )


def build_plan(config: RethlasConfig, request: RuntimeRequest) -> RuntimePlan:
    return backend_for(request.provider).build_plan(request)


def missing_runtime_dependencies(plan: RuntimePlan) -> List[str]:
    missing: List[str] = []
    if plan.provider_kind == "codex-cli" and plan.command:
        if shutil.which(plan.command[0]) is None:
            missing.append(plan.command[0])
    if plan.provider_kind == "litellm":
        if find_spec("litellm") is None:
            missing.append("python package: litellm")
    if plan.provider_kind in {"litellm", "openai-compatible", "anthropic-compatible"} and plan.api_key_env:
        if not os.getenv(plan.api_key_env):
            missing.append(plan.api_key_env)
    return missing


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_run_id(prompt: str) -> str:
    match = re.search(r"Run_id:\s*([A-Za-z0-9._-]+)", prompt)
    if match:
        return match.group(1)
    return "mock_run"
