# Native loop hardening plan

## Objective

Close the remaining gap between the Codex runtime and the native LiteLLM/DeepSeek
runtime without reimplementing Codex wholesale. The next work should focus on
the three highest-impact weaknesses:

1. the native loop has a smaller and less controlled tool surface;
2. the native loop still lacks a real math-oriented phase controller;
3. repair currently replaces the whole candidate proof instead of targeting
   flawed proof sections.

The current native loop already has the essential verifier repair contract:
candidate proof, verifier pass, repair attempt, and verified/nonzero terminal
semantics. This plan builds on that foundation.

## Current Baseline

Implemented behavior as of the current `main` branch:

- Native generation uses LiteLLM-compatible models such as DeepSeek.
- A candidate `blueprint.md` is generated, verified, and repaired for up to
  eight attempts.
- The whole run is bounded by `runtime.timeout_seconds`.
- Success requires a verifier `correct` verdict and `blueprint_verified.md`.
- Exhaustion writes `native_generation_exhausted` and exits nonzero.
- Mathematical output prompts require `$...$` and `$$...$$` delimiters.

Known remaining problems:

- Convergence pressure is still prompt-only; tools are still passed after the
  prompt says to stop using tools.
- Tool call ids are generated from content hashes when provider ids are absent,
  which can collide for repeated anonymous calls.
- Each attempt writes the model transcript to the same log path, so later repair
  attempts can overwrite earlier attempt transcripts.
- The native loop does not have explicit math phases such as orient, retrieve,
  decompose, draft, and repair.
- Repair prompts ask for a full replacement proof rather than a targeted section
  fix.

## P0: Tool Control And Auditability

### Goals

Make tool use explicit, enforceable, and observable. This is required before
building a stronger native phase controller because the controller must be able
to decide which tools are actually available in each phase.

### Implementation

1. Add native tool modes:
   - `explore`: allow `search_arxiv_theorems`, `memory_init`, `memory_append`,
     `memory_search`, and `branch_update`.
   - `repair`: allow `memory_append`, `memory_search`, `branch_update`, and
     optionally `search_arxiv_theorems` only when the verifier report indicates
     missing external support.
   - `finalize`: pass no tools to LiteLLM.
   - `verify_only`: expose only `verify_proof_service` if the model itself is
     ever allowed to request verification inside an attempt.

2. Add a schema filtering helper in `rethlas/agent_loop.py`, for example:

   ```python
   def _tool_schemas_for_mode(registry, mode: str) -> list[dict] | None:
       ...
   ```

   The helper should filter by function name before assigning
   `completion_kwargs["tools"]`.

3. Make convergence pressure enforceable:
   - after the search/tool-only threshold is reached, switch the next model call
     to `finalize`;
   - in `finalize`, omit `tools` completely;
   - append a `tool_mode_changed` event with reason and attempt.

4. Replace hash-based fallback tool ids:
   - use deterministic per-loop counters such as
     `call_attempt{attempt}_iter{iteration}_{index}`;
   - avoid content-hash ids because repeated identical calls can collide.

5. Preserve attempt transcripts:
   - write `request.log_path` as a run-level summary or latest attempt;
   - write each attempt to a distinct file such as
     `attempt-001.md`, `attempt-002.md` under the same log directory;
   - include the attempt transcript path in `native_attempt_started` or
     `candidate_written`.

6. Add a bounded memory summary helper:
   - include latest verifier reports;
   - include latest failed paths and branch states;
   - include whether `blueprint.md` and `blueprint_verified.md` exist;
   - cap the rendered summary by characters.

### Tests

- Tool schemas in `finalize` mode are omitted from LiteLLM kwargs.
- Search is absent after search budget is exceeded.
- Tool-call ids remain unique for repeated anonymous tool calls in one
  iteration.
- Attempt transcripts are written to distinct files and earlier attempts are
  not overwritten.
- Memory summary is bounded and deterministic.

## P1: Minimal Math Phase Controller

### Goals

Move from a generic "generate or repair" loop to a small explicit math-state
machine. This should give the native path some of the structure Codex gets from
`AGENTS.md`, while keeping control logic in Python.

### Phase Model

Use these phases:

- `orient`: restate the problem, assumptions, target, and known context.
- `retrieve`: search and read references only when needed.
- `decompose`: produce candidate lemmas, proof plan, and failure risks.
- `draft`: write a full candidate proof.
- `repair`: fix a verifier-rejected candidate.
- `finalize`: produce the final markdown candidate without tools.
- `verify`: Python-controlled verifier call.

The first implementation can run a fixed minimal sequence:

```text
orient -> decompose -> draft -> verify
wrong -> repair -> finalize -> verify
wrong -> repair -> finalize -> verify
...
```

Retrieval should be optional and triggered by either:

- no references are available and the problem is nontrivial;
- the model explicitly asks for external support during orient/decompose;
- the verifier report names a missing external theorem or applicability issue.

### Implementation

1. Add a small `NativeRunState` dataclass:
   - `attempt`
   - `phase`
   - `tool_mode`
   - `search_count`
   - `last_draft`
   - `last_verification`
   - `strategy_summary`
   - `failed_paths`

2. Emit phase events:
   - `native_phase_started`
   - `native_phase_finished`
   - `tool_mode_changed`
   - `strategy_updated`
   - `failed_path_recorded`

3. Add prompt builders per phase:
   - `_native_orient_prompt`
   - `_native_decompose_prompt`
   - `_native_draft_prompt`
   - `_native_repair_prompt`
   - `_native_finalize_prompt`

4. Persist useful intermediate outputs:
   - orient output to `immediate_conclusions`;
   - decompose output to `subgoals` or `big_decisions`;
   - failed verifier facts to `failed_paths`;
   - verifier reports to `verification_reports`.

5. Keep Python in charge of verification:
   - do not rely on the model to decide success;
   - do not allow success unless `blueprint_verified.md` is written.

### Tests

- A successful run emits orient, decompose, draft, verify phases.
- A wrong verifier verdict emits repair and finalize phases before the next
  verifier call.
- Phase outputs are written to the expected memory channels.
- The model cannot skip verification by returning confident prose.

## P2: Section-Aware Repair

### Goals

Reduce proof degradation caused by full replacement repair. The native loop
should modify only the flawed section when the verifier report gives enough
location information.

### Implementation

1. Add a markdown proof section parser:
   - recognize `# lemma ...` and `# theorem ...`;
   - preserve section order;
   - preserve each section's `## statement` and `## proof`;
   - keep the final target theorem last.

2. Add verifier-location mapping:
   - map report locations such as lemma ids, theorem ids, or headings to parsed
     sections;
   - if mapping fails, fall back to full replacement.

3. Add targeted repair prompt:
   - include the full proof as context;
   - include the target section;
   - include the verifier findings;
   - require a complete replacement for only the target section.

4. Reassemble the proof:
   - replace only the target section;
   - keep the original final theorem statement unchanged;
   - run the verifier on the reassembled complete proof.

5. Record repair metadata:
   - changed section ids;
   - verifier findings addressed;
   - whether fallback full replacement was used.

### Tests

- A verifier gap in one lemma replaces only that lemma section.
- The final theorem statement remains exactly the input problem statement.
- If section mapping fails, the loop falls back to full replacement.
- Dollar-delimited LaTeX policy remains present in targeted repair prompts.

## Execution Order

1. Implement P0 first. It fixes hard control and observability issues that make
   later phase work safer.
2. Implement P1 second. It adds the math-specific control loop while still using
   full-candidate repair.
3. Implement P2 last. It touches markdown parsing and should be isolated from
   tool/phase-control changes.

The next coding slice should be P0 only unless the diff remains small after
tests are written. P1 and P2 should each be committed separately.

## Acceptance Gate

Before merging each phase:

- run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 agents\\verification\\.venv\\Scripts\\python.exe -m pytest tests -q`;
- inspect `git diff --check`;
- manually review event names and memory channels for consistency with
  `agents/generation/AGENTS.md`;
- do not stage generated logs, memory files, or result artifacts.
