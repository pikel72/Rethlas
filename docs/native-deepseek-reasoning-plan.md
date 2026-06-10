# Native DeepSeek math tool plan

## Goal

Make the DeepSeek/LiteLLM native path a real mathematical proof agent without
turning it into a general-purpose shell agent.

The Python controller remains responsible for run lifecycle, artifact writes,
verification calls, event logging, and success/failure semantics. The model is
responsible for mathematical strategy, retrieval choices, proof drafting, and
repair decisions. Tools should expose only the mathematical context needed for
that work.

## Current State

The native path already has the basic controller shape:

- `rethlas.cli.cmd_run` routes non-Codex generation models into
  `run_native_generation`.
- `run_native_generation` initializes memory, runs a LiteLLM tool loop, writes a
  candidate `blueprint.md`, calls `verify_proof_service`, and repeats attempts
  when verification fails.
- `blueprint_verified.md` is written only when the verifier returns `correct`.
- Native tools now include bounded run/reference context, math retrieval/source
  inspection, structured math memory, the verifier service, and compatibility
  memory/branch tools.
- Native generation uses a compact math-specific system prompt. `AGENTS.md`
  remains the Codex CLI policy.

The remaining gap is end-to-end validation against real provider behavior and
quality tuning once the tool belt is in place.

## Required Tool Set

### 1. `read_run_context`

Purpose: give the model a bounded snapshot of the current proof run.

Input:

```json
{
  "problem_id": "string",
  "include_draft": true,
  "include_recent_events": true
}
```

Return:

- problem id and problem file path;
- complete problem statement;
- reference directory status;
- bounded reference summary;
- existing `blueprint.md` text when requested and present;
- existing `blueprint_verified.md` status;
- latest verifier report;
- latest failed paths, big decisions, proof steps, and branch states;
- recent run events relevant to resume/repair.

Why it is necessary:

Resume and repair currently depend on prompt text telling the model to inspect
prior state. The native model needs one deterministic tool call that returns the
state it is expected to use.

Implementation notes:

- Build in Python from `ProblemPaths`, memory JSONL files, result files, and log
  events.
- Bound each section by character count and item count.
- Prefer newest verifier reports and failed paths over raw event volume.
- Do not expose paths outside `agents/generation`.

### 2. `read_problem_reference`

Purpose: let the model inspect user-provided reference material for the current
problem beyond the initial prompt excerpt.

Input:

```json
{
  "problem_id": "string",
  "relative_path": "string",
  "max_chars": 12000
}
```

Return:

- normalized reference path;
- text content for `.md`, `.txt`, or `.tex`;
- for PDFs, the matching extracted `.txt` path/content when available;
- clear error if the requested file is outside the problem reference directory.

Why it is necessary:

The initial prompt includes only a bounded reference excerpt. Long local refs
can contain decisive definitions, theorem statements, or assumptions that the
model must be able to revisit precisely.

Implementation notes:

- Scope reads to `data/{problem_id}.refs/` and its `.extracted/` subtree.
- Add a companion `list_problem_references(problem_id)` only if needed for
  discoverability.
- Keep this as a math/reference reader, not a general filesystem reader.

### 3. `search_math_results`

Purpose: replace bare theorem search with a math-oriented retrieval tool.

Input:

```json
{
  "problem_id": "string",
  "query": "string",
  "purpose": "background|lemma|counterexample|definition|repair",
  "num_results": 10
}
```

Return:

- normalized result list with title, theorem/statement text, arXiv id, theorem
  id, and source URL when available;
- a short relevance note for each result;
- an event/memory record of the query and result ids.

Why it is necessary:

Mathematical proof search needs retrieval, but the model should receive results
as candidate mathematical facts with provenance, not just an endpoint payload.

Implementation notes:

- Wrap the existing `search_arxiv_theorems` implementation first.
- Keep the existing tool temporarily for compatibility, but move prompts toward
  `search_math_results`.
- Persist search summaries to memory so later repair attempts can reuse them.

### 4. `fetch_math_source`

Purpose: inspect the source behind a retrieval result.

Input:

```json
{
  "problem_id": "string",
  "source_id": "string",
  "focus_query": "string",
  "max_chars": 16000
}
```

Return:

- source metadata;
- cached local text path when available;
- focused excerpts around matching theorem/proposition/definition terms;
- source-level assumptions or definitions found near the excerpt;
- clear status when the source cannot be fetched or extracted.

Why it is necessary:

Theorem statements alone are not enough. The model must inspect definitions,
ambient assumptions, and proof context before importing a result into a proof.

Implementation notes:

- First support arXiv ids returned by `search_math_results`.
- Download/cache under `agents/generation/downloads/`.
- Extract PDFs to text using the existing `pdftotext` approach when available.
- Return focused excerpts, not whole papers.
- Record fetched source metadata in memory.

### 5. `record_math_note`

Purpose: replace free-form `memory_append` calls with schema-guided mathematical
state updates.

Input:

```json
{
  "problem_id": "string",
  "note_type": "conclusion|source_note|subgoal|proof_step|failed_path|decision",
  "content": {},
  "branch_id": "string"
}
```

Return:

- selected memory channel;
- written path;
- normalized record;
- timestamp.

Why it is necessary:

The native loop needs persistent memory, but unrestricted JSON records make
later search noisy. A small set of mathematical note types keeps memory useful
across attempts.

Implementation notes:

- Internally call existing `memory_append`.
- Validate required fields per `note_type`.
- Keep `memory_append` available for controller/internal writes.
- Prompt the model to prefer `record_math_note`.

### 6. `search_memory`

Purpose: provide bounded memory retrieval for mathematical state.

Input:

```json
{
  "problem_id": "string",
  "query": "string",
  "note_types": ["conclusion", "failed_path"],
  "limit": 8
}
```

Return:

- compact hits grouped by note type;
- score;
- short excerpt;
- source memory channel;
- timestamp.

Why it is necessary:

Existing `memory_search` returns raw JSONL-shaped payloads. Repair and resume
need compact mathematical recall, especially prior failures and verifier reports.

Implementation notes:

- Wrap existing BM25 search.
- Map `note_types` to memory channels.
- Summarize large records before returning them to the model.
- Keep raw memory files as source of truth.

## Controller Responsibilities

The model should not own the success contract. The Python controller must
continue to:

- build `ProblemPaths` and prepare references;
- initialize memory;
- call the model tool loop;
- write candidate markdown to `blueprint.md`;
- call `verify_proof_service` after every candidate;
- write verifier reports to memory;
- write `blueprint_verified.md` only on `verdict == "correct"`;
- emit run/job events;
- enforce attempt, runtime, and tool-iteration budgets.

The model can still see verifier reports through repair prompts and
`read_run_context`, but verification remains a controller step.

## Prompt Changes

Replace the native system prompt's direct use of full `AGENTS.md` with a compact
native math policy:

1. Solve the stated problem and return complete markdown proof candidates.
2. Use `read_run_context` at the start of resumed or repair attempts.
3. Use `read_problem_reference` before relying on local refs not already quoted
   in the prompt.
4. Use `search_math_results` and `fetch_math_source` before relying on external
   literature.
5. Use `record_math_note` for durable conclusions, source notes, subgoals,
   proof steps, failed paths, and decisions.
6. Return only the complete candidate proof when ready.
7. Mathematical notation must use dollar-delimited LaTeX.

Keep `AGENTS.md` as the Codex CLI policy. The native prompt should be shorter
and match the actual native tools.

## Implementation Phases

### Phase 0: Repair Current Native Loop Defects

Deliverables:

- Fix the empty-draft path so it does not reference an undefined
  `finish_reason`.
- Preserve the previous non-empty draft across an empty model response.
- Add regression tests for empty response after an existing draft.

Acceptance:

- Empty response records `empty_draft_skipped`.
- Existing `blueprint.md` is not clobbered.
- Next repair attempt receives the previous non-empty draft.

### Phase 1: Run Context And Reference Tools

Deliverables:

- Add `read_run_context`.
- Add `read_problem_reference`.
- Add optional `list_problem_references` if discovery is needed by tests or
  prompts.
- Register these tools in `build_generation_tool_registry`.
- Add prompt instructions for when to call them.

Acceptance:

- A resumed run can retrieve prior draft and latest verifier report through
  `read_run_context`.
- A reference under `data/{problem_id}.refs/` can be read.
- Path traversal outside the reference directory is rejected.
- Returned context is bounded and deterministic.

### Phase 2: Math Retrieval Source Tools

Deliverables:

- Add `search_math_results` as a wrapper around existing theorem search.
- Add `fetch_math_source` for arXiv ids and cached downloads.
- Persist retrieval summaries/source notes to memory.

Acceptance:

- Search returns normalized result records.
- Fetching an arXiv id caches a source under `downloads/` and returns focused
  excerpts.
- Failed fetches return structured failure without crashing the model loop.
- No default unit test requires network; network tests are opt-in.

### Phase 3: Structured Math Memory

Deliverables:

- Add `record_math_note`.
- Add `search_memory`.
- Update native prompt to prefer these tools over raw memory calls.
- Keep raw `memory_init`, `memory_append`, and `memory_search` for compatibility
  until existing tests and logs are migrated.

Acceptance:

- Each note type writes to the intended channel with required fields validated.
- `search_memory` returns compact hits rather than large raw records.
- Repair prompts can include memory hits without exceeding fixed bounds.

### Phase 4: Native Prompt Alignment

Deliverables:

- Add `_native_math_system_prompt`.
- Use it for LiteLLM/native generation.
- Keep Codex CLI behavior unchanged.
- Remove native-only reliance on unavailable AGENTS.md mechanisms.

Acceptance:

- Native prompt names only tools that are actually registered.
- Draft prompt includes problem statement, reference summary, output policy, and
  tool policy.
- Repair prompt includes previous candidate, verifier report, and run context
  policy.

### Phase 5: End-To-End Validation

Deliverables:

- Mock tests for resume, reference read, retrieval failure, memory note writes,
  and verifier repair.
- One opt-in DeepSeek smoke script for `example`.
- Update docs and CLI help if tool behavior changes user-visible commands.

Acceptance:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_rethlas_streaming.py tests/test_rethlas_jobs.py tests/test_rethlas_follow.py tests/test_rethlas_viewer.py tests/test_rethlas_presets.py tests/test_rethlas_config.py`
  remains green.
- New native-tool tests pass without network.
- Opt-in DeepSeek smoke reaches either `blueprint_verified.md` or a clear
  nonzero exhausted state with inspectable events.

## Recommended Next Slice

Phase 0 through Phase 4 are the current implementation target for this slice:

- fix empty-draft handling;
- expose bounded run context and problem-reference tools;
- expose math retrieval/source tools;
- expose structured math-memory tools.
- replace broad native `AGENTS.md` usage with a compact native math policy.

The next slice should be Phase 5: end-to-end validation with mocks and an
opt-in DeepSeek smoke run.
