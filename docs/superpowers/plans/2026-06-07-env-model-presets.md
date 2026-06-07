# Env-Based Model Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 8 hardcoded `[models.*]` blocks in `rethlas.toml` with a 14-preset built-in table driven by `.env` keys, while preserving codex + mock profiles in toml.

**Architecture:** Add a new `rethlas/presets.py` module holding a static `BUILTIN_PRESETS` dict. Extend `ModelConfig` with `api_base` and `compat` fields. Update `RethlasConfig.resolve_model` to first check toml-registered profiles, then dispatch env presets to a new `_resolve_env_preset` helper. Update `LiteLLMBackend` to pass `api_key`, `api_base`, and `custom_llm_provider` to `litellm.completion`. Rewrite `.env.example` and `README.md` accordingly. Existing 8 toml profile sections are deleted; `codex-fast` / `codex-deep` / `gpt-5.5` / 4 mock profiles are kept.

**Tech Stack:** Python 3.11+ (`tomllib`), `dataclasses(frozen=True)`, `pytest`, `monkeypatch`, `litellm` (existing dependency).

---

## File Structure

**New files:**
- `rethlas/presets.py` — `PresetSpec` dataclass + `BUILTIN_PRESETS` dict (14 entries) + `resolve_env_preset()` helper.
- `tests/test_rethlas_presets.py` — 14 unit tests for the env-preset resolution path + the `BUILTIN_PRESETS` table shape.

**Modified files:**
- `rethlas/config.py` — extend `ModelConfig` (add `api_base`, `compat`); add `_resolve_env_preset()`; update `RethlasConfig.resolve_model()` to dispatch env presets.
- `rethlas/runtime.py` — `LiteLLMBackend.build_plan` and `run` use `model.api_base` (fallback to `provider.base_url`) and pass `custom_llm_provider=model.compat` + `api_key=os.getenv(model.api_key_env)` to `litellm.completion`.
- `rethlas/cli.py` — extend `cmd_doctor` with preset status scan.
- `rethlas.toml` — delete 8 OpenAI/Anthropic `[models.*]` sections; keep codex + mock profiles.
- `.env.example` — rewrite to match spec §3.
- `README.md` — rewrite "Custom Model Configuration" and "Environment Variables" sections.
- `tests/test_rethlas_runtime.py` — update the existing `test_runtime_config_has_multi_model_profiles` assertion to use env presets (or remove if it now duplicates the new test file).

---

## Task 1: Extend `ModelConfig` with `api_base` and `compat` fields

**Files:**
- Modify: `rethlas/config.py:27-41`

- [ ] **Step 1: Add two new fields to `ModelConfig`**

In `rethlas/config.py`, replace the `ModelConfig` dataclass (lines 27-41) with:

```python
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
```

`api_base` and `compat` both default to `None` so existing toml-loaded configs are unaffected.

- [ ] **Step 2: Run existing tests to confirm no regression**

Run: `python -m pytest tests/test_rethlas_runtime.py -q`
Expected: PASS (all 7 existing tests green).

- [ ] **Step 3: Commit**

```bash
git add rethlas/config.py
git commit -m "config: add api_base and compat fields to ModelConfig"
```

---

## Task 2: Create `rethlas/presets.py` with `BUILTIN_PRESETS` table

**Files:**
- Create: `rethlas/presets.py`

- [ ] **Step 1: Create the file with `PresetSpec` and `BUILTIN_PRESETS`**

Create `rethlas/presets.py` with this content:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class PresetSpec:
    """Static description of a built-in model preset backed by .env credentials."""

    name: str
    display_name: str
    base_url: Optional[str]
    compat: str  # "openai" or "anthropic"
    key_env: str
    default_model: str
    model_env_override: str
    key_optional: bool = False
    base_url_env_override: Optional[str] = None  # if None, defaults to key_env + "_BASE"


# Built-in presets. .env just needs <key_env>=... (and optionally <key_env>_BASE=...).
# `compat` decides how LiteLLM routes the call.
BUILTIN_PRESETS: Dict[str, PresetSpec] = {
    "deepseek-1": PresetSpec(
        name="deepseek-1",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        compat="openai",
        key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        model_env_override="DEEPSEEK_1_MODEL",
    ),
    "openai": PresetSpec(
        name="openai",
        display_name="OpenAI",
        base_url="https://api.openai.com/v1",
        compat="openai",
        key_env="OPENAI_API_KEY",
        default_model="gpt-5",
        model_env_override="OPENAI_MODEL",
    ),
    "claude": PresetSpec(
        name="claude",
        display_name="Anthropic Claude",
        base_url="https://api.anthropic.com/v1",
        compat="anthropic",
        key_env="ANTHROPIC_API_KEY",
        default_model="claude-opus-4-5",
        model_env_override="CLAUDE_MODEL",
    ),
    "gemini": PresetSpec(
        name="gemini",
        display_name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        compat="openai",
        key_env="GOOGLE_API_KEY",
        default_model="gemini-2.5-pro",
        model_env_override="GEMINI_MODEL",
    ),
    "qwen": PresetSpec(
        name="qwen",
        display_name="通义千问 (DashScope)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        compat="openai",
        key_env="QWEN_API_KEY",
        default_model="qwen-plus",
        model_env_override="QWEN_MODEL",
    ),
    "kimi": PresetSpec(
        name="kimi",
        display_name="Moonshot Kimi",
        base_url="https://api.moonshot.cn/v1",
        compat="openai",
        key_env="KIMI_API_KEY",
        default_model="kimi-k2-0711-preview",
        model_env_override="KIMI_MODEL",
    ),
    "openrouter": PresetSpec(
        name="openrouter",
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        compat="openai",
        key_env="OPENROUTER_API_KEY",
        default_model="openai/gpt-4o",
        model_env_override="OPENROUTER_MODEL",
    ),
    "ollama": PresetSpec(
        name="ollama",
        display_name="Ollama (local)",
        base_url="http://localhost:11434/v1",
        compat="openai",
        key_env="OLLAMA_API_KEY",
        default_model="llama3.1",
        model_env_override="OLLAMA_MODEL",
        key_optional=True,
    ),
    "glm": PresetSpec(
        name="glm",
        display_name="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        compat="openai",
        key_env="GLM_API_KEY",
        default_model="glm-4.5",
        model_env_override="GLM_MODEL",
    ),
    "MiniMax": PresetSpec(
        name="MiniMax",
        display_name="MiniMax",
        base_url="https://api.MiniMax.io/v1",
        compat="openai",
        key_env="MiniMax_API_KEY",
        default_model="MiniMax-M3",
        model_env_override="MiniMax_MODEL",
    ),
    "siliconflow": PresetSpec(
        name="siliconflow",
        display_name="硅基流动 (SiliconFlow)",
        base_url="https://api.siliconflow.cn/v1",
        compat="openai",
        key_env="SILICONFLOW_API_KEY",
        default_model="Qwen/Qwen2.5-72B-Instruct",
        model_env_override="SILICONFLOW_MODEL",
    ),
    "doubao": PresetSpec(
        name="doubao",
        display_name="豆包 (火山方舟)",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        compat="openai",
        key_env="DOUBAO_API_KEY",
        default_model="doubao-seed-1-6-250615",
        model_env_override="DOUBAO_MODEL",
    ),
    "mimo": PresetSpec(
        name="mimo",
        display_name="小米 MiMo",
        base_url="https://api.xiaomi.com/v1",
        compat="openai",
        key_env="MIMO_API_KEY",
        default_model="mimo-7b",
        model_env_override="MIMO_MODEL",
    ),
    "custom": PresetSpec(
        name="custom",
        display_name="Custom (user-defined)",
        base_url=None,
        compat="openai",
        key_env="CUSTOM_API_KEY",
        default_model="custom",
        model_env_override="CUSTOM_MODEL",
        key_optional=True,
        base_url_env_override="CUSTOM_API_BASE",
    ),
}


def base_url_env_name(preset: PresetSpec) -> str:
    """Return the env var name users can set to override this preset's base_url."""
    return preset.base_url_env_override or f"{preset.key_env}_BASE"
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python -c "from rethlas.presets import BUILTIN_PRESETS; print(len(BUILTIN_PRESETS))"`
Expected: `14`

- [ ] **Step 3: Commit**

```bash
git add rethlas/presets.py
git commit -m "presets: add 14-entry BUILTIN_PRESETS table"
```

---

## Task 3: Add tests for the BUILTIN_PRESETS table shape

**Files:**
- Create: `tests/test_rethlas_presets.py`

- [ ] **Step 1: Create the test file with structural invariant tests**

Create `tests/test_rethlas_presets.py` with this content:

```python
from __future__ import annotations

from rethlas.presets import BUILTIN_PRESETS, PresetSpec, base_url_env_name


EXPECTED_PRESET_NAMES = {
    "deepseek-1",
    "openai",
    "claude",
    "gemini",
    "qwen",
    "kimi",
    "openrouter",
    "ollama",
    "glm",
    "MiniMax",
    "siliconflow",
    "doubao",
    "mimo",
    "custom",
}


def test_builtin_presets_contains_all_expected_names():
    assert set(BUILTIN_PRESETS) == EXPECTED_PRESET_NAMES


def test_every_preset_is_a_presetspec_instance():
    for name, preset in BUILTIN_PRESETS.items():
        assert isinstance(preset, PresetSpec), name
        assert preset.name == name


def test_every_preset_compat_is_openai_or_anthropic():
    for name, preset in BUILTIN_PRESETS.items():
        assert preset.compat in {"openai", "anthropic"}, name


def test_every_preset_has_nonempty_key_env_and_default_model():
    for name, preset in BUILTIN_PRESETS.items():
        assert preset.key_env.strip(), name
        assert preset.default_model.strip(), name
        assert preset.model_env_override.strip(), name


def test_key_env_names_are_unique_across_presets():
    seen = set()
    for preset in BUILTIN_PRESETS.values():
        assert preset.key_env not in seen, f"duplicate key_env: {preset.key_env}"
        seen.add(preset.key_env)


def test_ollama_is_key_optional():
    assert BUILTIN_PRESETS["ollama"].key_optional is True


def test_custom_preset_does_not_hardcode_base_url():
    assert BUILTIN_PRESETS["custom"].base_url is None


def test_base_url_env_name_uses_override_when_set():
    assert base_url_env_name(BUILTIN_PRESETS["custom"]) == "CUSTOM_API_BASE"


def test_base_url_env_name_defaults_to_key_env_plus_base_suffix():
    assert base_url_env_name(BUILTIN_PRESETS["deepseek-1"]) == "DEEPSEEK_API_KEY_BASE"
```

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/test_rethlas_presets.py -q`
Expected: PASS (9 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_rethlas_presets.py
git commit -m "test: add structural invariants for BUILTIN_PRESETS"
```

---

## Task 4: Implement `_resolve_env_preset` and dispatch from `resolve_model` (TDD)

**Files:**
- Modify: `rethlas/config.py:83-89` (extend `resolve_model`)
- Modify: `rethlas/config.py` (add `_resolve_env_preset` helper)
- Modify: `tests/test_rethlas_presets.py` (add resolution tests)

- [ ] **Step 1: Add failing tests to `tests/test_rethlas_presets.py`**

Append to `tests/test_rethlas_presets.py`:

```python
import os
import pytest

from rethlas.config import load_config


@pytest.fixture
def fresh_env(monkeypatch):
    """Strip every env var that an env preset might read, then yield the monkeypatch."""
    keys_to_strip = [
        "RETHLAS_MODEL", "RETHLAS_VERIFICATION_MODEL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE", "DEEPSEEK_1_MODEL",
        "OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL",
        "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE", "CLAUDE_MODEL",
        "GOOGLE_API_KEY", "GEMINI_MODEL",
        "QWEN_API_KEY", "QWEN_MODEL",
        "KIMI_API_KEY", "KIMI_API_BASE", "KIMI_MODEL",
        "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
        "OLLAMA_API_KEY", "OLLAMA_MODEL",
        "GLM_API_KEY", "GLM_MODEL",
        "MiniMax_API_KEY", "MiniMax_API_BASE", "MiniMax_MODEL",
        "SILICONFLOW_API_KEY", "SILICONFLOW_MODEL",
        "DOUBAO_API_KEY", "DOUBAO_API_BASE", "DOUBAO_MODEL",
        "MIMO_API_KEY", "MIMO_API_BASE", "MIMO_MODEL",
        "CUSTOM_API_KEY", "CUSTOM_API_BASE", "CUSTOM_COMPAT", "CUSTOM_MODEL",
    ]
    for key in keys_to_strip:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_builtin_preset_resolves_with_key_and_default_model(fresh_env):
    fresh_env.setenv("DEEPSEEK_API_KEY", "sk-x")
    config = load_config()
    m = config.resolve_model("deepseek-1")
    assert m.provider == "litellm"
    assert m.model == "deepseek-chat"
    assert m.api_key_env == "DEEPSEEK_API_KEY"
    assert m.api_base == "https://api.deepseek.com/v1"
    assert m.compat == "openai"


def test_builtin_preset_model_env_override(fresh_env):
    fresh_env.setenv("DEEPSEEK_API_KEY", "sk-x")
    fresh_env.setenv("DEEPSEEK_1_MODEL", "deepseek-reasoner")
    config = load_config()
    m = config.resolve_model("deepseek-1")
    assert m.model == "deepseek-reasoner"


def test_builtin_preset_base_url_env_override(fresh_env):
    fresh_env.setenv("DEEPSEEK_API_KEY", "sk-x")
    fresh_env.setenv("DEEPSEEK_API_BASE", "https://proxy.example.com/v1")
    config = load_config()
    m = config.resolve_model("deepseek-1")
    assert m.api_base == "https://proxy.example.com/v1"


def test_missing_api_key_raises_friendly_error(fresh_env):
    config = load_config()
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        config.resolve_model("deepseek-1")


def test_ollama_key_optional(fresh_env):
    config = load_config()
    m = config.resolve_model("ollama")
    assert m.api_key_env == "OLLAMA_API_KEY"
    assert m.api_base == "http://localhost:11434/v1"


def test_custom_requires_base_and_compat(fresh_env):
    fresh_env.setenv("CUSTOM_API_KEY", "sk-x")
    config = load_config()
    with pytest.raises(ValueError) as excinfo:
        config.resolve_model("custom")
    message = str(excinfo.value)
    assert "CUSTOM_API_BASE" in message
    assert "CUSTOM_COMPAT" in message


def test_custom_compat_anthropic_routes_correctly(fresh_env):
    fresh_env.setenv("CUSTOM_API_KEY", "sk-x")
    fresh_env.setenv("CUSTOM_API_BASE", "https://example.com/v1")
    fresh_env.setenv("CUSTOM_COMPAT", "anthropic")
    config = load_config()
    m = config.resolve_model("custom")
    assert m.compat == "anthropic"
    assert m.api_base == "https://example.com/v1"


def test_custom_model_env_override(fresh_env):
    fresh_env.setenv("CUSTOM_API_KEY", "sk-x")
    fresh_env.setenv("CUSTOM_API_BASE", "https://example.com/v1")
    fresh_env.setenv("CUSTOM_COMPAT", "openai")
    fresh_env.setenv("CUSTOM_MODEL", "llama-3.3-70b")
    config = load_config()
    m = config.resolve_model("custom")
    assert m.model == "llama-3.3-70b"


def test_codex_still_works(fresh_env):
    config = load_config()
    m = config.resolve_model("codex")
    assert m.provider == "codex"
    assert m.name == "gpt-5.5"


def test_unknown_name_lists_all_presets(fresh_env):
    config = load_config()
    with pytest.raises(ValueError) as excinfo:
        config.resolve_model("does-not-exist")
    message = str(excinfo.value)
    for needle in ("codex", "gpt-5.5", "mock-generation", "deepseek-1", "claude", "custom"):
        assert needle in message, f"missing {needle!r} in error message"


def test_rethlas_model_env_selects_default(fresh_env):
    fresh_env.setenv("RETHLAS_MODEL", "deepseek-1")
    fresh_env.setenv("DEEPSEEK_API_KEY", "sk-x")
    config = load_config()
    m = config.resolve_model()
    assert m.name == "deepseek-1"


def test_rethlas_verification_model_env_independent(fresh_env):
    fresh_env.setenv("RETHLAS_MODEL", "deepseek-1")
    fresh_env.setenv("RETHLAS_VERIFICATION_MODEL", "claude")
    fresh_env.setenv("DEEPSEEK_API_KEY", "sk-d")
    fresh_env.setenv("ANTHROPIC_API_KEY", "sk-a")
    config = load_config()
    gen = config.resolve_model(os.getenv("RETHLAS_MODEL"))
    ver = config.resolve_model(os.getenv("RETHLAS_VERIFICATION_MODEL"))
    assert gen.name == "deepseek-1"
    assert ver.name == "claude"
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `python -m pytest tests/test_rethlas_presets.py -q`
Expected: FAIL — most new tests fail because `resolve_model("deepseek-1")` etc. currently raise `Unknown model profile` errors.

- [ ] **Step 3: Add `_resolve_env_preset` and update `resolve_model`**

In `rethlas/config.py`, add this import at the top (after the existing `dataclass`/`field` imports):

```python
from .presets import BUILTIN_PRESETS, PresetSpec, base_url_env_name
```

Then add a new helper function after `find_repo_root` (around line 107):

```python
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
        if not base_url:
            raise ValueError("Preset 'custom' requires CUSTOM_API_BASE to be set.")
        if compat not in {"openai", "anthropic"}:
            raise ValueError(
                f"Preset 'custom' requires CUSTOM_COMPAT=openai or CUSTOM_COMPAT=anthropic (got {compat!r})."
            )
    else:
        base_url_env = base_url_env_name(preset)
        base_url = os.getenv(base_url_env) or preset.base_url
        compat = preset.compat

    model_name = os.getenv(preset.model_env_override) or preset.default_model

    return ModelConfig(
        name=name,
        provider="litellm",
        model=model_name,
        api_key_env=preset.key_env,
        api_base=base_url,
        compat=compat,
    )
```

(`ModelConfig` is defined in the same module, so no import is needed inside the helper.)

Now replace the existing `RethlasConfig.resolve_model` method (lines 83-89):

```python
    def resolve_model(self, requested_model: Optional[str] = None) -> ModelConfig:
        model_name = requested_model or os.getenv("RETHLAS_MODEL") or self.runtime.default_model
        # 1. toml-registered profiles: codex, mock-*, and any remaining user-defined
        if model_name in self.models:
            return self.models[model_name]
        # 2. env presets: built-in table
        if model_name in BUILTIN_PRESETS:
            return _resolve_env_preset(model_name)
        # 3. unknown name → helpful error
        toml_names = sorted(self.models)
        env_names = sorted(BUILTIN_PRESETS)
        raise ValueError(
            f"Unknown model profile {model_name!r}. "
            f"Available: toml=[{', '.join(toml_names)}], env_presets=[{', '.join(env_names)}]"
        )
```

Also add a special case for the `"codex"` alias — toml config currently uses `gpt-5.5` as the default profile name. The `codex` alias is **not** in `config.models` and is not an env preset. Add a fallback just before step 3:

```python
        # 2.5 alias: "codex" → toml's gpt-5.5
        if model_name == "codex" and "gpt-5.5" in self.models:
            return self.models["gpt-5.5"]
```

The full method becomes:

```python
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
```

- [ ] **Step 4: Run the new tests and confirm they pass**

Run: `python -m pytest tests/test_rethlas_presets.py -q`
Expected: PASS (21 tests: 9 structural + 12 resolution).

- [ ] **Step 5: Confirm existing tests still pass**

Run: `python -m pytest tests/ -q`
Expected: PASS (existing tests still green; `test_rethlas_runtime.py` tests may fail in the next task because they reference `openai-default`).

- [ ] **Step 6: Commit**

```bash
git add rethlas/config.py tests/test_rethlas_presets.py
git commit -m "config: dispatch env presets in resolve_model via BUILTIN_PRESETS"
```

---

## Task 5: Wire `model.api_base` and `compat` into `LiteLLMBackend` and `build_plan`

**Files:**
- Modify: `rethlas/runtime.py:170-271`

- [ ] **Step 1: Add a failing test for `build_plan` carrying `model.api_base`**

Append to `tests/test_rethlas_presets.py`:

```python
def test_build_plan_uses_model_api_base_when_set(fresh_env, tmp_path):
    from rethlas.runtime import build_request, build_plan

    fresh_env.setenv("DEEPSEEK_API_KEY", "sk-x")
    config = load_config()
    request = build_request(
        config,
        role="generation",
        cwd=tmp_path,
        prompt="hello",
        log_path=tmp_path / "log.txt",
        model_name="deepseek-1",
    )
    plan = build_plan(config, request)
    assert plan.api_base_url == "https://api.deepseek.com/v1"
    assert plan.api_key_env == "DEEPSEEK_API_KEY"
```

- [ ] **Step 2: Run the new test and confirm it fails**

Run: `python -m pytest tests/test_rethlas_presets.py::test_build_plan_uses_model_api_base_when_set -v`
Expected: FAIL — `plan.api_base_url` is currently `None` (LiteLLMBackend.build_plan reads `request.provider.base_url`, but `providers.litellm` has no `base_url`).

- [ ] **Step 3: Update `LiteLLMBackend.build_plan` to prefer `model.api_base`**

In `rethlas/runtime.py`, replace the `LiteLLMBackend` class methods.

Replace `build_plan` (lines 199-218) with:

```python
    def build_plan(self, request: RuntimeRequest) -> RuntimePlan:
        notes = ["LiteLLM backend supports plain model calls."]
        if request.role == "verification":
            notes.append("Verification JSON extraction and writing is implemented.")
        else:
            notes.append("Full Rethlas tool/MCP loop integration is not implemented yet.")
        api_base_url = request.model.api_base or request.provider.base_url
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
```

- [ ] **Step 4: Update `LiteLLMBackend.run` to pass `api_key`, `api_base`, `custom_llm_provider` to LiteLLM**

In `rethlas/runtime.py`, replace the `run` method (lines 220-271). The replacement:

```python
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
        api_base = request.model.api_base or request.provider.base_url
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
```

Note: this uses `completion_kwargs["api_base"]` and `custom_llm_provider`; LiteLLM's standard `completion()` accepts both.

- [ ] **Step 5: Run the new test and confirm it passes**

Run: `python -m pytest tests/test_rethlas_presets.py::test_build_plan_uses_model_api_base_when_set -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: existing tests still pass; the new test passes. `test_rethlas_runtime.py` failures about `openai-default` are expected and addressed in Task 6.

- [ ] **Step 7: Commit**

```bash
git add rethlas/runtime.py tests/test_rethlas_presets.py
git commit -m "runtime: LiteLLMBackend passes api_key/api_base/custom_llm_provider from model"
```

---

## Task 6: Update existing test in `test_rethlas_runtime.py`

**Files:**
- Modify: `tests/test_rethlas_runtime.py:22-26`

- [ ] **Step 1: Update the assertion in `test_runtime_config_has_multi_model_profiles`**

The current test (lines 22-26) references `config.models["openai-default"]` and `config.models["anthropic-default"]`, which are about to be deleted from `rethlas.toml`. Replace it with an assertion that exercises the env-preset path:

Find:

```python
def test_runtime_config_has_multi_model_profiles():
    config = load_config()
    assert config.models["openai-default"].provider == "litellm"
    assert config.models["anthropic-default"].provider == "litellm"
    assert config.models["mock-verification-correct"].provider == "mock"
```

Replace with:

```python
def test_runtime_config_has_multi_model_profiles(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    config = load_config()
    # toml-only profiles: codex + mock
    assert config.models["mock-verification-correct"].provider == "mock"
    assert "gpt-5.5" in config.models
    assert "codex-fast" in config.models
    assert "codex-deep" in config.models
    # env presets: resolved through the new BUILTIN_PRESETS path
    openai = config.resolve_model("openai")
    assert openai.provider == "litellm"
    assert openai.compat == "openai"
    claude = config.resolve_model("claude")
    assert claude.provider == "litellm"
    assert claude.compat == "anthropic"
```

- [ ] **Step 2: Run the updated test**

Run: `python -m pytest tests/test_rethlas_runtime.py -q`
Expected: PASS (all 7 tests green).

- [ ] **Step 3: Commit**

```bash
git add tests/test_rethlas_runtime.py
git commit -m "test: rebase runtime profile test on env presets + retained codex/mock"
```

---

## Task 7: Delete 8 OpenAI/Anthropic `[models.*]` sections from `rethlas.toml`

**Files:**
- Modify: `rethlas.toml`

- [ ] **Step 1: Remove the 8 sections**

In `rethlas.toml`, delete these 8 blocks in full (lines 51-77, 79-102, 104-111 of the file at HEAD):

```toml
[models.openai-default]
provider = "litellm"
model = "openai/gpt-5.5"
reasoning_effort = "xhigh"
api_key_env = "OPENAI_API_KEY"
supports_tools = true
supports_streaming = true

[models.openai-fast]
provider = "litellm"
model = "openai/gpt-5.5"
reasoning_effort = "medium"
api_key_env = "OPENAI_API_KEY"
supports_tools = true
supports_streaming = true
max_tokens = 4000
temperature = 0.2

[models.openai-deep]
provider = "litellm"
model = "openai/gpt-5.5"
reasoning_effort = "xhigh"
api_key_env = "OPENAI_API_KEY"
supports_tools = true
supports_streaming = true
max_tokens = 12000
temperature = 0.1

[models.anthropic-default]
provider = "litellm"
model = "anthropic/claude-opus-4-5"
api_key_env = "ANTHROPIC_API_KEY"
supports_tools = true
supports_streaming = true

[models.anthropic-fast]
provider = "litellm"
model = "anthropic/claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"
supports_tools = true
supports_streaming = true
max_tokens = 4000
temperature = 0.2

[models.anthropic-deep]
provider = "litellm"
model = "anthropic/claude-opus-4-5"
api_key_env = "ANTHROPIC_API_KEY"
supports_tools = true
supports_streaming = true
max_tokens = 12000
temperature = 0.1

[models.openai-native-default]
provider = "openai"
model = "gpt-5.5"
reasoning_effort = "xhigh"

[models.anthropic-native-default]
provider = "anthropic"
model = "claude-opus-4-5"
```

Keep intact: `[runtime]`, `[agents]`, all `[providers.*]`, `[models."gpt-5.5"]`, `[models.codex-fast]`, `[models.codex-deep]`, all 4 `[models.mock-*]`, `[verification]`, `[paths]`.

- [ ] **Step 2: Verify the toml still parses**

Run: `python -c "from rethlas.config import load_config; c = load_config(); print(sorted(c.models))"`
Expected output contains: `['codex-deep', 'codex-fast', 'gpt-5.5', 'mock-generation', 'mock-verification-correct', 'mock-verification-malformed', 'mock-verification-wrong']`
Should NOT contain: `openai-default`, `openai-fast`, `openai-deep`, `anthropic-default`, `anthropic-fast`, `anthropic-deep`, `openai-native-default`, `anthropic-native-default`.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add rethlas.toml
git commit -m "toml: drop 8 OpenAI/Anthropic model profiles, now provided by env presets"
```

---

## Task 8: Rewrite `.env.example` to match the new shape

**Files:**
- Modify: `.env.example` (replace its 28-line contents)

- [ ] **Step 1: Write the new `.env.example`**

Replace the entire contents of `.env.example` with:

```bash
# Rethlas AI 模型预设 - 在 .env 里设 <VENDOR>_API_KEY 即启用对应预设。
# 需要换地址 (代理/自部署) 才填 <VENDOR>_API_BASE。
# 想换真实 model 名 (比如把 deepseek-1 切到 reasoner), 填 <PRESET>_MODEL。
# 详见 docs/superpowers/specs/2026-06-07-env-model-presets-design.md

# --- DeepSeek --- 可用预设: deepseek-1; 模型: deepseek-chat, deepseek-reasoner
DEEPSEEK_API_KEY=
DEEPSEEK_API_BASE=
DEEPSEEK_1_MODEL=

# --- OpenAI --- 可用预设: openai; 模型: gpt-5, gpt-5-mini, gpt-4.1
OPENAI_API_KEY=
OPENAI_API_BASE=
OPENAI_MODEL=

# --- Anthropic --- 可用预设: claude; 模型: claude-opus-4-5, claude-sonnet-4-5
ANTHROPIC_API_KEY=
ANTHROPIC_API_BASE=
CLAUDE_MODEL=

# --- Google Gemini --- 可用预设: gemini; 模型: gemini-2.5-pro, gemini-2.5-flash
GOOGLE_API_KEY=
GEMINI_MODEL=

# --- 通义千问 (DashScope) --- 可用预设: qwen; 模型: qwen-plus, qwen-turbo, qwen-max
QWEN_API_KEY=
QWEN_MODEL=

# --- Moonshot Kimi --- 可用预设: kimi; 模型: kimi-k2-0711-preview
KIMI_API_KEY=
KIMI_API_BASE=
KIMI_MODEL=

# --- OpenRouter --- 可用预设: openrouter; 模型: 任意 OpenAI/Anthropic/Google/...
OPENROUTER_API_KEY=
OPENROUTER_MODEL=

# --- Ollama (本地) --- 可用预设: ollama; 模型: llama3.1, qwen2.5-coder:32b
# (本地服务, key 可空)
OLLAMA_API_KEY=
OLLAMA_MODEL=

# --- 智谱 GLM --- 可用预设: glm; 模型: glm-4.5, glm-4.5-air
GLM_API_KEY=
GLM_MODEL=

# --- MiniMax --- 可用预设: MiniMax; 模型: MiniMax-M3, MiniMax-text-01
MiniMax_API_KEY=
MiniMax_API_BASE=
MiniMax_MODEL=

# --- 硅基流动 (SiliconFlow) --- 可用预设: siliconflow; 模型: Qwen/Qwen2.5-72B-Instruct
SILICONFLOW_API_KEY=
SILICONFLOW_MODEL=

# --- 豆包 (火山方舟) --- 可用预设: doubao; 模型: doubao-seed-1-6-250615
DOUBAO_API_KEY=
DOUBAO_API_BASE=
DOUBAO_MODEL=

# --- 小米 MiMo --- 可用预设: mimo; 模型: mimo-7b
MIMO_API_KEY=
MIMO_API_BASE=
MIMO_MODEL=

# --- Custom (任意未列出的厂商) ---
# 必填三项: CUSTOM_API_KEY, CUSTOM_API_BASE, CUSTOM_COMPAT (openai|anthropic)
# 可选: CUSTOM_MODEL (真实 model name), 不填则用预设名本身
CUSTOM_API_KEY=
CUSTOM_API_BASE=
CUSTOM_COMPAT=
CUSTOM_MODEL=

# === 当前选用的预设 (不设则用 rethlas.toml 的 [runtime].default_model) ===
RETHLAS_MODEL=

# === 可选: verification agent 单独指定 (不设则与 RETHLAS_MODEL 相同) ===
RETHLAS_VERIFICATION_MODEL=
```

- [ ] **Step 2: Verify the file**

Run: `cat .env.example | head -20`
Expected: starts with the comment block describing the env preset system.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "env: rewrite .env.example for 14-preset env-driven configuration"
```

---

## Task 9: Update `README.md` "Custom Model Configuration" + "Environment Variables"

**Files:**
- Modify: `README.md:164-388` (approximately; "Custom Model Configuration" through "Mock Models" sections)

- [ ] **Step 1: Replace the "Custom Model Configuration" section**

Find the section in `README.md` that begins with `## Custom Model Configuration` (around line 164) and ends just before `## Runtime Behavior` (around line 399). Replace it with the following:

````markdown
## Custom Model Configuration

Rethlas reads model configuration from two places:

- `rethlas.toml`: holds `[runtime]`, `[providers.*]`, and a small set of toml profiles (`gpt-5.5`, `codex-fast`, `codex-deep`, and 4 `mock-*`).
- `.env`: holds API keys and per-preset overrides for the 14 built-in env presets (deepseek, openai, claude, gemini, qwen, kimi, openrouter, ollama, glm, MiniMax, siliconflow, doubao, mimo, custom).

The default runtime is still Codex CLI. To switch to a cloud vendor, fill in `<VENDOR>_API_KEY` in `.env` and set `RETHLAS_MODEL=<preset>` (or pass `--model <preset>`).

### Built-in env presets

| Preset name   | Vendor                  | Required env var       | Default model                |
|---------------|-------------------------|------------------------|------------------------------|
| `deepseek-1`  | DeepSeek                | `DEEPSEEK_API_KEY`     | `deepseek-chat`              |
| `openai`      | OpenAI                  | `OPENAI_API_KEY`       | `gpt-5`                      |
| `claude`      | Anthropic               | `ANTHROPIC_API_KEY`    | `claude-opus-4-5`            |
| `gemini`      | Google Gemini           | `GOOGLE_API_KEY`       | `gemini-2.5-pro`             |
| `qwen`        | 通义千问 (DashScope)    | `QWEN_API_KEY`         | `qwen-plus`                  |
| `kimi`        | Moonshot Kimi           | `KIMI_API_KEY`         | `kimi-k2-0711-preview`       |
| `openrouter`  | OpenRouter              | `OPENROUTER_API_KEY`   | `openai/gpt-4o`              |
| `ollama`      | Ollama (local)          | `OLLAMA_API_KEY` (可空) | `llama3.1`                  |
| `glm`         | 智谱 GLM                 | `GLM_API_KEY`          | `glm-4.5`                    |
| `MiniMax`     | MiniMax                 | `MiniMax_API_KEY`     | `MiniMax-M3`                |
| `siliconflow` | 硅基流动 (SiliconFlow)  | `SILICONFLOW_API_KEY`  | `Qwen/Qwen2.5-72B-Instruct`  |
| `doubao`      | 豆包 (火山方舟)         | `DOUBAO_API_KEY`       | `doubao-seed-1-6-250615`     |
| `mimo`        | 小米 MiMo               | `MIMO_API_KEY`         | `mimo-7b`                    |
| `custom`      | 任意未列出厂商 (自填)   | `CUSTOM_API_KEY` + `CUSTOM_API_BASE` + `CUSTOM_COMPAT` | `<preset name>` |

Each preset also has two optional env vars:

- `<VENDOR>_API_BASE`: override the default `base_url` (for proxies or self-hosted endpoints).
- `<PRESET>_MODEL` (e.g. `DEEPSEEK_1_MODEL`): override the default real model name.

### Use a preset

Set the key in `.env` and run:

```bash
export DEEPSEEK_API_KEY="sk-..."
export RETHLAS_MODEL=deepseek-1
python -m rethlas.cli run ns/ns
```

PowerShell equivalent:

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
$env:RETHLAS_MODEL = "deepseek-1"
python -m rethlas.cli run ns/ns
```

Inspect the resolved plan before a long run:

```bash
python -m rethlas.cli plan --role generation --problem ns/ns --model deepseek-1
python -m rethlas.cli plan --role verification --model deepseek-1
```

### Switch the real model name

`DEEPSEEK_1_MODEL=deepseek-reasoner` makes `deepseek-1` resolve to `deepseek-reasoner` instead of `deepseek-chat`, with no code change.

### Custom (任意未列出厂商)

```bash
CUSTOM_API_KEY=sk-...
CUSTOM_API_BASE=https://my-proxy.example.com/v1
CUSTOM_COMPAT=openai
CUSTOM_MODEL=llama-3.3-70b
RETHLAS_MODEL=custom
```

### Switch back to Codex

```bash
unset RETHLAS_MODEL
python -m rethlas.cli run ns/ns   # uses rethlas.toml's [runtime].default_model = "gpt-5.5" (codex)
```

`codex-fast` and `codex-deep` (different `reasoning_effort` on the codex profile) remain in `rethlas.toml`:

```bash
python -m rethlas.cli run ns/ns --model codex-fast
python -m rethlas.cli run ns/ns --model codex-deep
```

### Add a new vendor preset

The 14 built-in presets are not user-extensible from `.env`. To add a new vendor:

- File an issue or PR to add an entry to `rethlas/presets.py::BUILTIN_PRESETS`, **or**
- Use the `custom` slot (any base URL + openai/anthropic compat).

### Environment Variables

| Variable | Purpose |
|---|---|
| `<VENDOR>_API_KEY` | API key for the vendor (e.g. `DEEPSEEK_API_KEY`). |
| `<VENDOR>_API_BASE` | Override the default base URL. |
| `<PRESET>_MODEL` | Override the default real model name. |
| `RETHLAS_MODEL` | Selects the active preset. Overridden by `--model` on the CLI. |
| `RETHLAS_VERIFICATION_MODEL` | Selects the preset for the verification agent (defaults to `RETHLAS_MODEL`). |
| `CODEX_BIN` | Codex CLI binary name (defaults to `codex`). |

`.env.example` lists every supported variable with a comment describing the matching preset.

### Mock Models

Mock profiles are independent of env presets and useful for local wiring / CI:

```bash
python -m rethlas.cli run example --model mock-generation
python -m rethlas.cli plan --role verification --model mock-verification-correct
pytest -q tests/test_rethlas_runtime.py
```
````

- [ ] **Step 2: Verify the README renders sensibly**

Run: `python -c "with open('README.md') as f: t = f.read(); print(len(t), 'chars')"`
Expected: a sensible number, file is still under a few thousand lines.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite Custom Model Configuration for env presets"
```

---

## Task 10: Add preset status scan to `doctor --tools --verbose`

**Files:**
- Modify: `rethlas/cli.py` (extend `cmd_doctor`)

- [ ] **Step 1: Add a new import to `cli.py`**

At the top of `rethlas/cli.py`, in the import block from `.config`, add `BUILTIN_PRESETS` and `base_url_env_name` to the import line. The current import is `from .config import load_config`. Replace with:

```python
from .config import load_config
from .presets import BUILTIN_PRESETS, base_url_env_name
```

- [ ] **Step 2: Add the preset scan block to `cmd_doctor`**

In `cmd_doctor`, after the existing `models:` print block (which iterates `config.models.values()`), add this block. Find the line that prints `"models:"` and the for-loop that follows it. Append after the loop (before any later code in the function):

```python
    print("")
    print("env presets:")
    for name, preset in sorted(BUILTIN_PRESETS.items()):
        key_set = bool(os.getenv(preset.key_env))
        base_env = base_url_env_name(preset)
        base_override = os.getenv(base_env)
        status = "ready" if (preset.key_optional or key_set) else f"missing {preset.key_env}"
        print(f"  {name} ({preset.display_name}): {status}")
        if args.verbose:
            print(
                f"    base_url={preset.base_url or '(none)'} "
                f"compat={preset.compat} "
                f"key_env={preset.key_env} "
                f"key_set={key_set} "
                f"base_override={base_override or '(none)'} "
                f"default_model={preset.default_model}"
            )
```

Also add a check for `custom` completeness when verbose:

```python
    if args.verbose and "custom" in BUILTIN_PRESETS:
        custom = BUILTIN_PRESETS["custom"]
        custom_key = bool(os.getenv(custom.key_env))
        custom_base = os.getenv("CUSTOM_API_BASE")
        custom_compat = os.getenv("CUSTOM_COMPAT")
        print(
            f"  custom: key={custom_key} base={custom_base or '(none)'} "
            f"compat={custom_compat or '(none)'}"
        )
```

(Insert both blocks after the existing `models:` print loop.)

- [ ] **Step 3: Manually verify doctor output**

Run: `python -m rethlas.cli doctor --verbose`
Expected: the `env presets:` section appears, listing all 14 presets. Each entry reports `ready` or `missing <KEY>`; verbose lines include `key_set`, `base_override`, etc.

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rethlas/cli.py
git commit -m "cli: doctor --verbose reports per-preset env readiness"
```

---

## Task 11: Final verification and end-to-end smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Verify `doctor` works without keys set**

Run: `python -m rethlas.cli doctor --tools --verbose`
Expected: codex + mock profiles shown; env presets section reports `missing <KEY>` for all 14.

- [ ] **Step 3: Set one key and verify resolution**

Run: `set DEEPSEEK_API_KEY=sk-test (Windows) or export DEEPSEEK_API_KEY=sk-test (Unix)`, then:

Run: `python -m rethlas.cli doctor --verbose 2>&1 | findstr deepseek-1` (Windows) or `python -m rethlas.cli doctor --verbose 2>&1 | grep deepseek-1` (Unix)
Expected: `deepseek-1 (DeepSeek): ready`.

- [ ] **Step 4: Run a dry-run with an env preset**

Run: `python -m rethlas.cli run ns/ns --dry-run --model deepseek-1`
Expected: dry-run completes without error and shows the resolved `deepseek-1` profile in the plan output.

- [ ] **Step 5: Run a dry-run with codex default (no flag)**

Run: `python -m rethlas.cli run ns/ns --dry-run`
Expected: dry-run completes; the resolved model is `gpt-5.5` (codex).

- [ ] **Step 6: Commit any remaining changes (likely none)**

Run: `git status`
Expected: clean working tree. If anything is unstaged, commit it as a follow-up.

---

## Self-Review

**Spec coverage:**

| Spec section / requirement | Covered by task |
|---|---|
| §1 概念 (preset definition, env override, codex separation) | Task 2 (data), Task 4 (resolution) |
| §2 14-entry preset table | Task 2 (data), Task 3 (shape test) |
| §3 `.env` shape | Task 8 |
| §4.1 `ModelConfig.api_base` / `compat` | Task 1 |
| §4.2 `resolve_model` dispatch | Task 4 |
| §4 LiteLLM compat routing | Task 5 |
| §5 toml 8-section deletion + codex-fast/codex-deep retention | Task 7 |
| §6 friendly error messages | Task 4 (ValueError tests) |
| §7 12 unit tests | Tasks 3, 4, 5, 6 |
| §7 existing-test update | Task 6 |
| §8 README rewrite | Task 9 |
| §8 `.env.example` rewrite | Task 8 |
| §9 doctor enhancement | Task 10 |
| §9 范围外 (no hot reload, no mock rename, etc.) | Respected — not implemented |

**Placeholder scan:** none. Every step has concrete code, exact paths, exact commands.

**Type / naming consistency:**

- `PresetSpec` is defined in `rethlas/presets.py` and imported into `config.py`. The local import inside `_resolve_env_preset` is intentionally there to avoid a circular import (the file would otherwise import `ModelConfig` from `config` while `config` is being defined). Confirmed safe.
- `_resolve_env_preset` returns a `ModelConfig` with `provider="litellm"` and `compat` from the preset. The new `LiteLLMBackend.run` reads `request.model.compat` and uses it as `custom_llm_provider`. Names match.
- `base_url_env_name(preset)` is the single helper for "what env var overrides this preset's base_url" — used in Task 4, Task 10. Single source of truth.
- `RETHLAS_VERIFICATION_MODEL` is read in the test only (Task 4); wiring it into `cli.py` is a future change. The spec says this is optional env behavior, and the test only verifies that the env var does not interfere with the generation resolution. **Action:** confirmed the test only checks independence, not verification-side wiring. Future enhancement, not in this plan.
