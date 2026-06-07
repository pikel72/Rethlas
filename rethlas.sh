#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print_header() {
  printf '\n%s\n' "$1"
  printf '%s\n' '------------------------------------------------------------'
}

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    printf '%s: %s\n' "$1" "$(command -v "$1")"
  else
    printf '%s: missing\n' "$1"
  fi
}

check_path() {
  local path="$1"
  local label="$2"
  if [[ -e "$ROOT_DIR/$path" ]]; then
    printf '%s: found\n' "$label"
  else
    printf '%s: missing (%s)\n' "$label" "$path"
  fi
}

pause_for_user() {
  printf '\nPress Enter to continue...'
  read -r _
}

doctor() {
  print_header "Doctor"
  check_command python3
  check_command python
  check_command codex
  check_command curl
  check_path "agents/generation/.venv/bin/python" "generation venv"
  check_path "agents/verification/.venv/bin/python" "verification venv"
  check_path "agents/generation/tests/run_example.sh" "generation runner"
  check_path "agents/verification/api/server.py" "verification API"
  printf '\n'
  python3 -m rethlas.cli doctor || python -m rethlas.cli doctor
  if curl -fsS --max-time 2 http://127.0.0.1:8091/health >/dev/null 2>&1; then
    printf 'verifier: reachable at http://127.0.0.1:8091\n'
  else
    printf 'verifier: not reachable at http://127.0.0.1:8091\n'
  fi
}

start_verifier() {
  print_header "Starting verification service"
  cd "$ROOT_DIR"
  python3 -m rethlas.cli verify-server || python -m rethlas.cli verify-server
}

ask_problem() {
  printf '\nEnter a problem id or path.\n'
  printf 'Examples:\n'
  printf '  example\n'
  printf '  ns/ns\n'
  printf '  data/modrep/modrep.md\n\n'
  printf 'Problem: '
  read -r problem
  if [[ -z "$problem" ]]; then
    problem="example"
  fi
}

run_problem() {
  local problem="$1"
  print_header "Running $problem"
  cd "$ROOT_DIR"
  python3 -m rethlas.cli run "$problem" || python -m rethlas.cli run "$problem"
}

dry_run_problem() {
  local problem="$1"
  print_header "Dry run $problem"
  cd "$ROOT_DIR"
  python3 -m rethlas.cli run "$problem" --dry-run || python -m rethlas.cli run "$problem" --dry-run
}

while true; do
  clear || true
  printf 'Rethlas launcher\n'
  printf '=================\n\n'
  printf '  1. Doctor\n'
  printf '  2. Start verification service\n'
  printf '  3. Run included example\n'
  printf '  4. Run a problem\n'
  printf '  5. Dry-run a problem\n'
  printf '  0. Exit\n\n'
  printf 'Choose an option: '
  read -r choice

  case "$choice" in
    1)
      doctor
      pause_for_user
      ;;
    2)
      start_verifier
      ;;
    3)
      run_problem "example"
      pause_for_user
      ;;
    4)
      ask_problem
      run_problem "$problem"
      pause_for_user
      ;;
    5)
      ask_problem
      dry_run_problem "$problem"
      pause_for_user
      ;;
    0)
      exit 0
      ;;
    *)
      printf '\nUnknown option: %s\n' "$choice"
      pause_for_user
      ;;
  esac
done
