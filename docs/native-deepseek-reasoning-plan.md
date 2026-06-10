# Native DeepSeek reasoning loop plan

## Goal

Make the non-Codex runtime strong enough for pure mathematical proof search:

- it should generate a complete proof blueprint;
- run the verifier;
- use verifier feedback to repair the proof;
- preserve useful memory between attempts;
- stop only when a verified proof exists or when a clear budget is exhausted.

The goal is not to copy Codex CLI's full agent loop. The native path should be
smaller and more predictable, but it must close the core quality gap: today it
can produce a draft and run one verifier pass, but it does not yet behave like
a self-correcting proof agent.

## Current State

The Codex path is still the most complete runtime:

- `rethlas.toml` maps `gpt-5.5`, `codex-fast`, and `codex-deep` to
  `provider = "codex"`.
- `rethlas/runtime.py::CodexCliBackend` runs `codex exec` in the generation or
  verification working directory.
- `reasoning_effort` is passed through to Codex CLI.
- Codex receives `agents/generation/AGENTS.md` as an actual agent instruction
  context and can use the richer Codex tool environment.

The native DeepSeek path is now usable but narrower:

- env preset `deepseek` resolves through `rethlas/presets.py` and LiteLLM;
- `rethlas/agent_loop.py::run_native_generation` initializes memory, runs a
  LiteLLM tool loop, writes `blueprint.md`, and calls `verify_proof_service`
  once;
- the native tool registry exposes only:
  `search_arxiv_theorems`, `verify_proof_service`, `memory_init`,
  `memory_append`, `memory_search`, and `branch_update`;
- job control, tailing, background runs, resume, and the results viewer already
  exist around this path.

Key gaps:

1. Verification failure is not terminally represented as failure. Native
   generation can return `0` after writing only an unaccepted `blueprint.md`.
2. There is no verifier-driven repair loop. One wrong verdict ends the native
   run instead of feeding repair hints back into the model.
3. Convergence pressure is currently prompt-only. The code still passes tools
   after telling the model to stop using tools.
4. Streaming and fallback tool-call shapes are only partly normalized.
5. DeepSeek-specific reasoning controls are implicit. Env presets do not expose
   a thinking/reasoning budget profile.
6. AGENTS.md describes skills and recursive agents, but the native loop only
   sees those as text. It needs a smaller real control policy tailored to math.

## Critical Correction

The current native path is not just lower quality than Codex; it violates the
basic Rethlas success contract. Codex generation follows `AGENTS.md`, where a
wrong verification report must feed a repair/replan cycle and the agent must
stop only after `blueprint_verified.md` is published. Native DeepSeek currently
does one post-generation verifier call and can then exit `0` with only an
unaccepted draft.

This must be corrected before any profile tuning, UI polish, or broader quality
work. The first coding slice should change the control flow from:

```text
generate once -> verify once -> return 0 even if wrong
```

to:

```text
attempt 1 draft -> verify
if wrong: persist report -> repair prompt -> attempt 2 -> verify
...
if correct: write blueprint_verified.md -> return 0
if budget exhausted: emit run_failed/native_generation_exhausted -> return nonzero
```

There is no acceptable intermediate state where native generation returns
success without a verified proof.

## Target Behavior

For native generation with `--model deepseek`:

1. Prepare problem and references as today.
2. Initialize memory.
3. Produce an initial plan or proof attempt.
4. Optionally call retrieval and memory tools.
5. Write a complete markdown candidate proof.
6. Run `verify_proof_service`.
7. If verdict is `correct`, write `blueprint_verified.md`, emit
   `verification_finished(correct)`, and exit `0`.
8. If verdict is `wrong`, append the verification report to memory, ask the
   model for a targeted repair, and repeat.
9. If the loop exhausts configured attempt/model/tool budgets, emit
   `run_failed` with the last verifier report and exit nonzero.

This gives the native path the same important success contract as the Codex
path: success means a verified artifact, not just a generated draft.

## Design Principles

1. Keep the loop explicit in Python. Do not hide critical run state inside one
   giant prompt.
2. Make verifier verdicts the control signal.
3. Preserve model freedom inside each attempt, but bound the outer loop.
4. Prefer math-specific phases over generic multi-agent orchestration.
5. Treat retrieval as support. Prevent search-only loops.
6. Keep artifacts append-only and inspectable.
7. Keep Codex behavior intact while improving the LiteLLM/native path.

## Proposed Architecture

### 1. Add a Native Run State Object

Add a small internal state type in `rethlas/agent_loop.py`, or a new
`rethlas/native_loop.py` if the file grows too large.

Suggested fields:

- `problem_id`
- `attempt_index`
- `max_attempts`
- `max_model_iterations_per_attempt`
- `max_tool_iterations_per_attempt`
- `phase`: `plan`, `draft`, `verify`, `repair`, `failed`, `verified`
- `last_draft`
- `last_verification`
- `tool_counts`
- `search_iterations`
- `repair_history`

Persist phase transitions as events:

- `native_attempt_started`
- `candidate_written`
- `verification_started`
- `verification_finished`
- `repair_started`
- `repair_finished`
- `native_attempt_failed`
- `native_generation_exhausted`

### 2. Split Drafting From Repair

Replace the current single `_run_litellm_tool_loop(...) -> str` call with two
closely related prompts:

- `draft`: solve from the problem statement, references, memory summary, and
  allowed tools;
- `repair`: solve from the previous candidate plus the verifier report.

The repair prompt should be strict:

- address each `critical_errors` item first;
- address each `gaps` item next;
- explain which parts of the proof changed in memory, but return only the full
  revised markdown candidate as the model response;
- do not delete working lemmas unless the verifier report makes them invalid.

The output remains a complete markdown proof every time. Avoid patch/diff
application at this stage; full replacement is simpler and less fragile.

### 3. Make Verification A Loop

Change `run_native_generation` from:

```text
draft once -> verify once -> return 0
```

to:

```text
for attempt in 1..max_attempts:
    candidate = draft_or_repair(...)
    write blueprint.md
    verification = verify_proof_service(...)
    if correct:
        write blueprint_verified.md
        return 0
    append verifier report to memory
return nonzero
```

Default budgets:

- `max_attempts = 4`
- `max_model_iterations_per_attempt = 16`
- `max_tool_iterations_per_attempt = 8`
- `max_search_iterations_per_attempt = 4`

For simple problems, this stays cheap. For harder problems, the system gets
several genuine repair opportunities without becoming an unbounded daemon.

### 4. Enforce Tool Policy In Code

The current convergence pressure only adds a prompt. Change the code so policy
affects `completion_kwargs`.

Suggested modes:

- `tools="auto"`: normal exploration;
- `tools="no_search"`: memory and verifier allowed, retrieval blocked;
- `tools="none"`: final candidate must be plain markdown;
- `tools="verify_only"`: only `verify_proof_service` is exposed if needed.

The first version can implement this by filtering `registry.schemas()` before
passing it to LiteLLM.

Important behavior:

- after too many search-only iterations, remove `search_arxiv_theorems`;
- when forcing final blueprint output, do not pass any tools;
- during repair, allow memory tools but keep search disabled unless the verifier
  failure explicitly depends on a missing external theorem.

### 5. Normalize Tool Calls Robustly

Before adding more loop logic, fix tool-call normalization so all paths share
one representation.

Needed changes:

- support pydantic object tool calls, dict tool calls, and streaming deltas;
- generate a fallback `tool_call_id` if the provider omits one;
- serialize assistant tool calls without assuming `.get`;
- record invalid JSON arguments as `tool_finished(ok=false)` and feed that
  error back to the model;
- add a regression for fallback non-streaming tool calls from a streamed failure.

This is required for DeepSeek because streaming + tools is a common source of
provider-specific behavior.

### 6. Add Native Reasoning Profiles

Env presets intentionally do not hardcode a vendor model, but the runtime still
needs reusable local policy profiles.

Add optional TOML native-loop profiles, for example:

```toml
[native_profiles.deepseek-balanced]
max_attempts = 4
max_model_iterations_per_attempt = 16
max_search_iterations_per_attempt = 4
temperature = 0.2

[native_profiles.deepseek-deep]
max_attempts = 8
max_model_iterations_per_attempt = 24
max_search_iterations_per_attempt = 6
temperature = 0.1
```

Then add a CLI flag:

```text
python -m rethlas.cli run ns/ns --model deepseek --native-profile deepseek-deep
```

Do not add this before the verifier loop exists. The first implementation can
keep constants in code and introduce config after the control flow is stable.

### 7. Summarize Memory For Each Attempt

The native loop should not dump all JSONL memory into the prompt. Add a compact
summary builder that reads recent/high-value memory:

- latest `verification_reports`;
- latest `failed_paths`;
- current `branch_states`;
- selected `proof_steps`;
- latest `big_decisions`;
- whether `blueprint.md` or `blueprint_verified.md` already exists.

Use the summary in both draft and repair prompts. Keep the raw memory files as
the source of truth.

### 8. Make Resume Semantically Real

`resume` currently tells the model to inspect memory/results/logs. Native
resume should also initialize the run state from disk:

- if `blueprint_verified.md` exists, do not rerun unless `--force` is passed;
- if `blueprint.md` exists and no verified proof exists, start in `repair` mode
  after running or reading the latest verifier report;
- if the latest event is `run_failed`, include that failure in the first repair
  prompt;
- preserve attempt numbering by counting prior `native_attempt_started` events.

### 9. Strengthen The Verification Agent Contract

The native generator can only self-correct as well as the verifier reports
useful failures. Keep the JSON schema, but make the generator rely on these
fields:

- `verification_report.summary`
- `verification_report.critical_errors`
- `verification_report.gaps`
- `repair_hints`

Near-term improvement: require each critical error/gap to include a location
string and a repairable issue statement. Tests can use mock verifier payloads
without calling a real model.

## Implementation Phases

### Phase 0: Mandatory Behavior Lock

Deliverables:

- Add failing tests that capture the intended native contract before changing
  the implementation.
- Native generation must not return `0` unless `blueprint_verified.md` exists.
- A wrong verifier verdict must trigger another model attempt when repair budget
  remains.
- Repeated wrong verifier verdicts must exhaust the native budget and return
  nonzero.
- Job terminal status must treat exhausted or unverified native runs as failed.

Acceptance checks:

- unit test: first wrong verifier verdict is fed back into a repair prompt and
  the second candidate can verify;
- unit test: verifier always wrong causes budget exhaustion and nonzero return;
- unit test: a run that only writes `blueprint.md` is not marked succeeded;
- unit test: background job terminal reconciliation marks exhausted native runs
  failed;
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_rethlas_runtime.py tests/test_rethlas_streaming.py tests/test_rethlas_jobs.py tests/test_rethlas_follow.py -q`.

### Phase 1: Minimal Verifier-Driven Repair Loop

Deliverables:

- Add outer native attempt loop.
- Add draft and repair prompt builders.
- Persist verifier reports to generation memory.
- Write `blueprint_verified.md` only on `correct`.
- Emit detailed events per attempt.
- Remove the single post-generation verifier call as the terminal control path.
- Keep the first implementation simple: full-candidate replacement on each
  repair attempt, not patch/diff application.

Acceptance checks:

- mock model first emits an invalid proof, mock verifier returns `wrong`, model
  receives repair prompt, second candidate verifies;
- mock verifier always wrong, native run exhausts budget and exits nonzero;
- `watch` exits nonzero for exhausted native runs and zero for verified runs.

Phase 0 and Phase 1 should be implemented as one corrective slice. Splitting
them leaves the native path either unable to self-correct or still capable of
false success.

### Phase 2: Tool-Call Hardening And Math-Specific Control Policy

Deliverables:

- Tool-call serialization handles pydantic objects, dicts, and streaming
  deltas through one normalization path.
- Add code-enforced tool modes.
- Add search budget and search disabling.
- Add final-candidate no-tools mode.
- Add memory summary builder.
- Add repair prompt that targets verifier findings.

Acceptance checks:

- model that repeatedly calls search is forced into no-search/final mode;
- fallback non-streaming tool calls serialize correctly;
- convergence/final mode omits `tools` from LiteLLM kwargs;
- repair prompt includes verifier findings and prior candidate;
- memory summary is bounded and deterministic;
- generated events show why tools were restricted.

### Phase 3: Native Profiles And CLI Controls

Deliverables:

- Add native loop config fields or native profiles.
- Add CLI flags for max attempts and native profile.
- Keep default behavior conservative.
- Document `deepseek-chat` vs `deepseek-reasoner` expectations without
  hardcoding either model name.

Acceptance checks:

- `plan --model deepseek` shows native loop budgets;
- `run --model deepseek --native-profile deepseek-deep --dry-run` shows the
  chosen profile;
- missing/unknown profile gives a clear error.

### Phase 4: Real-Provider Smoke Tests

Deliverables:

- Add manual smoke scripts that are skipped unless `DEEPSEEK_API_KEY` and
  `DEEPSEEK_MODEL` are set.
- Run a simple theorem through DeepSeek native path.
- Run a deliberately flawed first-attempt scenario with a mock verifier.
- Record event traces for quality comparison.

Acceptance checks:

- simple included `example` reaches `blueprint_verified.md`;
- at least one mock repair-loop test proves the loop can self-correct;
- no test requires network by default.

## Expected Quality Impact

After Phase 1, DeepSeek native should stop producing false-success drafts. That
alone makes result quality more trustworthy.

After Phase 2, the native path should handle moderately complex pure-math
problems better because verifier reports become actionable state rather than
terminal text. It will still be simpler than Codex: no broad Codex tool
environment, no true recursive sub-agent orchestration, and no hidden Codex
reasoning controls. But it should have the core property that matters for this
repo: proof attempts are repeatedly checked and repaired until either verified
or honestly failed.

After Phase 3, users can trade time/cost for quality using native profiles
instead of changing code.

## Risks

- DeepSeek tool-call compatibility may vary by model and endpoint. Keep the
  fallback path and tool normalization heavily tested.
- The verifier is still model-based, not a formal proof assistant. A verified
  proof is a stronger artifact than a draft, but not a formal guarantee.
- Repair loops can overfit to verifier wording. Preserve the full previous
  candidate and require complete revised proofs to reduce local patch damage.
- More attempts mean more API cost. Defaults should stay modest.

## Recommended Next Step

Implement Phase 0 and Phase 1 together as the next coding slice. They are
closely coupled: success semantics, tool normalization, and the verifier repair
loop all touch `run_native_generation` and its tests. Defer profiles and richer
memory summarization until the basic self-correction loop is proven with mocks.
