#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBLEM_FILE="${PROBLEM_FILE:-data/example.md}"
MODEL="${MODEL:-gpt-5.5}"
REASONING_EFFORT="${REASONING_EFFORT:-xhigh}"
DRY_RUN="${DRY_RUN:-0}"

PROBLEM_FILE="${PROBLEM_FILE//\\//}"

if [[ "$PROBLEM_FILE" = /* ]]; then
  echo "PROBLEM_FILE must be relative to agents/generation: $PROBLEM_FILE" >&2
  exit 1
fi

if [[ "$PROBLEM_FILE" == ".." || "$PROBLEM_FILE" == ../* || "$PROBLEM_FILE" == */.. || "$PROBLEM_FILE" == */../* ]]; then
  echo "PROBLEM_FILE must not contain '..': $PROBLEM_FILE" >&2
  exit 1
fi

if [[ "$PROBLEM_FILE" != data/* ]]; then
  PROBLEM_FILE="data/$PROBLEM_FILE"
fi

if [[ "$PROBLEM_FILE" != *.md ]]; then
  problem_name="$(basename "$PROBLEM_FILE")"
  if [[ "$problem_name" == *.* ]]; then
    echo "PROBLEM_FILE must point to a markdown file under data/: $PROBLEM_FILE" >&2
    exit 1
  fi
  PROBLEM_FILE="${PROBLEM_FILE}.md"
fi

if [[ "$PROBLEM_FILE" != data/*.md ]]; then
  echo "PROBLEM_FILE must point to a markdown file under data/: $PROBLEM_FILE" >&2
  exit 1
fi

if [[ ! -f "$ROOT_DIR/$PROBLEM_FILE" ]]; then
  echo "Problem file not found: $ROOT_DIR/$PROBLEM_FILE" >&2
  exit 1
fi

# data/algebra/prob1.md → algebra/prob1
problem_rel="${PROBLEM_FILE#data/}"
problem_rel="${problem_rel%.md}"
problem_id="$(basename "$PROBLEM_FILE" .md)"
ref_dir="data/${problem_rel}.refs"
ref_prompt="Use reference_dir=${ref_dir} if it exists."

prepare_references() {
  local abs_ref_dir="$ROOT_DIR/$ref_dir"
  if [[ ! -d "$abs_ref_dir" ]]; then
    return
  fi

  local pdf_count=0
  while IFS= read -r -d '' pdf; do
    pdf_count=$((pdf_count + 1))
    if ! command -v pdftotext >/dev/null 2>&1; then
      echo "WARNING: found PDF references, but pdftotext is not installed; PDFs will be ignored." >&2
      return
    fi

    local rel_pdf="${pdf#"$abs_ref_dir"/}"
    local txt="$abs_ref_dir/.extracted/${rel_pdf%.pdf}.txt"
    mkdir -p "$(dirname "$txt")"
    if [[ ! -f "$txt" || "$pdf" -nt "$txt" ]]; then
      pdftotext -layout "$pdf" "$txt"
    fi
  done < <(find "$abs_ref_dir" -type f -iname '*.pdf' -not -path "$abs_ref_dir/.extracted/*" -print0)

  if [[ $pdf_count -gt 0 ]]; then
    ref_prompt="Use reference_dir=${ref_dir} if it exists. PDF references have been extracted to ${ref_dir}/.extracted; read those extracted .txt files instead of the PDFs."
  fi
}

prepare_references

LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/$problem_rel}"
mkdir -p "$LOG_DIR"

log_file="$LOG_DIR/${problem_id}.md"
prompt="Use AGENTS.md exactly to solve the math problem in ${PROBLEM_FILE}. Use problem_id=${problem_rel}. ${ref_prompt}"

CODEX_VERSION="$(codex --version 2>/dev/null || echo 'unknown')"

echo "========================================"
echo " Codex:    $CODEX_VERSION"
echo " Model:    $MODEL"
echo " Effort:   $REASONING_EFFORT"
echo " Problem:  $PROBLEM_FILE"
echo " Problem ID: $problem_rel"
echo " References: $ref_dir"
echo " Log:      $log_file"
echo "========================================"
echo ""
echo "Running ${PROBLEM_FILE} -> $log_file"

START_EPOCH=$(date +%s)

elapsed_timer() {
  while true; do
    sleep 30
    local now=$(date +%s)
    local secs=$((now - START_EPOCH))
    printf "\r  [elapsed %02d:%02d:%02d] still running..." \
      $((secs/3600)) $(((secs%3600)/60)) $((secs%60))
  done
}
elapsed_timer &
TIMER_PID=$!
cleanup_timer() {
  kill "$TIMER_PID" 2>/dev/null || true
  wait "$TIMER_PID" 2>/dev/null || true
}
trap cleanup_timer EXIT

VERIFY_URL="${VERIFY_URL:-http://127.0.0.1:8091/health}"
if ! curl -sf "$VERIFY_URL" >/dev/null 2>&1; then
  echo "WARNING: verification service not reachable at ${VERIFY_URL%%/health*}"
  echo "         The agent will skip proof verification."
  echo "         Start it first if you need verified proofs."
  echo ""
fi

if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" || "$DRY_RUN" == "yes" ]]; then
  echo "Dry run only. Codex command:"
  printf '  codex exec -C %q -m %q --config %q --dangerously-bypass-approvals-and-sandbox %q\n' \
    "$ROOT_DIR" \
    "$MODEL" \
    "model_reasoning_effort=\"$REASONING_EFFORT\"" \
    "$prompt"
  exit 0
fi

codex_rc=0
(
  cd "$ROOT_DIR"
  codex exec \
    -C "$ROOT_DIR" \
    -m "$MODEL" \
    --config "model_reasoning_effort=\"$REASONING_EFFORT\"" \
    --dangerously-bypass-approvals-and-sandbox \
    "$prompt"
) >"$log_file" 2>&1 || codex_rc=$?

cleanup_timer
trap - EXIT

END_EPOCH=$(date +%s)
TOTAL=$((END_EPOCH - START_EPOCH))
printf "\n"

if [[ $codex_rc -ne 0 ]]; then
  echo "codex exited with code $codex_rc (see $log_file for details)"
fi

echo "Finished ${PROBLEM_FILE} -> $log_file"
printf "Total time: %02d:%02d:%02d\n" \
  $((TOTAL/3600)) $(((TOTAL%3600)/60)) $((TOTAL%60))
echo ""
echo "To view results in the browser, run:"
echo "  ./site/serve.sh"
echo "Then open http://localhost:3264"
