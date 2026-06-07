# Rethlas entrypoint and runtime plan

## Goal

Make Rethlas easier to run from a normal Windows or Unix shell, while reducing the current hard dependency on Codex CLI and Bash.

This plan focuses on the entrypoint/runtime layer only. Richer intermediate output is related, but should be planned separately after the launch path is stable.

The immediate usability target is click-to-open on Windows: a user should be able to double-click a root-level launcher, choose a small number of actions from a menu, and start or inspect an agent run without remembering internal directories.

## Current state

The current runnable path is:

1. Start the verification agent manually from `agents/verification`.
2. Run the generation agent manually from `agents/generation`.
3. The generation runner invokes `codex exec`.
4. The generation agent calls the verification HTTP service.
5. The verification HTTP service invokes `codex exec` again for verification.

The existing Windows wrappers work, but they are still transitional:

- `agents/verification/start_server.ps1` starts only the verifier and assumes a ready `.venv`.
- `agents/generation/tests/run_example.ps1` is located under `tests/`, even though it is the practical production entrypoint on Windows.
- The user must know which directory to enter, which service to start first, and what to do when dependencies are missing.
- Bash scripts remain the documented first-class path in several places.
- Both generation and verification still call Codex directly, so there is no clean place to swap in another model/runtime backend.

## Design principles

1. Root-level commands should be the primary user interface.
2. PowerShell should be first-class on Windows, not a secondary workaround.
3. Bash should be optional compatibility, not a required path.
4. Backend selection should be explicit and centralized.
5. Existing Codex behavior should remain available while we introduce a more general runner.
6. The generation and verification agents should keep their current working directories and artifact layout unless there is a concrete reason to change them.
7. Setup, health checks, run metadata, logs, and result paths should be visible before a long run starts.

## Target user experience

From the repository root:

```powershell
.\rethlas.ps1 setup
.\rethlas.ps1 verify-server
.\rethlas.ps1 run example
.\rethlas.ps1 run ns/ns
.\rethlas.ps1 status ns/ns
```

Optional Unix equivalent:

```bash
./rethlas setup
./rethlas verify-server
./rethlas run example
./rethlas run ns/ns
./rethlas status ns/ns
```

The key improvement is that users should not need to remember:

- `cd agents/verification`
- `cd agents/generation`
- where `.venv` lives
- whether a problem path needs `data/` or `.md`
- where logs, memory, and results are written
- which environment variable controls the verifier URL

There should also be a click-oriented Windows path:

```text
Double-click rethlas.bat
```

The first version can be a simple menu:

- doctor
- start verification service
- run included example
- run a named problem
- dry-run a named problem

Linux/macOS can use a terminal menu through `./rethlas.sh`. It is less important that this be double-clickable on every desktop environment, but it should offer the same commands and call the same implementation once the shared runner exists.

## Proposed architecture

### 1. Add root-level launchers

Add:

- `rethlas.bat`
- `rethlas.ps1`
- optionally `rethlas` or `rethlas.sh`

The initial `.bat` file can be the click target and menu. The PowerShell launcher should become the more scriptable command interface. Both should support or delegate to these commands:

- `setup`
- `verify-server`
- `run`
- `status`
- `doctor`

The existing scripts under `agents/...` should remain for compatibility, but they should delegate to shared implementation where possible.

### 2. Add a shared Python runner package

Add a small Python package, for example:

```text
rethlas/
  __init__.py
  cli.py
  paths.py
  problems.py
  references.py
  runtime.py
  status.py
```

This package should own cross-platform logic:

- repository root detection
- problem path normalization
- reference directory detection
- PDF-to-text extraction checks
- log/result/memory path calculation
- health checks for the verifier
- backend command construction
- run metadata writing

PowerShell and Bash should become thin shells over this Python CLI. That avoids duplicating path validation and PDF extraction in separate shell scripts.

### 3. Separate runner backend from agent instructions

Introduce a runtime abstraction:

```text
RuntimeBackend
  run_generation(prompt, cwd, log_path, options)
  run_verification(prompt, cwd, log_path, options)
```

Initial backend:

- `codex`: current `codex exec` behavior
- `litellm`: shared OpenAI/Anthropic model-call layer

Future backend candidates:

- `openai-compatible`: direct OpenAI-compatible API runner
- `anthropic-compatible`: direct Anthropic-compatible API runner
- `mock`: deterministic local test backend
- `manual`: write prepared prompt/log metadata without invoking a model

This gives us a clean way to stop hardcoding Codex throughout the codebase without changing the math-agent policy all at once.

LiteLLM should be treated as a model gateway, not as the Rethlas agent framework. Rethlas should keep ownership of memory, verification, tool routing, and artifact writing.

The abstraction should distinguish provider format from model profile:

- provider format: `codex-cli`, `openai-compatible`, `anthropic-compatible`
- model profile: a named configuration such as `gpt-5.5`, `openai-default`, or `anthropic-default`

That lets multiple models share one provider while still carrying provider-specific options such as reasoning effort, base URL, and API key environment variable.

### 4. Centralize configuration

Add one repo-level config file:

```text
rethlas.toml
```

Suggested shape:

```toml
[runtime]
default_model = "gpt-5.5"
timeout_seconds = 3600

[providers.codex]
kind = "codex-cli"
command = "codex"

[providers.openai]
kind = "openai-compatible"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[providers.anthropic]
kind = "anthropic-compatible"
base_url = "https://api.anthropic.com/v1"
api_key_env = "ANTHROPIC_API_KEY"

[models.gpt-5.5]
provider = "codex"
model = "gpt-5.5"
reasoning_effort = "xhigh"

[verification]
host = "127.0.0.1"
port = 8091

[paths]
generation_dir = "agents/generation"
verification_dir = "agents/verification"
```

Environment variables can still override config, but the normal path should be readable from one file.

### 5. Make setup explicit

`.\rethlas.ps1 setup` should:

- create or reuse `agents/generation/.venv`
- install `agents/generation/mcp/requirements.txt`
- create or reuse `agents/verification/.venv`
- install `agents/verification/requirements.txt`
- check for `codex` only if `runtime.backend = "codex"`
- check for `pdftotext` and warn if absent
- print a concise readiness summary

It should not silently require a prior manual venv setup.

### 6. Make `run` orchestration explicit

`.\rethlas.ps1 run ns/ns` should:

1. Normalize the problem id to `data/ns/ns.md`.
2. Detect `data/ns/ns.refs/`.
3. Extract PDFs if possible.
4. Check whether verifier is reachable.
5. Print run metadata:
   - backend
   - model
   - problem id
   - problem file
   - reference dir
   - log path
   - memory path
   - result path
   - verifier URL
6. Invoke the selected runtime backend.
7. Stream output by default while also writing the log.
8. Exit nonzero on runtime failure.

This makes `agents/generation/tests/run_example.ps1` unnecessary as the user-facing entrypoint. It can remain as:

```powershell
..\..\rethlas.ps1 run example
```

or be renamed later.

### 7. Make verification API use the same runtime abstraction

`agents/verification/api/server.py` currently builds a Codex command directly. It should instead call shared runtime code:

```python
from rethlas.runtime import get_backend
```

The API can still run Codex at first, but the hardcoded `CODEX_BIN`, `CODEX_MODEL`, and `build_codex_command` logic should move out of the API file.

This keeps the API responsible for HTTP, request validation, run IDs, result loading, and errors; runtime execution becomes a replaceable dependency.

## Implementation phases

### Phase 1: Mature the Windows/root entrypoint

Deliverables:

- Add or refine `rethlas.bat` as the double-click Windows menu.
- Add `rethlas.ps1`.
- Add or refine `rethlas.sh` as the Unix menu.
- Add `rethlas/cli.py`, `paths.py`, `problems.py`, `references.py`, and `status.py`.
- Move problem normalization and PDF extraction out of `run_example.ps1` into Python.
- Keep Codex as the only backend.
- Update README to make root PowerShell commands the first documented path.

Acceptance checks:

- `.\rethlas.ps1 doctor`
- `.\rethlas.ps1 run example -DryRun`
- `.\rethlas.ps1 run ns/ns -DryRun`
- double-clicking `rethlas.bat` opens a usable menu
- `./rethlas.sh` opens a comparable terminal menu
- existing `agents/generation/tests/run_example.ps1 -DryRun` still works or delegates cleanly

### Phase 2: Centralize runtime config

Deliverables:

- Add `rethlas.toml`.
- Add config loading and environment overrides.
- Remove duplicated model defaults from launch scripts where possible.
- Print effective config in `doctor` and before each run.

Acceptance checks:

- Changing the model in `rethlas.toml` changes dry-run output.
- Environment override still wins for one-off runs.
- Missing backend dependency is reported before a long run starts.

### Phase 3: Runtime backend abstraction

Deliverables:

- Add `rethlas/runtime.py`.
- Implement `CodexBackend`.
- Add `MockBackend` for tests and dry integration checks.
- Refactor `agents/verification/api/server.py` to use the shared runtime layer.

Acceptance checks:

- Verification API dry/unit test can run without Codex through `MockBackend`.
- Existing Codex verification path still writes `verification/results/{run_id}/log.md`.
- API errors mention the selected backend, not only `codex exec`.

### Phase 4: Bash compatibility cleanup

Deliverables:

- Keep `run_example.sh` as a compatibility wrapper.
- Add a root Unix launcher only if needed.
- Remove duplicated Bash-only implementation logic.
- Update docs so Bash is an optional path, not the canonical one.

Acceptance checks:

- Unix launcher and PowerShell launcher call the same Python implementation.
- Problem path behavior is identical across shells.

## Complete remaining roadmap

This section is the execution plan from the current state to a usable multi-model Rethlas. It assumes the current repository already has:

- root menu launchers: `rethlas.bat`, `rethlas.sh`
- runtime config: `rethlas.toml`
- shared package skeleton: `rethlas/config.py`, `rethlas/problems.py`, `rethlas/runtime.py`, `rethlas/cli.py`
- a Codex CLI backend plan path
- a LiteLLM backend skeleton for plain model calls

### Stage 0: Stabilize the current branch

Goal: make the current usability and runtime skeleton changes commit-ready before deeper refactors.

Tasks:

- Stage the doc moves from `docs/analysis-*.md` to `docs/legacy/`.
- Commit the launchers, `rethlas.toml`, `requirements.txt`, and `rethlas/` package together.
- Confirm ignored runtime artifacts remain ignored.
- Keep `codex-cli` as the default model profile.

Acceptance checks:

- `git status --short --ignored` shows only expected ignored runtime artifacts after staging.
- `python -m rethlas.cli doctor`
- `python -m rethlas.cli plan --role generation --problem ns/ns`
- `python -m rethlas.cli plan --role generation --problem ns/ns --model openai-default`
- `python -m rethlas.cli plan --role verification --model anthropic-default --json`
- `powershell -NoProfile -ExecutionPolicy Bypass -File agents/generation/tests/run_example.ps1 -ProblemFile ns/ns -DryRun -NoLiveLog`
- `cmd /c "echo 0|rethlas.bat"`

### Stage 1: Finish root CLI and launcher delegation

Goal: make root commands the only user-facing entrypoint, with old scripts retained as compatibility wrappers.

Tasks:

- Add `rethlas.ps1` as the scriptable Windows command surface.
- Extend `rethlas/cli.py` with real subcommands:
  - `doctor`
  - `setup`
  - `verify-server`
  - `run`
  - `status`
  - `plan`
- Move reference/PDF extraction from `run_example.ps1` and `run_example.sh` into `rethlas/references.py`.
- Add `rethlas/status.py` to summarize logs, memory, result files, verifier health, and last run state.
- Make `rethlas.bat` call `python -m rethlas.cli` or `rethlas.ps1` instead of duplicating logic.
- Make `rethlas.sh` call the same Python CLI.
- Turn `agents/generation/tests/run_example.ps1` and `run_example.sh` into thin compatibility wrappers.

Acceptance checks:

- `.\rethlas.ps1 doctor`
- `.\rethlas.ps1 run ns/ns -DryRun`
- `.\rethlas.ps1 status ns/ns`
- double-clicking `rethlas.bat` opens a menu whose actions call the shared CLI
- `./rethlas.sh` calls the shared CLI on Unix
- compatibility wrappers produce the same normalized problem path as root commands

### Stage 2: Runtime contract and test backend

Goal: make provider execution testable without Codex or API keys.

Tasks:

- Add a stable `RuntimeResult` object:
  - `returncode`
  - `started_at`
  - `ended_at`
  - `log_path`
  - `output_text`
  - `usage`
  - `provider_metadata`
  - `error`
- Make every backend return `RuntimeResult` instead of bare exit codes.
- Add `MockBackend` with deterministic outputs for:
  - generation
  - verification correct
  - verification wrong
  - runtime failure
  - malformed JSON
- Add model profile examples:
  - `mock-generation`
  - `mock-verification-correct`
  - `mock-verification-wrong`
- Add unit tests for config loading, problem normalization, backend selection, and missing dependency checks.

Acceptance checks:

- `python -m rethlas.cli plan --model mock-generation`
- unit tests pass without Codex, LiteLLM, OpenAI key, or Anthropic key
- mock verification can write and read a valid `verification.json`

### Stage 3: Migrate verification API to shared runtime

Goal: remove direct Codex command construction from `agents/verification/api/server.py`.

Why first: verification is the smallest agent surface. It accepts one statement/proof pair and expects one structured JSON result.

Tasks:

- Replace `CODEX_BIN`, `CODEX_MODEL`, `CODEX_REASONING_EFFORT`, and `build_codex_command()` with `rethlas.runtime`.
- Add `RETHLAS_MODEL` support for the verification service.
- Preserve current result layout:
  - `agents/verification/results/{run_id}/log.md`
  - `agents/verification/results/{run_id}/verification.json`
- Keep the existing Codex path working through `CodexCliBackend`.
- Add a mock verification mode for tests.
- Make API errors say `runtime provider/model failed`, not only `codex exec failed`.
- Keep schema validation in the verification MCP/server layer.

Acceptance checks:

- `python -m rethlas.cli plan --role verification`
- FastAPI `/health` still works.
- `/verify` works with the default Codex profile when Codex is available.
- `/verify` works with mock profiles without Codex.
- Missing LiteLLM/API key errors are reported before a long run starts.
- Existing `agents/verification/scripts/test_verify_endpoint.py` still passes or is updated to the new runtime model selection.

Implementation note: mock verification profiles should be kept available even after real provider support lands. They are the cheapest regression guard for runtime selection, JSON output validation, and API error behavior.

### Stage 4: Implement LiteLLM verification backend

Goal: make verification usable through OpenAI and Anthropic via LiteLLM.

Tasks:

- Add a verification-specific prompt renderer in `rethlas/prompts.py`.
- In `LiteLLMBackend`, support:
  - non-streaming completion
  - optional streaming log capture
  - timeout
  - retry policy for transient provider errors
  - usage metadata capture when LiteLLM returns it
- Force verification outputs through a JSON extraction/validation path:
  - parse raw model text
  - extract first valid JSON object if needed
  - validate with `schemas/verification_output.schema.json`
  - write normalized `verification.json`
- Add provider/model metadata to `log.md`.
- Add clear failure artifacts when JSON is missing or invalid.

Acceptance checks:

- `RETHLAS_MODEL=openai-default` verifier plan reports LiteLLM and `OPENAI_API_KEY`.
- `RETHLAS_MODEL=anthropic-default` verifier plan reports LiteLLM and `ANTHROPIC_API_KEY`.
- With valid API keys, `/verify` returns a schema-valid response for a trivial theorem.
- With invalid output, API returns a useful error and preserves raw text in `log.md`.

### Stage 5: Tool and MCP bridge design for generation

Goal: replace Codex CLI's implicit agent/tool environment with an explicit Rethlas-controlled loop.

This is the hard part. LiteLLM gives model calls; it does not automatically provide Codex's agent execution, MCP tool dispatch, file editing, shell command execution, or sub-agent behavior.

Tasks:

- Define a `ToolRegistry` for generation tools:
  - `search_arxiv_theorems`
  - `verify_proof_service`
  - `memory_init`
  - `memory_append`
  - `memory_search`
  - `branch_update`
- Decide whether to call MCP servers in-process or through MCP protocol.
  - Short-term recommended: import/call the existing Python functions directly.
  - Later option: real MCP client bridge.
- Define a provider-neutral tool call format:
  - tool name
  - JSON arguments
  - result payload
  - error payload
- Map LiteLLM/OpenAI tool calls and Anthropic tool calls into that internal format.
- Add tool-call transcript logging.
- Add guardrails:
  - allowed working directory
  - no parent path access
  - max tool calls per iteration
  - max runtime
  - verifier call timeout

Acceptance checks:

- A mock model can request `memory_init`, `memory_append`, and `verify_proof_service`.
- Tool results are written to the run log.
- Tool errors are visible and do not silently terminate the run.
- The generation loop can run without Codex in mock mode.

### Stage 6: Implement native Rethlas generation loop

Goal: allow OpenAI/Anthropic models to run the generation agent without Codex CLI.

Tasks:

- Add `rethlas/agent_loop.py`.
- Load `agents/generation/AGENTS.md` as the system/developer instruction.
- Load relevant skill files from `agents/generation/.agents/skills/` when invoked or when bootstrapping.
- Start with a conservative loop:
  1. send problem prompt and current instructions
  2. receive text/tool calls
  3. execute allowed tool calls
  4. append tool results
  5. repeat until final condition or budget
- Persist transcript and model events under `logs/{problem_id}/`.
- Keep artifacts under existing `memory/{problem_id}/` and `results/{problem_id}/`.
- Initially support one agent thread only.
- Leave recursive sub-agent parallelism for a later stage.

Acceptance checks:

- Mock generation loop completes a deterministic fake proof.
- LiteLLM generation loop can initialize memory and write a draft blueprint for a tiny toy problem.
- The loop refuses to read outside `agents/generation`.
- Existing Codex generation path still works.

### Stage 7: Rich intermediate output

Goal: make long runs legible while they are running.

Tasks:

- Define an event schema:
  - `run_started`
  - `model_started`
  - `model_delta`
  - `tool_started`
  - `tool_finished`
  - `memory_written`
  - `verification_started`
  - `verification_finished`
  - `artifact_written`
  - `branch_updated`
  - `run_finished`
  - `run_failed`
- Write JSONL event streams:
  - `logs/{problem_id}/events.jsonl`
  - optionally `logs/{problem_id}/console.log`
- Make CLI and launchers tail a concise event view by default.
- Add `rethlas status {problem}` to summarize:
  - elapsed time
  - latest model action
  - latest tool call
  - latest verification verdict
  - current artifact paths
- Add optional verbose mode to show raw model deltas.

Acceptance checks:

- During a mock run, `events.jsonl` updates after every model/tool/artifact event.
- `rethlas status ns/ns` reads events without needing the process to be alive.
- The menu launcher can show where to inspect the live log.

### Stage 8: Provider-specific quality and safety

Goal: make OpenAI and Anthropic behavior predictable instead of merely callable.

Tasks:

- Add model profile fields:
  - `max_tokens`
  - `temperature`
  - `top_p`
  - `reasoning_effort`
  - `thinking_budget_tokens`
  - `supports_tools`
  - `supports_streaming`
  - `context_window`
- Add provider capability checks.
- Add per-provider prompt formatting if needed.
- Add retry/backoff config.
- Add cost/usage recording when provider returns usage.
- Add redaction for API keys and sensitive environment values in logs.

Acceptance checks:

- Doctor prints model capabilities.
- Unsupported options fail early with clear errors.
- Logs never include API key values.
- Usage metadata is recorded when available.

### Stage 9: Recursive sub-agent support without Codex

Goal: recover the multi-agent capability currently provided by Codex CLI configuration.

Tasks:

- Model sub-agent definitions from `.codex/agents/subgoal-prover.toml` in Rethlas config.
- Add `SubAgentRunner` that can spawn controlled child loops.
- Enforce:
  - `max_threads`
  - `max_depth`
  - `job_max_runtime_seconds`
- Store sub-agent transcripts separately.
- Merge child outputs into memory channels.

Acceptance checks:

- Mock recursive run spawns two child agents and merges results.
- Max depth and max threads are enforced.
- Failed child branches are written to `failed_paths`.

### Stage 10: Cleanup and deprecation

Goal: make the new architecture the normal path while preserving old Codex compatibility.

Tasks:

- Update README quick start to root launchers and `rethlas.toml`.
- Move old manual Codex scripts into a compatibility section.
- Remove duplicated shell logic after wrappers delegate to Python.
- Mark `.codex/` config as Codex backend-specific.
- Keep `CodexCliBackend` as a supported backend, not as the architecture center.

Acceptance checks:

- New user can run doctor/setup/dry-run from root.
- Existing Codex users can still run the old path.
- OpenAI/Anthropic users have clear setup instructions and failure messages.

## Recommended commit sequence

1. `Organize docs and add root launchers`
   - doc moves
   - `rethlas.bat`
   - `rethlas.sh`
   - README quick-start update

2. `Add runtime config and planner`
   - `rethlas.toml`
   - `rethlas/config.py`
   - `rethlas/problems.py`
   - `rethlas/runtime.py`
   - `rethlas/cli.py`

3. `Add LiteLLM model gateway skeleton`
   - root `requirements.txt`
   - `LiteLLMBackend`
   - OpenAI/Anthropic model profiles
   - planner dependency checks

4. `Add root CLI run/status/setup commands`
   - `rethlas.ps1`
   - `rethlas/references.py`
   - `rethlas/status.py`
   - wrapper delegation

5. `Migrate verification API to runtime backend`
   - remove direct Codex command building from verification API
   - add mock verification tests

6. `Implement LiteLLM verification runtime`
   - JSON extraction/validation
   - provider metadata
   - schema tests

7. `Add native generation tool loop`
   - tool registry
   - mock loop
   - LiteLLM tool-call bridge

8. `Add intermediate event stream`
   - event schema
   - status/tail behavior
   - launcher integration

## Risk register

- Tool calling mismatch: OpenAI-compatible and Anthropic tool-call shapes differ. Mitigation: normalize into an internal tool-call object before dispatch.
- Codex implicit behavior loss: Codex CLI currently provides agent execution details that LiteLLM does not. Mitigation: migrate verification first, then implement a small explicit generation loop.
- Long-run opacity: without events, API backends may look stuck. Mitigation: add event JSONL before serious long-running LiteLLM generation.
- Invalid verifier JSON: direct models may produce prose around JSON. Mitigation: strict JSON extraction, schema validation, and raw-output preservation.
- Provider dependency drift: model names and options change. Mitigation: model profiles in `rethlas.toml`, doctor capability checks, and clear dependency errors.
- Over-frameworking: adopting a full agent framework too early could obscure Rethlas-specific memory and verification semantics. Mitigation: keep Rethlas-owned loop first; revisit frameworks only after native loop requirements are explicit.

## Near-term recommended first patch

The first implementation patch should be deliberately small:

1. Add root `rethlas.bat` and `rethlas.sh` menus that call the existing scripts.
2. Add root `rethlas.ps1`.
3. Add `rethlas/cli.py` with `doctor`, `run --dry-run`, and problem normalization.
4. Make `agents/generation/tests/run_example.ps1` delegate to the new CLI or share its logic.
5. Leave verification API and Codex backend untouched.

This immediately improves usability without forcing the bigger backend redesign into the first patch.

## Non-goals for this phase

- Rewriting the math-agent instructions.
- Replacing Codex immediately.
- Changing the memory/result directory layout.
- Building a GUI.
- Designing the richer intermediate-output protocol in detail.

## Open questions

1. Should the root command be only `rethlas.ps1` for now, or should we also add a Windows `rethlas.cmd` shim?
2. Should `setup` create separate venvs for generation and verification, or one repo-level `.venv`?
3. Should `verify-server` run in the foreground only, or also support a background mode with PID/log management?
4. Should non-Codex backends target the OpenAI Responses API first, or a provider-neutral adapter first?
