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

    def _api_base_url(self, request: RuntimeRequest) -> Optional[str]:
        return request.model.api_base or request.provider.base_url

    def build_plan(self, request: RuntimeRequest) -> RuntimePlan:
        notes = ["LiteLLM backend supports plain model calls."]
        if request.role == "verification":
            notes.append("Verification JSON extraction and writing is implemented.")
        else:
            notes.append("Full Rethlas tool/MCP loop integration is not implemented yet.")
        api_base_url = self._api_base_url(request)
        return RuntimePlan(
            role=request.role,
            provider_name=request.provider.name,
            provider_kind=request.provider.kind,
            model_profile=request.model.name,
            model=request.model.model,
            cwd=request.cwd,
            log_path=request.log_path,
            command=None,
            api_base_url=api_base_url,
            api_key_env=self._api_key_env(request),
            implemented=True,
            notes=notes,
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
        prompt = request.prompt
        if request.role == "verification":
            prompt = _verification_json_prompt(request.prompt)

        completion_kwargs: Dict[str, Any] = {
            "model": request.model.model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": request.timeout_seconds,
        }
        api_key_env = self._api_key_env(request)
        if api_key_env:
            api_key = os.getenv(api_key_env)
            if api_key:
                completion_kwargs["api_key"] = api_key
        api_base = self._api_base_url(request)
        if api_base:
            completion_kwargs["api_base"] = api_base
        if request.model.compat:
            completion_kwargs["custom_llm_provider"] = request.model.compat
        completion_kwargs.update(_litellm_options(request.model))

        response = litellm.completion(**completion_kwargs)
        content = response.choices[0].message.content or ""
        log_text = (
            f"started_at_utc: {started_at}\n"
            f"provider: {request.provider.name} ({request.provider.kind})\n"
            f"model_profile: {request.model.name}\n"
            f"model: {request.model.model}\n\n"
            f"{content}"
        )
        request.log_path.write_text(log_text, encoding="utf-8")
        error: Optional[str] = None
        returncode = 0
        if request.role == "verification":
            try:
                payload = _extract_json_object(content)
                _validate_verification_payload(payload)
                output_path = request.log_path.parent / "verification.json"
                output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            except ValueError as exc:
                error = str(exc)
                returncode = 1
        if stream:
            print(content)
        usage = getattr(response, "usage", None)
        return RuntimeResult(
            returncode=returncode,
            started_at=started_at,
            ended_at=_utc_now(),
            log_path=request.log_path,
            output_text=content,
            usage=usage if isinstance(usage, dict) else None,
            provider_metadata={"provider": request.provider.name, "model": request.model.model},
            error=error,
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


def _litellm_options(model: ModelConfig) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    if model.max_tokens is not None:
        options["max_tokens"] = model.max_tokens
    if model.temperature is not None:
        options["temperature"] = model.temperature
    if model.top_p is not None:
        options["top_p"] = model.top_p
    if model.reasoning_effort is not None:
        options["reasoning_effort"] = model.reasoning_effort
    return options


def _verification_json_prompt(prompt: str) -> str:
    return (
        prompt
        + "\n\n"
        + "Return only a JSON object with exactly these top-level keys: "
        + "verification_report, verdict, repair_hints. "
        + "verification_report must contain summary, critical_errors, and gaps. "
        + "verdict must be either correct or wrong. Do not wrap the JSON in markdown."
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("model returned empty verification output")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model output did not contain a JSON object")
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"model output contained invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("verification output must be a JSON object")
    return payload


def _validate_verification_payload(payload: Dict[str, Any]) -> None:
    report = payload.get("verification_report")
    verdict = payload.get("verdict")
    repair_hints = payload.get("repair_hints")
    if not isinstance(report, dict):
        raise ValueError("verification_report must be an object")
    if verdict not in {"correct", "wrong"}:
        raise ValueError("verdict must be 'correct' or 'wrong'")
    if not isinstance(repair_hints, str):
        raise ValueError("repair_hints must be a string")
    for key in ("summary", "critical_errors", "gaps"):
        if key not in report:
            raise ValueError(f"verification_report.{key} is missing")
    if not isinstance(report["summary"], str):
        raise ValueError("verification_report.summary must be a string")
    if not isinstance(report["critical_errors"], list):
        raise ValueError("verification_report.critical_errors must be a list")
    if not isinstance(report["gaps"], list):
        raise ValueError("verification_report.gaps must be a list")
    has_findings = bool(report["critical_errors"] or report["gaps"])
    if verdict == "correct" and (has_findings or repair_hints):
        raise ValueError("correct verdict requires no findings and empty repair_hints")
    if verdict == "wrong" and not repair_hints.strip():
        raise ValueError("wrong verdict requires non-empty repair_hints")
