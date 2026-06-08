# Rethlas

Rethlas is a natural-language reasoning system for mathematics. It has two main agents:

- the generation agent, which reads a markdown problem and writes a proof blueprint
- the verification agent, which checks a proof blueprint and writes a structured verdict

The default runtime is still Codex CLI. The repository also includes a runtime layer for LiteLLM-backed OpenAI/Anthropic models and deterministic mock models for local tests.

## Quick Start

Clone the repository:

```bash
git clone https://github.com/frenzymath/Rethlas.git
cd Rethlas
```

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Optional: create a local environment file for API keys:

```bash
cp .env.example .env
# then fill in the API keys you want to use
```

Rethlas automatically loads `.env` from the repo root when the CLI starts. Existing
shell env variables win (so explicit `export FOO=bar` overrides `.env`).
You can also `source .env` manually if you want the values in your shell too.

For Windows PowerShell users who prefer manual setup, the equivalent is:

```powershell
$env:OPENAI_API_KEY = "..."
$env:ANTHROPIC_API_KEY = "..."
```

For the default Codex model profile, install Codex CLI:

```bash
npm install -g @openai/codex
```

Check the installation:

```bash
python -m rethlas.cli doctor --tools --verbose
```

On Windows, you can also double-click:

```text
rethlas.bat
```

Or use the PowerShell wrapper:

```powershell
.\rethlas.ps1 doctor
```

On Linux/macOS:

```bash
chmod +x ./rethlas.sh
./rethlas.sh
```

## Running A Problem

Problems live under `agents/generation/data/`.

Examples:

```text
agents/generation/data/example.md
agents/generation/data/ns/ns.md
agents/generation/data/modrep/modrep.md
```

Dry-run a problem before starting a long run:

```bash
python -m rethlas.cli run ns/ns --dry-run
```

Run it:

```bash
python -m rethlas.cli run ns/ns
```

Windows wrapper equivalent:

```powershell
.\rethlas.ps1 run ns/ns --dry-run
.\rethlas.ps1 run ns/ns
```

Problem names are normalized automatically. These are equivalent:

```bash
python -m rethlas.cli run ns/ns
python -m rethlas.cli run ns/ns.md
python -m rethlas.cli run data/ns/ns.md
```

Outputs are written under:

```text
agents/generation/logs/{problem_id}/
agents/generation/memory/{problem_id}/
agents/generation/results/{problem_id}/
```

Check status:

```bash
python -m rethlas.cli status ns/ns
```

## Starting The Verification Service

The generation agent calls the verification agent over HTTP.

Start it from the repository root:

```bash
python -m rethlas.cli verify-server
```

Windows wrapper:

```powershell
.\rethlas.ps1 verify-server
```

Dry-run the server command:

```bash
python -m rethlas.cli verify-server --dry-run
```

The default verification URL is configured in `rethlas.toml`:

```toml
[verification]
host = "127.0.0.1"
port = 8091
```

## Custom Model Configuration

Rethlas reads model configuration from two places:

- `rethlas.toml`: holds `[runtime]`, `[providers.*]`, and a small set of toml profiles (`gpt-5.5`, `codex-fast`, `codex-deep`, and 4 `mock-*`).
- `.env`: holds API keys, base URLs, and **the real model name** for each of the 14 built-in env presets.

The default runtime is still Codex CLI. To switch to a cloud vendor, fill in `<VENDOR>_API_KEY` and `<VENDOR>_MODEL` in `.env` and set `RETHLAS_MODEL=<preset>` (or pass `--model <preset>`).

### How preset resolution works

`BUILTIN_PRESETS` (in `rethlas/presets.py`) carries **vendor metadata only** — `base_url`, `compat` (openai-compatible vs anthropic-compatible), and which env var holds the API key. It does **not** carry a "default model name". The real model name is supplied by you in `.env` via `<VENDOR>_MODEL` (e.g. `DEEPSEEK_MODEL=deepseek-chat`).

This means when a vendor ships a new model, you change one line in `.env` — Rethlas does not need a release. `.env.example` lists example model names per vendor; verify against the vendor's own docs for the latest options.

### Built-in env presets

| Preset name   | Vendor                  | Required env var       | Model env var (you set) |
|---------------|-------------------------|------------------------|-------------------------|
| `deepseek`    | DeepSeek                | `DEEPSEEK_API_KEY`     | `DEEPSEEK_MODEL`        |
| `openai`      | OpenAI                  | `OPENAI_API_KEY`       | `OPENAI_MODEL`          |
| `claude`      | Anthropic               | `ANTHROPIC_API_KEY`    | `CLAUDE_MODEL`          |
| `gemini`      | Google Gemini           | `GOOGLE_API_KEY`       | `GEMINI_MODEL`          |
| `qwen`        | 通义千问 (DashScope)    | `QWEN_API_KEY`         | `QWEN_MODEL`            |
| `kimi`        | Moonshot Kimi           | `KIMI_API_KEY`         | `KIMI_MODEL`            |
| `openrouter`  | OpenRouter              | `OPENROUTER_API_KEY`   | `OPENROUTER_MODEL`      |
| `ollama`      | Ollama (local)          | `OLLAMA_API_KEY` (可空) | `OLLAMA_MODEL`         |
| `glm`         | 智谱 GLM                 | `GLM_API_KEY`          | `GLM_MODEL`             |
| `MiniMax`     | MiniMax                 | `MINIMAX_API_KEY`     | `MINIMAX_MODEL`        |
| `siliconflow` | 硅基流动 (SiliconFlow)  | `SILICONFLOW_API_KEY`  | `SILICONFLOW_MODEL`     |
| `doubao`      | 豆包 (火山方舟)         | `DOUBAO_API_KEY`       | `DOUBAO_MODEL`          |
| `mimo`        | 小米 MiMo               | `MIMO_API_KEY`         | `MIMO_MODEL`            |
| `custom`      | 任意未列出厂商 (自填)   | `CUSTOM_API_BASE` + `CUSTOM_COMPAT` (`CUSTOM_API_KEY` 可选) | `CUSTOM_MODEL` |

Each preset also has one optional env var for proxy / self-hosted endpoints:

- `<VENDOR>_API_BASE`: override the default `base_url`.

### Use a preset

Set the key **and** the model name in `.env`, then run:

```bash
export DEEPSEEK_API_KEY="sk-..."
export DEEPSEEK_MODEL="deepseek-chat"
export RETHLAS_MODEL="deepseek"
python -m rethlas.cli run ns/ns
```

PowerShell equivalent:

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
$env:DEEPSEEK_MODEL = "deepseek-chat"
$env:RETHLAS_MODEL = "deepseek"
python -m rethlas.cli run ns/ns
```

Inspect the resolved plan before a long run:

```bash
python -m rethlas.cli plan --role generation --problem ns/ns --model deepseek
python -m rethlas.cli plan --role verification --model deepseek
```

### Pick a different model

Change `<VENDOR>_MODEL` in `.env`:

```bash
DEEPSEEK_MODEL=deepseek-reasoner
```

This is the only place you need to edit when a vendor releases a new model — no Python change, no Rethlas release. `.env.example` gives examples and variable names; the vendor's own docs remain the source of truth for available model IDs.

### Custom (任意未列出厂商)

```bash
CUSTOM_API_BASE=https://my-proxy.example.com/v1
CUSTOM_COMPAT=openai
CUSTOM_MODEL=llama-3.3-70b
CUSTOM_API_KEY=sk-...  # optional if the endpoint does not require auth
RETHLAS_MODEL=custom
```

`CUSTOM_API_BASE`, `CUSTOM_COMPAT`, and `CUSTOM_MODEL` are required. `CUSTOM_API_KEY` is optional so local proxies without auth can work.

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
| `<VENDOR>_API_KEY` | API key for the vendor (e.g. `DEEPSEEK_API_KEY`). **Required** to use the preset. |
| `<VENDOR>_API_BASE` | Override the default `base_url`. **Optional**; for proxies or self-hosted endpoints. |
| `<VENDOR>_MODEL` | Real model name (e.g. `DEEPSEEK_MODEL=deepseek-chat`). **Required** to use the preset — no hardcoded defaults. |
| `RETHLAS_MODEL` | Selects the active preset. Overridden by `--model` on the CLI. |
| `RETHLAS_VERIFICATION_MODEL` | Selects the preset for the verification agent (defaults to `RETHLAS_MODEL`). |
| `CODEX_BIN` | Codex CLI binary name (defaults to `codex`). |

`.env.example` lists every supported variable with a reference card (vendor home, API docs, key signup, and example model names).

### Mock Models

Mock profiles are independent of env presets and useful for local wiring / CI:

```bash
python -m rethlas.cli run example --model mock-generation
python -m rethlas.cli plan --role verification --model mock-verification-correct
pytest -q tests/test_rethlas_runtime.py
```

## Runtime Behavior

Implemented runtime kinds:

- `codex-cli`: runs `codex exec`; this remains the most complete path
- `litellm`: calls OpenAI/Anthropic-style models through LiteLLM
- `mock`: deterministic local backend for tests

Current behavior:

- Codex generation keeps the original full agent behavior.
- Verification API now uses the shared runtime layer.
- LiteLLM verification extracts model JSON, validates it, and writes `verification.json`.
- LiteLLM generation has a native tool-call loop, writes `blueprint.md`, and performs one verifier pass when the verification service is reachable.
- Native OpenAI/Anthropic provider kinds are placeholders for future direct API implementations; use `provider = "litellm"` today.

## References

To attach local references to a problem, create a sibling `.refs` directory:

```text
agents/generation/data/modrep/modrep.md
agents/generation/data/modrep/modrep.refs/
```

Supported reference files include `.md`, `.tex`, `.txt`, and `.pdf`.
PDF references are extracted to `.extracted/` when `pdftotext` is installed.

## Useful Commands

```bash
python -m rethlas.cli doctor --tools --verbose
python -m rethlas.cli setup --dry-run
python -m rethlas.cli verify-server --dry-run
python -m rethlas.cli run example --dry-run
python -m rethlas.cli run example
python -m rethlas.cli status example
python -m rethlas.cli subagent-check
```

Legacy generation scripts still work, but they delegate to the root CLI:

```bash
agents/generation/tests/run_example.sh
```

```powershell
.\agents\generation\tests\run_example.ps1 -ProblemFile ns/ns -DryRun
```

## Viewing Results In A Browser

Rethlas includes a no-dependency local results viewer. It reads
`agents/generation/results/**/blueprint_verified.md` first, falling back to
`blueprint.md`, and serves the generated pages at `http://127.0.0.1:3264`.

```bash
python -m rethlas.cli results-site --open
```

PowerShell wrapper:

```powershell
.\rethlas.ps1 results-site --open
```

You can also build the static HTML without starting a server:

```bash
python -m rethlas.cli results-site --sync-only
```

Then open:

```text
agents/generation/viewer/index.html
```

The older Zola site is still available under `agents/generation/site`. If Zola
is installed, from `agents/generation/` you can run:

```bash
./site/serve.sh
```

Open:

```text
http://localhost:3264
```

## Repository Layout

```text
agents/generation/       generation agent instructions, tools, data, logs, memory, results
agents/verification/     verification agent API, tools, schemas, results
rethlas/                 shared CLI/runtime/problem/status/tool helpers
rethlas.toml             provider and model profile configuration
rethlas.bat              Windows double-click launcher
rethlas.ps1              Windows scriptable wrapper
rethlas.sh               Linux/macOS menu launcher
tests/                   runtime regression tests
docs/legacy/             older analysis documents
```
