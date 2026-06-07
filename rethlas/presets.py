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
