#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage:
  scripts/test_owner_execution_pause.sh show --user-email you@example.com
  scripts/test_owner_execution_pause.sh pause --user-email you@example.com [--reason manual_local_test]
  scripts/test_owner_execution_pause.sh resume --user-email you@example.com
  scripts/test_owner_execution_pause.sh walkthrough --user-email you@example.com [--reason manual_local_test]

Owner selectors:
  --user-id <id>
  --user-email <email>
  --org-id <id>
  --org-slug <slug>

Notes:
  - This script wraps: uv run python manage.py owner_execution_pause ...
  - `walkthrough` pauses with `--skip-cleanup` by default so you can verify
    "current work may finish, but no new work starts" without intentionally
    shutting down an in-flight agent.
EOF
}

has_flag() {
  local needle="$1"
  shift
  local arg
  for arg in "$@"; do
    if [[ "$arg" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

run_manage() {
  UV_CACHE_DIR=.uv-cache uv run python manage.py owner_execution_pause "$@"
}

print_pause_checks() {
  local selector_args=("$@")

  echo
  echo "Manual checks while paused:"
  echo "1. If an agent run was already in progress before pause, verify it is allowed to finish."
  echo "2. Try to start new work:"
  echo "   - send the agent a new inbound message"
  echo "   - trigger a cron tick if the agent has cron"
  echo "   - create a browser task"
  echo "3. Expected result:"
  echo "   - no new agent work starts"
  echo "   - browser task creation is rejected, or a queued task cancels before start"
  echo "4. When you are done, resume with:"
  printf '   scripts/test_owner_execution_pause.sh resume'
  printf ' %q' "${selector_args[@]}"
  printf ' --skip-enqueue\n'
}

ACTION="${1:-}"
if [[ -z "$ACTION" ]]; then
  usage
  exit 1
fi
shift || true

case "$ACTION" in
  show)
    run_manage show "$@"
    ;;
  pause)
    run_manage pause "$@"
    print_pause_checks "$@"
    ;;
  resume)
    run_manage resume "$@"
    echo
    echo "Manual checks after resume:"
    echo "1. Trigger fresh work again."
    echo "2. Expected result: the agent or browser task is admitted normally."
    ;;
  walkthrough)
    echo "Current state:"
    run_manage show "$@"
    echo
    echo "Pausing owner for local testing..."

    pause_args=("$@")
    if ! has_flag --skip-cleanup "${pause_args[@]}"; then
      pause_args+=(--skip-cleanup)
    fi
    if ! has_flag --reason "${pause_args[@]}"; then
      pause_args+=(--reason "manual_local_test")
    fi

    run_manage pause "${pause_args[@]}"
    print_pause_checks "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    echo >&2
    usage
    exit 1
    ;;
esac
