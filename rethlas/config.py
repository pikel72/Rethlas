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


from .presets import BUILTIN_PRESETS, base_url_env_name


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
    api_base: Optional[str] = None
    compat: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    thinking_budget_tokens: Optional[int] = None
    supports_tools: bool = False
    supports_streaming: bool = False
    context_window: Optional[int] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeConfig:
    default_model: str
    timeout_seconds: int = 3600


@dataclass(frozen=True)
class AgentsConfig:
    max_threads: int = 10
    max_depth: int = 3
    job_max_runtime_seconds: int = 3600


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


def _resolve_env_preset(name: str) -> ModelConfig:
    """Resolve a built-in env preset name to a ModelConfig, reading .env credentials."""
    if name not in BUILTIN_PRESETS:
        available = sorted(BUILTIN_PRESETS)
        raise ValueError(
            f"Unknown env preset {name!r}. Built-in presets: {', '.join(available)}"
        )
    preset = BUILTIN_PRESETS[name]

    api_key = os.getenv(preset.key_env)
    if not preset.key_optional and not api_key:
        raise ValueError(
            f"Preset {name!r} requires {preset.key_env} to be set. "
            f"Add it to .env or your shell, or unset RETHLAS_MODEL to use the default."
        )

    if name == "custom":
        base_url = os.getenv("CUSTOM_API_BASE")
        compat = os.getenv("CUSTOM_COMPAT", "").strip().lower()
        custom_model = os.getenv("CUSTOM_MODEL")
        missing = []
        if not base_url:
            missing.append("CUSTOM_API_BASE")
        if compat not in {"openai", "anthropic"}:
            missing.append("CUSTOM_COMPAT (openai|anthropic)")
        if not custom_model:
            missing.append("CUSTOM_MODEL")
        if missing:
            raise ValueError(
                f"Preset 'custom' requires the following env var(s) to be set: "
                f"{', '.join(missing)}."
            )
    else:
        # Allow two env var forms for the base URL override:
        #   1. The formal <KEY_ENV>_BASE form (per base_url_env_name) — used by
        #      presets that set base_url_env_override.
        #   2. The user-friendly <VENDOR>_API_BASE form (e.g. DEEPSEEK_API_BASE),
        #      matching the .env.example template.
        base_url_env = base_url_env_name(preset)
        vendor_base_env = preset.key_env.replace("_API_KEY", "_API_BASE")
        base_url = (
            os.getenv(base_url_env)
            or os.getenv(vendor_base_env)
            or preset.base_url
        )
        compat = preset.compat

    model_name = os.getenv(preset.model_env_override)
    if not model_name:
        raise ValueError(
            f"Preset {name!r} requires {preset.model_env_override} to be set in .env. "
            f"See .env.example for example model names per vendor. "
            f"Unset RETHLAS_MODEL to use the default (Codex) instead."
        )

    return ModelConfig(
        name=name,
        provider="litellm",
        model=model_name,
        api_key_env=preset.key_env,
        api_base=base_url,
        compat=compat,
        supports_tools=True,
        supports_streaming=True,
    )


@dataclass(frozen=True)
class RethlasConfig:
    repo_root: Path
    runtime: RuntimeConfig
    agents: AgentsConfig
    providers: Mapping[str, ProviderConfig]
    models: Mapping[str, ModelConfig]
    verification: VerificationConfig
    paths: PathsConfig

    def resolve_model(self, requested_model: Optional[str] = None) -> ModelConfig:
        model_name = requested_model or os.getenv("RETHLAS_MODEL") or self.runtime.default_model
        # 1. toml-registered profiles: codex, mock-*, and any remaining user-defined
        if model_name in self.models:
            return self.models[model_name]
        # 2. env presets: built-in table
        if model_name in BUILTIN_PRESETS:
            return _resolve_env_preset(model_name)
        # 2.5 alias: "codex" → toml's gpt-5.5
        if model_name == "codex" and "gpt-5.5" in self.models:
            return self.models["gpt-5.5"]
        # 3. unknown name → helpful error
        toml_names = sorted(self.models)
        env_names = sorted(BUILTIN_PRESETS)
        raise ValueError(
            f"Unknown model profile {model_name!r}. "
            f"Available: toml=[{', '.join(toml_names)}], env_presets=[{', '.join(env_names)}]"
        )

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


def _load_dotenv_if_present(path: Path) -> int:
    """Load KEY=VALUE pairs from a `.env` file into `os.environ` if present.

    - Skips blank lines and lines starting with `#`.
    - Supports `KEY=value`, `KEY="value"`, `KEY='value'`.
    - Existing env vars win (does not overwrite) — matches python-dotenv default.
    - Stops silently if the file doesn't exist.
    - Skips malformed lines (e.g. lines without `=`) without raising.

    Returns the number of NEW env vars set (i.e. vars that were not already
    in `os.environ`).
    """
    if not path.exists():
        return 0
    set_count = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if not key:
            continue
        if key in os.environ:
            continue  # existing env wins
        os.environ[key] = value
        set_count += 1
    return set_count


def load_dotenv_from_repo_root(repo_root: Path) -> int:
    """Load `.env` from the repo root. Used by the CLI at startup so users
    don't have to `set -a; source .env` manually before every command.

    Library users who import `load_config` directly should call this
    themselves if they want the same behavior.
    """
    return _load_dotenv_if_present(repo_root / ".env")


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

    agents_raw = _as_dict(raw.get("agents"), "agents")
    agents = AgentsConfig(
        max_threads=int(agents_raw.get("max_threads", 10)),
        max_depth=int(agents_raw.get("max_depth", 3)),
        job_max_runtime_seconds=int(agents_raw.get("job_max_runtime_seconds", 3600)),
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
        known = {
            "provider",
            "model",
            "reasoning_effort",
            "api_key_env",
            "max_tokens",
            "temperature",
            "top_p",
            "thinking_budget_tokens",
            "supports_tools",
            "supports_streaming",
            "context_window",
        }
        models[name] = ModelConfig(
            name=name,
            provider=str(model_table["provider"]),
            model=str(model_table.get("model", name)),
            reasoning_effort=model_table.get("reasoning_effort"),
            api_key_env=model_table.get("api_key_env"),
            max_tokens=_optional_int(model_table.get("max_tokens")),
            temperature=_optional_float(model_table.get("temperature")),
            top_p=_optional_float(model_table.get("top_p")),
            thinking_budget_tokens=_optional_int(model_table.get("thinking_budget_tokens")),
            supports_tools=bool(model_table.get("supports_tools", False)),
            supports_streaming=bool(model_table.get("supports_streaming", False)),
            context_window=_optional_int(model_table.get("context_window")),
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
        agents=agents,
        providers=providers,
        models=models,
        verification=verification,
        paths=paths,
    )


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)
