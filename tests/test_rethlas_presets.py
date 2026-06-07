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
