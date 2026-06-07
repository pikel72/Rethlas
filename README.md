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

Models are configured in `rethlas.toml`.

There are three concepts:

- **provider**: how the model is called, such as `codex-cli`, `litellm`, or `mock`
- **model profile**: a named model configuration used by `--model`
- **default model**: the profile used when `--model` is omitted

The default is:

```toml
[runtime]
default_model = "gpt-5.5"
timeout_seconds = 3600
```

### Codex CLI Model

The default profile uses Codex CLI:

```toml
[providers.codex]
kind = "codex-cli"
command = "codex"

[models."gpt-5.5"]
provider = "codex"
model = "gpt-5.5"
reasoning_effort = "xhigh"
supports_tools = true
supports_streaming = true
```

Use it explicitly:

```bash
python -m rethlas.cli run ns/ns --model gpt-5.5
```

### OpenAI Through LiteLLM

Set your API key:

```bash
export OPENAI_API_KEY="..."
```

PowerShell:

```powershell
$env:OPENAI_API_KEY = "..."
```

Example profile:

```toml
[providers.litellm]
kind = "litellm"

[models.openai-default]
provider = "litellm"
model = "openai/gpt-5.5"
reasoning_effort = "xhigh"
api_key_env = "OPENAI_API_KEY"
supports_tools = true
supports_streaming = true
```

Inspect the plan:

```bash
python -m rethlas.cli plan --role generation --problem ns/ns --model openai-default
python -m rethlas.cli plan --role verification --model openai-default
```

Run generation with that profile:

```bash
python -m rethlas.cli run ns/ns --model openai-default
```

### Anthropic Through LiteLLM

Set your API key:

```bash
export ANTHROPIC_API_KEY="..."
```

PowerShell:

```powershell
$env:ANTHROPIC_API_KEY = "..."
```

Example profile:

```toml
[models.anthropic-default]
provider = "litellm"
model = "anthropic/claude-opus-4-5"
api_key_env = "ANTHROPIC_API_KEY"
supports_tools = true
supports_streaming = true
```

Use it:

```bash
python -m rethlas.cli run ns/ns --model anthropic-default
```

### Add Your Own Model Profile

Add a new table under `[models.<name>]`.

For an OpenAI-compatible LiteLLM model:

```toml
[models.my-openai-model]
provider = "litellm"
model = "openai/gpt-5.5"
api_key_env = "OPENAI_API_KEY"
reasoning_effort = "xhigh"
supports_tools = true
supports_streaming = true
max_tokens = 8000
temperature = 0.2
```

For an Anthropic model:

```toml
[models.my-claude-model]
provider = "litellm"
model = "anthropic/claude-opus-4-5"
api_key_env = "ANTHROPIC_API_KEY"
supports_tools = true
supports_streaming = true
max_tokens = 8000
temperature = 0.2
```

Make it the default:

```toml
[runtime]
default_model = "my-openai-model"
timeout_seconds = 3600
```

Or keep the default unchanged and pass it per run:

```bash
python -m rethlas.cli run ns/ns --model my-openai-model
```

Always check a custom model before running:

```bash
python -m rethlas.cli doctor --verbose
python -m rethlas.cli plan --role generation --problem ns/ns --model my-openai-model
python -m rethlas.cli plan --role verification --model my-openai-model
```

If an API key or package is missing, `plan` prints it before a long run starts.

### Mock Models

Mock profiles require no Codex, LiteLLM, or API key. They are useful for checking local wiring:

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

`agents/generation/site` contains a Zola site for browsing markdown results with LaTeX math.

Install Zola, then from `agents/generation/`:

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
