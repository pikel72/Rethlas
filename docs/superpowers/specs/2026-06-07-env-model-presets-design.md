# Rethlas `.env` 多模型预设设计

## 目标

把"配置模型"这件事从**仓库级（`rethlas.toml` 的 `[models.*]`）**移到**用户级（`.env`）**，并且把"加新厂商"这件事从**改两段 toml（`[providers.*]` + `[models.*]`）**变成**填一个 key**。

参考 `X:\Code\arxiv_paper_tracker\src\config.py` 的设计：项目内置一份厂商预设表（base_url + 兼容格式由项目给出），用户在 `.env` 只填 `<VENDOR>_API_KEY`，需要换地址才填 `<VENDOR>_API_BASE`。

## 当前状态（不满意的地方）

`529d806 Add model presets and env example` 在 `rethlas.toml` 写死了 10 个 `[models.*]` profile：

- `codex-fast` / `codex-deep` / `openai-default` / `openai-fast` / `openai-deep` / `anthropic-default` / `anthropic-fast` / `anthropic-deep` / `openai-native-default` / `anthropic-native-default`

问题：

1. **每个预设都是一整块 toml 段**（provider / model / api_key_env / reasoning_effort / max_tokens / temperature），想加一个新厂商或新预设就得改 toml。
2. **覆盖厂商少**，只有 OpenAI 和 Anthropic 两家。
3. **预设是仓库级**（提交到 git），不适合放"我自己用的 key/地址"这类信息。
4. `.env` 只放 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`，无法承载"我有 5 个厂商的 key"这种事实。

## 目标设计

### 1) 概念

- **预设（preset）** = 名字 + 厂商 + 真实 model name + base_url + 兼容格式 + key_env。预设**默认由项目代码内置**，用户**通常不需要新增**预设。
- **选择用 `--model X` 或 `RETHLAS_MODEL=X`**：X 命中某个内置预设 → 走 LiteLLM；X 命中 `rethlas.toml` 里剩下的 `[models.*]`（codex / mock-*）→ 走原本的路径。
- **真实 model name 可以覆盖**：每个内置预设提供一个 `default_model`，用户可用 `<PRESET>_MODEL` env 在 `.env` 里覆盖（例如 `DEEPSEEK_1_MODEL=deepseek-reasoner`）。
- **base_url 可以覆盖**：用户可用 `<KEY>_API_BASE` env 覆盖该预设的默认 base_url（适合走代理 / 自部署）。
- **Codex CLI 仍走 toml**：它是本地二进制 + OAuth 的特殊路径，不属于 env 预设体系。`codex` 和 `gpt-5.5` 这两个名字在 toml 的 `[models.*]` 里继续生效。

### 2) 内置预设清单（写进 `rethlas/presets.py` 的 `BUILTIN_PRESETS`）

每个预设字段：

| 字段 | 含义 | 示例 |
|---|---|---|
| `display_name` | 给人看的厂商名 | `"DeepSeek"` |
| `base_url` | 默认 endpoint | `"https://api.deepseek.com/v1"` |
| `compat` | 协议格式，决定 provider 走向 | `"openai"` 或 `"anthropic"` |
| `key_env` | 从哪个 env var 读 key | `"DEEPSEEK_API_KEY"` |
| `default_model` | 真实 LiteLLM model name | `"deepseek-chat"` |
| `model_env_override` | 哪个 env var 可以覆盖 `default_model` | `"DEEPSEEK_1_MODEL"` |
| `key_optional` | key 可空（本地/匿名） | 仅 `ollama` 为 `True` |

预设清单（14 个，命名和 arxiv_paper_tracker 的 `PROVIDER_CONFIG` 一致 + 几个本项目需要的中文厂商）：

| 预设名 | 厂商 | 默认 base_url | compat | key_env | default_model | model_env_override |
|---|---|---|---|---|---|---|
| `deepseek-1` | DeepSeek | `https://api.deepseek.com/v1` | openai | `DEEPSEEK_API_KEY` | `deepseek-chat` | `DEEPSEEK_1_MODEL` |
| `openai` | OpenAI | `https://api.openai.com/v1` | openai | `OPENAI_API_KEY` | `gpt-5` | `OPENAI_MODEL` |
| `claude` | Anthropic | `https://api.anthropic.com/v1` | anthropic | `ANTHROPIC_API_KEY` | `claude-opus-4-5` | `CLAUDE_MODEL` |
| `gemini` | Google | `https://generativelanguage.googleapis.com/v1beta/openai` | openai | `GOOGLE_API_KEY` | `gemini-2.5-pro` | `GEMINI_MODEL` |
| `qwen` | 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | openai | `QWEN_API_KEY` | `qwen-plus` | `QWEN_MODEL` |
| `kimi` | Moonshot | `https://api.moonshot.cn/v1` | openai | `KIMI_API_KEY` | `kimi-k2-0711-preview` | `KIMI_MODEL` |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | openai | `OPENROUTER_API_KEY` | `openai/gpt-4o` | `OPENROUTER_MODEL` |
| `ollama` | Ollama | `http://localhost:11434/v1` | openai | `OLLAMA_API_KEY` | `llama3.1` | `OLLAMA_MODEL` |
| `glm` | 智谱 AI | `https://open.bigmodel.cn/api/paas/v4/` | openai | `GLM_API_KEY` | `glm-4.5` | `GLM_MODEL` |
| `MiniMax` | MiniMax | `https://api.MiniMax.io/v1` | openai | `MiniMax_API_KEY` | `MiniMax-M3` | `MiniMax_MODEL` |
| `siliconflow` | 硅基流动 | `https://api.siliconflow.cn/v1` | openai | `SILICONFLOW_API_KEY` | `Qwen/Qwen2.5-72B-Instruct` | `SILICONFLOW_MODEL` |
| `doubao` | 豆包（火山方舟） | `https://ark.cn-beijing.volces.com/api/v3` | openai | `DOUBAO_API_KEY` | `doubao-seed-1-6-250615` | `DOUBAO_MODEL` |
| `mimo` | 小米 MiMo | `https://api.xiaomi.com/v1` | openai | `MIMO_API_KEY` | `mimo-7b` | `MIMO_MODEL` |
| `custom` | 用户自填 | 必填（无默认） | openai / anthropic | `CUSTOM_API_KEY` | 必填 | `CUSTOM_MODEL` |

说明：

- `ollama` 的 `key_optional=True`：本地 Ollama 不需要 key，env 留空也能用。
- `custom` 的 `base_url` / `default_model` / `compat` 全部必填，缺一就抛清晰错误。
- 所有 base_url 都可通过对应 env 覆盖（例如 `DEEPSEEK_API_BASE=https://proxy.example.com/v1`），这适用于所有预设包括 `custom` 的 `CUSTOM_API_BASE`。
- 不在表里的厂商目前没有预设入口 —— 用户要么选 `custom` 走 LiteLLM，要么自己用现有 `providers.openai` / `providers.anthropic` toml 段（保留原状）。

### 3) `.env` 形态

`.env.example`（仓库提交）按以下结构组织：

```bash
# ===== AI 模型预设（详见 docs/superpowers/specs/2026-06-07-env-model-presets-design.md）=====
# 行为：在 .env 里设 <VENDOR>_API_KEY 就启用对应预设。
# 需要换地址（代理/自部署）才填 <VENDOR>_API_BASE。
# 想用其他真实 model 名字（比如把 deepseek-1 切到 reasoner），填 <PRESET>_MODEL。

# --- DeepSeek ---
# 可用预设: deepseek-1
# 常见模型: deepseek-chat, deepseek-reasoner
DEEPSEEK_API_KEY=
DEEPSEEK_API_BASE=
DEEPSEEK_1_MODEL=

# --- OpenAI ---
# 可用预设: openai
# 常见模型: gpt-5, gpt-5-mini, gpt-4.1
OPENAI_API_KEY=
OPENAI_API_BASE=
OPENAI_MODEL=

# --- Anthropic ---
# 可用预设: claude
# 常见模型: claude-opus-4-5, claude-sonnet-4-5
ANTHROPIC_API_KEY=
ANTHROPIC_API_BASE=
CLAUDE_MODEL=

# --- Google Gemini ---
# 可用预设: gemini
# 常见模型: gemini-2.5-pro, gemini-2.5-flash
GOOGLE_API_KEY=
GEMINI_MODEL=

# --- 通义千问 (DashScope) ---
# 可用预设: qwen
# 常见模型: qwen-plus, qwen-turbo, qwen-max
QWEN_API_KEY=
QWEN_MODEL=

# --- Moonshot Kimi ---
# 可用预设: kimi
# 常见模型: kimi-k2-0711-preview, moonshot-v1-128k
KIMI_API_KEY=
KIMI_API_BASE=
KIMI_MODEL=

# --- OpenRouter ---
# 可用预设: openrouter
# 常见模型: 任意 OpenAI/Anthropic/Google/... 都行
OPENROUTER_API_KEY=
OPENROUTER_MODEL=

# --- Ollama (本地) ---
# 可用预设: ollama（本地服务，无 key 也能跑）
# 常见模型: llama3.1, qwen2.5-coder:32b
OLLAMA_API_KEY=
OLLAMA_MODEL=

# --- 智谱 GLM ---
# 可用预设: glm
# 常见模型: glm-4.5, glm-4.5-air
GLM_API_KEY=
GLM_MODEL=

# --- MiniMax ---
# 可用预设: MiniMax
# 常见模型: MiniMax-M3, MiniMax-text-01
MiniMax_API_KEY=
MiniMax_API_BASE=
MiniMax_MODEL=

# --- 硅基流动 (SiliconFlow) ---
# 可用预设: siliconflow
# 常见模型: Qwen/Qwen2.5-72B-Instruct, deepseek-ai/DeepSeek-V3
SILICONFLOW_API_KEY=
SILICONFLOW_MODEL=

# --- 豆包 (火山方舟) ---
# 可用预设: doubao
# 常见模型: doubao-seed-1-6-250615, doubao-1-5-pro-32k
DOUBAO_API_KEY=
DOUBAO_API_BASE=
DOUBAO_MODEL=

# --- 小米 MiMo ---
# 可用预设: mimo
# 常见模型: mimo-7b
MIMO_API_KEY=
MIMO_API_BASE=
MIMO_MODEL=

# --- Custom (任意未列出的厂商) ---
# 必填三项：CUSTOM_API_KEY, CUSTOM_API_BASE, CUSTOM_COMPAT (openai|anthropic)
# 可选：CUSTOM_MODEL（真实 model name），不填则用预设名本身
CUSTOM_API_KEY=
CUSTOM_API_BASE=
CUSTOM_COMPAT=
CUSTOM_MODEL=

# ===== 当前选用的预设（不设则用 rethlas.toml 的 [runtime].default_model）=====
RETHLAS_MODEL=

# ===== 可选：verification agent 单独指定（不设则与 RETHLAS_MODEL 相同）=====
RETHLAS_VERIFICATION_MODEL=
```

### 4) 解析流程

#### 4.1 `ModelConfig` 扩展

新增两个字段（与现有 `api_key_env` 平行）：

```python
@dataclass(frozen=True)
class ModelConfig:
    name: str
    provider: str
    model: str
    api_base: Optional[str] = None          # 新增：env 预设透传 base_url（覆盖 provider.base_url）
    compat: Optional[str] = None            # 新增："openai" 或 "anthropic"，决定 LiteLLM 路由
    # ... 其余字段保持不变
```

`api_base` / `compat` 都对 toml 段是可选 —— toml 段不写就是 `None`，runtime 层回退到 `request.provider.base_url` 和 LiteLLM 默认路由。

#### 4.2 `resolve_model(name)` 行为变更



```text
输入 name（None → 走 RETHLAS_MODEL env → 走 [runtime].default_model）
  1. if name 命中 toml 里剩下的 [models.*]（codex/*, mock-*） → 原样返回
  2. if name == "codex" → 返回 toml 里 [models."gpt-5.5"] 的 ModelConfig
  3. if name 命中 BUILTIN_PRESETS：
        preset = BUILTIN_PRESETS[name]
        model_name = os.getenv(preset.model_env_override) or preset.default_model
        api_base   = os.getenv(preset.key_env + "_BASE") or preset.base_url
        api_key    = os.getenv(preset.key_env)
        if not preset.key_optional and not api_key:
            raise ValueError(f"Preset {name!r} requires {preset.key_env} ...")
        if name == "custom":
            校验 CUSTOM_API_BASE / CUSTOM_COMPAT 必填
        return ModelConfig(
            name=name,
            provider="litellm",
            model=model_name,
            api_key_env=preset.key_env,
            api_base=api_base,
            compat=preset.compat,
        )
  4. 抛 ValueError，列出全部可用预设名（含 toml 剩下的和内置表）
```

实现上：

- `provider="litellm"` 已存在；`compat` 决定走 OpenAI 兼容还是 Anthropic 兼容。
- `compat="openai"` 时 LiteLLM 用 `custom_llm_provider="openai"`，`model` 字段直接用真实 model name（如 `gpt-5`、`deepseek-chat`）。
- `compat="anthropic"` 时 LiteLLM 用 `custom_llm_provider="anthropic"`，`model` 字段直接用真实 model name（如 `claude-opus-4-5`），Anthropic SDK 通过 headers 区分。
- `runtime.py` 的现有 `api_base_url` 解析改为：先看 `request.model.api_base`，再看 `request.provider.base_url`，再无就 None（让 LiteLLM 用 SDK 默认）。

### 5) `rethlas.toml` 处置

**删除**以下 8 段（`529d806` 加进去的 OpenAI / Anthropic 系列 —— 现在由 env 预设 `openai` / `claude` 等替代）：

```text
[models.openai-default]
[models.openai-fast]
[models.openai-deep]
[models.anthropic-default]
[models.anthropic-fast]
[models.anthropic-deep]
[models.openai-native-default]
[models.anthropic-native-default]
```

**保留**（结构 + codex 全套 + mock）：

- `[runtime]` （default_model 默认值仍为 `"gpt-5.5"`，codex 是默认 runtime）
- `[agents]`
- `[providers.codex]` / `[providers.litellm]` / `[providers.mock]` / `[providers.openai]` / `[providers.anthropic]`
- `[models."gpt-5.5"]`（codex 默认 profile，reasoning_effort=xhigh）
- `[models.codex-fast]`（codex + reasoning_effort=medium，保留作为 codex 变体快捷名）
- `[models.codex-deep]`（codex + reasoning_effort=xhigh，保留作为 codex 变体快捷名）
- `[models.mock-generation]` / `[models.mock-verification-correct]` / `[models.mock-verification-wrong]` / `[models.mock-verification-malformed]`
- `[verification]` / `[paths]`

注：mock profile 保留是因为它不依赖 env / 网络，是本地 deterministic 后端，CI / 单元测试用。`codex-fast` / `codex-deep` 保留是因为它们只改 `reasoning_effort`，走 env 预设体系反而要新增"codex reasoning_effort override"机制，得不偿失；保留在 toml 是最小改动。

### 6) 错误信息

| 场景 | 错误格式 |
|---|---|
| 选 `deepseek-1` 但 `DEEPSEEK_API_KEY` 为空 | `Preset 'deepseek-1' is enabled but DEEPSEEK_API_KEY is not set. Set it in .env or your shell.` |
| 选 `custom` 但 `CUSTOM_API_BASE` 为空 | `Preset 'custom' requires CUSTOM_API_BASE to be set.` |
| 选 `custom` 但 `CUSTOM_COMPAT` 不是 `openai`/`anthropic` | `Preset 'custom' requires CUSTOM_COMPAT=openai or CUSTOM_COMPAT=anthropic (got '').` |
| 选未知名 | `Unknown model 'X'. Available: codex, gpt-5.5, deepseek-1, openai, claude, ...` |

`doctor --tools --verbose` 增强：扫描已 set 的 key，对每个内置预设报告"是否可用 / 缺哪个 key"。

### 7) 单元测试（`tests/test_rethlas_presets.py` 新增）

注意：env 预设**不在** `config.models` dict 里（避免和 toml 段名字冲突），测试统一通过 `config.resolve_model("X")` 拿到 `ModelConfig` 再断言字段。

```text
test_builtin_preset_resolves_with_key_and_default_model
  设 DEEPSEEK_API_KEY=sk-x, 不设 DEEPSEEK_1_MODEL
  → m = config.resolve_model("deepseek-1")
  → m.model == "deepseek-chat", m.provider == "litellm", m.api_key_env == "DEEPSEEK_API_KEY"

test_builtin_preset_model_env_override
  设 DEEPSEEK_API_KEY=sk-x, DEEPSEEK_1_MODEL=deepseek-reasoner
  → m = config.resolve_model("deepseek-1")
  → m.model == "deepseek-reasoner"

test_builtin_preset_base_url_env_override
  设 DEEPSEEK_API_KEY=sk-x, DEEPSEEK_API_BASE=https://proxy.example.com/v1
  → m = config.resolve_model("deepseek-1")
  → m.api_base == "https://proxy.example.com/v1"

test_builtin_preset_carries_default_api_base
  设 DEEPSEEK_API_KEY=sk-x（不设 DEEPSEEK_API_BASE）
  → m = config.resolve_model("deepseek-1")
  → m.api_base == "https://api.deepseek.com/v1"（用预设默认值）

test_missing_api_key_raises_friendly_error
  不设 DEEPSEEK_API_KEY
  → config.resolve_model("deepseek-1") 抛 ValueError，错误信息含 "DEEPSEEK_API_KEY"

test_ollama_key_optional
  不设 OLLAMA_API_KEY
  → m = config.resolve_model("ollama") 成功
  → m.api_key_env == "OLLAMA_API_KEY"（字段存在但值允许为空）

test_custom_requires_base_and_compat
  设 CUSTOM_API_KEY=sk-x, 不设 CUSTOM_API_BASE / CUSTOM_COMPAT
  → config.resolve_model("custom") 抛 ValueError
  → 错误信息同时含 "CUSTOM_API_BASE" 和 "CUSTOM_COMPAT"

test_custom_compat_anthropic_routes_correctly
  设 CUSTOM_API_KEY=sk-x, CUSTOM_API_BASE=https://example.com/v1, CUSTOM_COMPAT=anthropic
  → m = config.resolve_model("custom")
  → m.compat == "anthropic", m.api_base == "https://example.com/v1"

test_custom_model_env_override
  设 CUSTOM_API_KEY=sk-x, CUSTOM_API_BASE=https://example.com/v1, CUSTOM_COMPAT=openai, CUSTOM_MODEL=llama-3.3-70b
  → m = config.resolve_model("custom")
  → m.model == "llama-3.3-70b"

test_legacy_toml_models_removed
  config.models keys 不含 "openai-default" / "anthropic-default"
  config.models keys 含 "gpt-5.5" / "codex-fast" / "codex-deep" / "mock-generation" / "mock-verification-correct"

test_codex_still_works
  m = config.resolve_model("codex")
  → m.provider == "codex", m.name == "gpt-5.5"（仍走 toml 的 [models."gpt-5.5"]）

test_unified_resolve_model_lists_all_presets
  config.resolve_model("does-not-exist") 抛 ValueError
  → 错误信息同时含 "codex", "gpt-5.5", "mock-generation", "deepseek-1", "openai", "claude", "custom"

test_verify_model_env_independent_from_generation
  设 RETHLAS_MODEL=deepseek-1, RETHLAS_VERIFICATION_MODEL=claude, DEEPSEEK_API_KEY=sk-d, ANTHROPIC_API_KEY=sk-a
  → gen = config.resolve_model()           # 走 RETHLAS_MODEL
  → ver = config.resolve_model(os.getenv("RETHLAS_VERIFICATION_MODEL"))
  → gen.model == "deepseek-chat", ver.model == "claude-opus-4-5"
```

更新 `tests/test_rethlas_runtime.py::test_runtime_config_has_multi_model_profiles` 里的断言：`"openai-default"` / `"anthropic-default"` 改为 `"openai"` / `"claude"`。

### 8) 文档

- `README.md` 的 "Custom Model Configuration" 段重写：
  - 删掉"在 `[models.*]` 加一段"的示例。
  - 改为"在 `.env` 设 `DEEPSEEK_API_KEY=...` 即可用 `deepseek-1`"，并列出全部 14 个内置预设和对应的 env var。
  - 保留 "How to add your own model" 段，但内容改成："项目不支持自定义新增预设（请提 issue 或用 `custom` 槽位）。"
- `README.md` 的 "Environment Variables" 段更新变量列表。
- `.env.example` 整个重写为 §3 形态。

### 9) 范围外（明确不做）

- 不引入"运行时从 `.env` 实时热加载并 reload" —— 仍是一次性 startup 读取。
- 不动 mock profile（保留 4 个）。
- 不动 `[runtime].default_model = "gpt-5.5"`（codex 仍是默认）。
- 不改 `verify-server` / `agent_loop` / `cli.py` 之外的运行时逻辑；本 spec 只动 `config.py`（`resolve_model` 路径）+ 新增 `presets.py` + 删 toml 段 + 更新 README/.env.example/测试。
- 不重命名既有 `mock-*` profile。
- 不做"按 env 变量自动 enumerate 出全部已启用预设"的命令（`doctor` 里加一个打印即可，但不开成新命令）。

## 实现顺序（high-level）

1. 新建 `rethlas/presets.py`，定义 `BUILTIN_PRESETS`（14 条）。
2. 改 `rethlas/config.py`：
   - 引入 `presets`，加 `_resolve_env_preset(name)` 辅助函数（返回 `ModelConfig` 或抛 `ValueError`）。
   - `RethlasConfig.resolve_model` 改为：先查 `self.models`（toml 段，含 `gpt-5.5` 和 4 个 mock），再走 `BUILTIN_PRESETS` 解析为 `ModelConfig`，再抛错。
   - `RethlasConfig.models` 字段语义**不变** —— 仍只含 toml 段。env 预设不在 `config.models` dict 里，只在 `resolve_model` 时按需构造（避免 toml 段和 env 预设同名时谁覆盖谁的歧义）。
3. 改 `rethlas/runtime.py`：`_api_key_env` / `_api_base` 解析处如需要，从 `model.extra` 取 `api_base` / `compat`。
4. 删 `rethlas.toml` 里的 8 段（OpenAI / Anthropic 系），保留 `codex-fast` / `codex-deep` / `gpt-5.5` / 4 个 mock。
5. 写 `.env.example`（§3 形态）。
6. 写 `tests/test_rethlas_presets.py`（§7 列表）。
7. 更新 `tests/test_rethlas_runtime.py` 里的相关断言。
8. 更新 `README.md` "Custom Model Configuration" + "Environment Variables" 两段。
9. 跑 `pytest -q` 全绿。
10. 跑 `python -m rethlas.cli doctor --tools --verbose`，确认能正确报告每个内置预设的 key 状态。

## 风险与回滚

- `resolve_model` 行为变化是最大风险点：测试要覆盖 toml-only 路径、env-only 路径和两者都没匹配三种情况。
- 如果某个预设的 base_url 默认值写错（特别是 `MiniMax` / `mimo` 这种本项目没集成过的厂商），用户在 `.env` 设 `*_API_BASE` 即可覆盖，**不需要改代码**。
- 删 toml 段是破坏性变更（`[models.openai-default]` 等名字消失）。但因为这些名字在 6 月 7 日这一天才合并（commit `529d806`），还没有 release，回滚成本低。`codex-fast` / `codex-deep` 仍保留在 toml，所以"--model codex-fast"和今天一样工作。

---

## 修订记录

### 2026-06-07 (同日): 移除 `default_model` 字段，重命名 `deepseek-1` → `deepseek`

**用户反馈**：厂商下面的模型名（如 `deepseek-chat`）写死在 `BUILTIN_PRESETS` 里是糟糕的设计 —— 厂商一改旗舰模型用户就只能等 Python 源码更新。我用 `qwen3.7max`，等他更新了我上哪去改？

**变更**：

1. **`PresetSpec.default_model` 字段删除**。`BUILTIN_PRESETS` 不再携带任何"默认模型名"信息，只携带厂商元数据（`base_url` / `compat` / `key_env` / `model_env_override` / `key_optional` / `base_url_env_override`）。
2. **真实模型名 100% 由 `.env` 驱动**。`_resolve_env_preset` 检查 `os.getenv(preset.model_env_override)`，**未设置则抛清晰错误**：
   ```
   Preset 'deepseek' requires DEEPSEEK_MODEL to be set in .env.
   See .env.example for the current recommended model names.
   ```
3. **`.env.example` 是真实模型名的唯一权威来源**。每个预设下挂完整参考卡：官网、文档、key 申请地址、当前推荐模型清单（带 reasoning / context window 等备注）、覆盖方式。`BUILTIN_PRESETS` 永不携带这些 —— 它们是"推荐"而不是"默认"。
4. **`deepseek-1` → `deepseek`**，对应 `model_env_override` 由 `DEEPSEEK_1_MODEL` 改为 `DEEPSEEK_MODEL`。其他 13 个预设命名不变（都是纯厂商名）。
5. **`default_model` 在 README 预设表中也删除**该列；改为指向 `.env.example`。

**影响范围**：

- `rethlas/presets.py` —— 删 `default_model` 字段；`BUILTIN_PRESETS` 14 条全部去掉该字段；`deepseek-1` 重命名。
- `rethlas/config.py` —— `_resolve_env_preset` 检查 `model_env_override` 未设时抛错。
- `tests/test_rethlas_presets.py` —— 删结构性测试 `test_every_preset_has_nonempty_key_env_and_default_model` 里的 `default_model` 断言；所有以前依赖"默认模型名"的测试改为显式 `setenv(<PRESET>_MODEL, ...)`。
- `.env.example` —— 重写为完整参考卡形态。
- `README.md` —— 预设表去掉"Default model"列。
- 本地 `.env` —— 给启用的 7 个预设各加 `<PRESET>_MODEL=...`。

**为什么这样改**：

- 厂商的"默认"是软建议，不该硬编码。用户选什么模型是 ops 决策。
- 厂商更新旗舰模型 → 用户改一行 `.env` 即可，**不需要 Rethlas 发版**。
- 旧设计（写死 `deepseek-chat`）相当于把 Rethlas 的 release cadence 锁到所有上游厂商的旗舰更新节奏上。
- arxiv_paper_tracker 也是这套设计：vendor 在源码里只有元数据，模型名走 env。


