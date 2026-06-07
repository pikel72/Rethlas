from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]


CONFIG_FILENAME = "rethlas.toml"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str
    command: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None


@dataclass(frozen=True)
class ModelConfig:
    name: str
    provider: str
    model: str
    reasoning_effort: Optional[str] = None
    api_key_env: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeConfig:
    default_model: str
    timeout_seconds: int = 3600


@dataclass(frozen=True)
class VerificationConfig:
    host: str = "127.0.0.1"
    port: int = 8091

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class PathsConfig:
    generation_dir: Path
    verification_dir: Path


@dataclass(frozen=True)
class RethlasConfig:
    repo_root: Path
    runtime: RuntimeConfig
    providers: Mapping[str, ProviderConfig]
    models: Mapping[str, ModelConfig]
    verification: VerificationConfig
    paths: PathsConfig

    def resolve_model(self, requested_model: Optional[str] = None) -> ModelConfig:
        model_name = requested_model or os.getenv("RETHLAS_MODEL") or self.runtime.default_model
        try:
            return self.models[model_name]
        except KeyError as exc:
            available = ", ".join(sorted(self.models))
            raise ValueError(f"Unknown model profile '{model_name}'. Available models: {available}") from exc

    def resolve_provider(self, model: ModelConfig) -> ProviderConfig:
        try:
            return self.providers[model.provider]
        except KeyError as exc:
            available = ", ".join(sorted(self.providers))
            raise ValueError(
                f"Model '{model.name}' references unknown provider '{model.provider}'. "
                f"Available providers: {available}"
            ) from exc


def find_repo_root(start: Optional[Path] = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / CONFIG_FILENAME).exists() and (candidate / "agents").is_dir():
            return candidate
    raise FileNotFoundError(f"Could not find {CONFIG_FILENAME} above {current}")


def _load_toml(path: Path) -> Dict[str, Any]:
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required to read rethlas.toml without extra dependencies")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _as_dict(raw: Any, name: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be a TOML table")
    return raw


def load_config(repo_root: Optional[Path] = None) -> RethlasConfig:
    root = (repo_root or find_repo_root()).resolve()
    raw = _load_toml(root / CONFIG_FILENAME)

    runtime_raw = _as_dict(raw.get("runtime"), "runtime")
    runtime = RuntimeConfig(
        default_model=str(runtime_raw.get("default_model", "gpt-5.5")),
        timeout_seconds=int(runtime_raw.get("timeout_seconds", 3600)),
    )

    providers: Dict[str, ProviderConfig] = {}
    for name, provider_raw in _as_dict(raw.get("providers"), "providers").items():
        provider_table = _as_dict(provider_raw, f"providers.{name}")
        providers[name] = ProviderConfig(
            name=name,
            kind=str(provider_table["kind"]),
            command=provider_table.get("command"),
            base_url=provider_table.get("base_url"),
            api_key_env=provider_table.get("api_key_env"),
        )

    models: Dict[str, ModelConfig] = {}
    for name, model_raw in _as_dict(raw.get("models"), "models").items():
        model_table = _as_dict(model_raw, f"models.{name}")
        known = {"provider", "model", "reasoning_effort", "api_key_env"}
        models[name] = ModelConfig(
            name=name,
            provider=str(model_table["provider"]),
            model=str(model_table.get("model", name)),
            reasoning_effort=model_table.get("reasoning_effort"),
            api_key_env=model_table.get("api_key_env"),
            extra={key: value for key, value in model_table.items() if key not in known},
        )

    verification_raw = _as_dict(raw.get("verification"), "verification")
    verification = VerificationConfig(
        host=str(verification_raw.get("host", "127.0.0.1")),
        port=int(verification_raw.get("port", 8091)),
    )

    paths_raw = _as_dict(raw.get("paths"), "paths")
    paths = PathsConfig(
        generation_dir=(root / str(paths_raw.get("generation_dir", "agents/generation"))).resolve(),
        verification_dir=(root / str(paths_raw.get("verification_dir", "agents/verification"))).resolve(),
    )

    return RethlasConfig(
        repo_root=root,
        runtime=runtime,
        providers=providers,
        models=models,
        verification=verification,
        paths=paths,
    )
