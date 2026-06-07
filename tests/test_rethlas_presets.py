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
