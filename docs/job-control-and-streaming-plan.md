# Rethlas job control and streaming output plan

This plan covers process control, progress visibility, and resumability for
long Rethlas runs. It explicitly does **not** add sleep-prevention behavior:
system sleep is a valid power-saving policy. Rethlas should instead persist
enough state to make interruption understandable and recoverable.

## Current State

Rethlas already has the pieces needed for a first usable control surface:

- `python -m rethlas.cli run <problem>` runs generation in the foreground.
- `python -m rethlas.cli status <problem>` summarizes logs, memory, results,
  and the latest events.
- Native LiteLLM generation appends structured events to
  `agents/generation/logs/{problem_id}/events.jsonl`.
- Native LiteLLM generation streams model text to stdout when `--no-live-log`
  is not used.
- Results are written to `agents/generation/results/{problem_id}/blueprint.md`
  and `blueprint_verified.md`.
- `python -m rethlas.cli results-site --open` serves generated results in a
  browser.

The missing layer is a first-class job model. Users can start and interrupt a
foreground process, but there is no durable job registry, no one-command stop,
no tail/watch command, and no consistent progress stream across Codex CLI,
LiteLLM, verification, and viewer/server processes.

## Goals

1. Make every long-running action controllable from the root CLI.
2. Show live progress in the terminal while preserving machine-readable events.
3. Make background runs inspectable and stoppable.
4. Preserve enough state that sleep, terminal closure, or Ctrl+C leaves a clear
   trail and a practical continuation path.
5. Keep the implementation small: process files, JSONL events, and standard
   Python APIs before considering a daemon or GUI.

## Non-Goals

- Do not prevent sleep, disable hibernation, or hold OS wake locks.
- Do not build a tray app yet.
- Do not require a database.
- Do not require Zola, Node, or platform-specific service managers.
- Do not guarantee exact model-call resume after sleep. Resume should restart
  the run with persisted memory/results as context.

## User Workflows

### Foreground Run

```bash
python -m rethlas.cli run example --model deepseek
```

Expected behavior:

- prints a concise run header
- streams model text
- prints tool start/finish lines
- prints verifier attempts and final verdict
- writes all events to JSONL
- exits `0` only when the run completed according to the current semantics

### Background Run

```bash
python -m rethlas.cli run example --model deepseek --background
python -m rethlas.cli jobs
python -m rethlas.cli tail example
python -m rethlas.cli stop example
```

Expected behavior:

- `--background` starts a child process and returns immediately
- `jobs` shows running/stopped/failed/succeeded jobs
- `tail` follows structured events and latest stdout/stderr
- `stop` sends a graceful interrupt first, then escalates only if needed

### Watch Until Verified

```bash
python -m rethlas.cli watch example
```

Expected behavior:

- follows `events.jsonl`
- exits `0` when `blueprint_verified.md` exists or `verification_finished`
  reports `correct`
- exits nonzero on `run_failed`
- keeps working after the original run process exits

### Resume After Sleep Or Interruption

```bash
python -m rethlas.cli status example
python -m rethlas.cli resume example --model deepseek
```

Expected behavior:

- `status` makes the last known state obvious:
  - process alive or dead
  - latest event
  - draft exists
  - verified draft exists
  - last error if any
- `resume` starts a new run that explicitly tells the model to inspect existing
  memory/results/logs before continuing.

Resume is not exact continuation of an interrupted HTTP call. It is a controlled
new run over persisted artifacts.

## Job Registry

Add a small file-backed registry under:

```text
agents/generation/jobs/
```

Each job writes:

```text
agents/generation/jobs/{job_id}/job.json
agents/generation/jobs/{job_id}/stdout.log
agents/generation/jobs/{job_id}/stderr.log
```

`job.json` fields:

```json
{
  "job_id": "example",
  "problem_id": "example",
  "role": "generation",
  "model": "deepseek",
  "pid": 12345,
  "status": "running",
  "started_at_utc": "...",
  "ended_at_utc": null,
  "command": ["python", "-m", "rethlas.cli", "run", "example", "--model", "deepseek"],
  "log_dir": "agents/generation/logs/example",
  "result_dir": "agents/generation/results/example"
}
```

The first implementation can use `job_id = problem_id` and reject duplicate
running jobs for the same problem. A later implementation can add
timestamp-suffixed job IDs for parallel attempts.

## Event Model

Keep `events.jsonl` as the source of truth. Extend it rather than introducing a
second progress format.

Required event types:

- `run_planned`
- `run_started`
- `model_started`
- `model_delta`
- `model_finished`
- `tool_started`
- `tool_finished`
- `verification_started`
- `verification_finished`
- `artifact_written`
- `run_interrupted`
- `run_failed`
- `run_finished`

Existing event types should be preserved for backward compatibility. New
commands should tolerate unknown event types.

Minimum event shape:

```json
{
  "timestamp_utc": "...",
  "event_type": "tool_finished",
  "problem_id": "example",
  "job_id": "example",
  "iteration": 8,
  "tool": "verify_proof_service",
  "ok": true
}
```

## Live Terminal Output

The default foreground output should be human-readable and compact:

```text
[14:50:18] model iteration 6 started: deepseek
[14:50:25] tool memory_append ok
[14:50:55] tool verify_proof_service started
[14:52:07] tool verify_proof_service ok
[14:52:39] verification correct
```

Model text should stream, but with boundaries:

- show model text chunks or full messages as they arrive
- never crash on console encoding errors
- keep the UTF-8 transcript in the log file even if stdout has replacement
  characters
- keep `--no-live-log` for quiet runs

For LiteLLM, prefer real provider streaming when stable:

```python
litellm.completion(..., stream=True)
```

The fallback remains message-level streaming: print each assistant message when
the model call returns. This is less responsive but still useful.

For Codex CLI, keep subprocess stdout passthrough, but also parse known status
lines into structured events when feasible.

## CLI Additions

Add these commands:

```bash
python -m rethlas.cli jobs
python -m rethlas.cli tail <problem-or-job>
python -m rethlas.cli watch <problem-or-job>
python -m rethlas.cli stop <problem-or-job>
python -m rethlas.cli resume <problem>
```

Extend existing commands:

```bash
python -m rethlas.cli run <problem> --background
python -m rethlas.cli run <problem> --json-events
python -m rethlas.cli status <problem> --watch
```

`--json-events` prints every event as JSONL to stdout for future UI integration.

## Stop Semantics

Stopping should be explicit and conservative:

1. Mark the job as `stopping`.
2. Send Ctrl+C / SIGINT to the child process group.
3. Wait a short grace period.
4. If still alive, send terminate.
5. If still alive, kill.
6. Append `run_interrupted` with the method used.

On Windows, start background jobs in a separate process group so they can be
interrupted as a unit. On Linux/macOS, use `start_new_session=True`.

No generated files should be deleted on stop.

## Sleep And Hibernation Behavior

Rethlas should not block sleep. Instead:

- all important progress must be written before and after model/tool calls
- status should say when the process is dead but the run did not finish
- HTTP timeout errors after wake should be recorded as `run_failed` or
  `verification_failed`
- `resume` should be the supported recovery action

Expected user-visible behavior after wake:

```text
process: not running
latest event: model_started iteration=9 at ...
latest error: runtime timed out / connection reset
draft blueprint: true
verified blueprint: false
suggested next command: python -m rethlas.cli resume example --model deepseek
```

## Results Page Integration

The browser results page should eventually show run status too, not only final
markdown.

First step:

- `results-site` reads latest status from `logs/{problem_id}/events.jsonl`
- index page shows:
  - verified/draft/missing badge
  - latest event
  - last update time

Later:

- auto-refresh index every few seconds
- show event timeline for each problem
- link to raw `blueprint.md`, `blueprint_verified.md`, and `events.jsonl`

## Implementation Stages

### Stage 1: Tail And Watch

Smallest useful increment.

- Add event-following helpers.
- Add `tail <problem>`.
- Add `watch <problem>`.
- Add tests with temporary `events.jsonl`.

Validation:

```bash
pytest -q
python -m rethlas.cli tail example
python -m rethlas.cli watch example
```

### Stage 2: Background Jobs

- Add file-backed job registry.
- Add `run --background`.
- Add `jobs`.
- Add `stop`.
- Add Windows and POSIX process-group handling.

Validation:

```bash
python -m rethlas.cli run example --model mock-generation --background
python -m rethlas.cli jobs
python -m rethlas.cli stop example
```

### Stage 3: Better Streaming

- Add structured `model_delta` events.
- Try LiteLLM provider streaming for native generation.
- Keep message-level fallback.
- Add `--json-events`.

Validation:

```bash
python -m rethlas.cli run example --model deepseek --json-events
```

### Stage 4: Resume

- Add `resume <problem>`.
- Prompt model to inspect existing memory/results/logs.
- Record `run_resumed` with previous status.

Validation:

```bash
python -m rethlas.cli stop example
python -m rethlas.cli resume example --model deepseek
```

### Stage 5: Results Page Status

- Add latest status badges to `results-site`.
- Add event timeline links.
- Keep generated viewer files ignored by git.

Validation:

```bash
python -m rethlas.cli results-site --sync-only
```

## Acceptance Criteria

- A user can start a long run without losing the terminal.
- A user can see live progress without opening raw logs.
- A user can stop a run intentionally and inspect what was preserved.
- A user can recover after sleep/hibernation by checking status and resuming.
- `blueprint_verified.md` remains the success signal.
- The implementation remains file-based and easy to debug.
