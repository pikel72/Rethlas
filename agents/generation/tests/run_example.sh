#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

problem="${PROBLEM_FILE:-example}"
model="${MODEL:-}"
dry_run="${DRY_RUN:-0}"

args=(run "$problem")
if [[ -n "$model" ]]; then
  args+=(--model "$model")
fi
if [[ "$dry_run" == "1" || "$dry_run" == "true" || "$dry_run" == "yes" ]]; then
  args+=(--dry-run)
fi

cd "$REPO_ROOT"
if command -v python3 >/dev/null 2>&1; then
  exec python3 -m rethlas.cli "${args[@]}"
fi
exec python -m rethlas.cli "${args[@]}"
