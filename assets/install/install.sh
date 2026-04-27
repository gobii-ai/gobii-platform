#!/usr/bin/env bash

set -euo pipefail

INSTALLER_VERSION="1.0.0"
REPO_URL="https://github.com/gobii-ai/gobii-platform.git"
INSTALL_DIR="${GOBII_INSTALL_DIR:-$HOME/gobii-platform}"
REQUESTED_REF="${GOBII_REF:-}"

log() {
  printf '==> %s\n' "$*"
}

warn() {
  printf 'Warning: %s\n' "$*" >&2
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

validate_platform() {
  case "$(uname -s)" in
    Darwin|Linux)
      ;;
    *)
      die "Gobii's installer currently supports macOS and Linux only."
      ;;
  esac
}

validate_prerequisites() {
  require_command curl
  require_command git
  require_command docker

  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required. Install the Docker Compose plugin and try again."
  docker info >/dev/null 2>&1 || die "Docker is installed but the daemon is not reachable. Start Docker Desktop or Docker Engine and retry."
}

is_supported_origin() {
  local normalized
  normalized="$1"
  normalized="${normalized#ssh://}"
  normalized="${normalized#git://}"
  normalized="${normalized#https://}"
  normalized="${normalized#http://}"
  normalized="${normalized#git@}"
  normalized="${normalized#www.}"

  case "$normalized" in
    github.com:*)
      normalized="github.com/${normalized#github.com:}"
      ;;
  esac

  while [ "${normalized%/}" != "$normalized" ]; do
    normalized="${normalized%/}"
  done

  normalized="${normalized%.git}"

  while [ "${normalized%/}" != "$normalized" ]; do
    normalized="${normalized%/}"
  done

  [ "$normalized" = "github.com/gobii-ai/gobii-platform" ]
}

ensure_install_checkout() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    local origin_url
    origin_url="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
    [ -n "$origin_url" ] || die "Existing checkout at $INSTALL_DIR has no origin remote."
    is_supported_origin "$origin_url" || die "Refusing to reuse $INSTALL_DIR because its origin remote is $origin_url."

    if [ -n "$(git -C "$INSTALL_DIR" status --porcelain -uno)" ]; then
      die "Existing checkout at $INSTALL_DIR has local changes. Commit, stash, or use GOBII_INSTALL_DIR."
    fi

    log "Updating existing Gobii checkout in $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch origin --tags --prune
    return
  fi

  [ ! -e "$INSTALL_DIR" ] || die "$INSTALL_DIR exists but is not a Gobii Git checkout. Move it aside or set GOBII_INSTALL_DIR."

  mkdir -p "$(dirname "$INSTALL_DIR")"
  log "Cloning Gobii into $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch origin --tags --prune
}

resolve_ref() {
  if [ -n "$REQUESTED_REF" ]; then
    printf '%s' "$REQUESTED_REF"
    return
  fi

  local latest_tag
  latest_tag="$(
    {
      git -C "$INSTALL_DIR" ls-remote --sort='-version:refname' --refs --tags origin 2>/dev/null || true
    } | sed -n '1s#.*refs/tags/##p'
  )"
  if [ -z "$latest_tag" ]; then
    latest_tag="$(git -C "$INSTALL_DIR" tag --sort=-version:refname | head -n 1)"
  fi
  [ -n "$latest_tag" ] || die "Could not determine the latest Gobii release tag."
  printf '%s' "$latest_tag"
}

checkout_ref() {
  local ref="$1"
  local target="$ref"

  if git -C "$INSTALL_DIR" show-ref --verify --quiet "refs/tags/$ref"; then
    target="refs/tags/$ref"
  elif git -C "$INSTALL_DIR" show-ref --verify --quiet "refs/remotes/origin/$ref"; then
    target="refs/remotes/origin/$ref"
  elif ! git -C "$INSTALL_DIR" rev-parse --verify --quiet "${ref}^{commit}" >/dev/null; then
    die "Could not resolve GOBII_REF=$ref. Use a release tag, branch, or reachable commit."
  fi

  log "Checking out $ref"
  git -C "$INSTALL_DIR" checkout --detach "$target" >/dev/null
}

start_stack() {
  [ -f "$INSTALL_DIR/compose.yaml" ] || die "Expected compose.yaml in $INSTALL_DIR after checkout."

  log "Starting Gobii with Docker Compose"
  (
    cd "$INSTALL_DIR"
    docker compose up --build -d
  )
}

print_success() {
  local ref="$1"

  cat <<EOF

Gobii installer $INSTALLER_VERSION finished successfully.

Installed checkout: $INSTALL_DIR
Active ref: $ref
Open Gobii: http://localhost:8000

Useful commands:
  cd "$INSTALL_DIR" && docker compose logs -f
  cd "$INSTALL_DIR" && docker compose down
  cd "$INSTALL_DIR" && docker compose --profile beat up --build -d
  cd "$INSTALL_DIR" && docker compose --profile email up --build -d
  cd "$INSTALL_DIR" && docker compose --profile obs up --build -d
EOF
}

main() {
  validate_platform
  log "Starting Gobii installer $INSTALLER_VERSION"
  validate_prerequisites
  ensure_install_checkout

  local ref
  ref="$(resolve_ref)"

  if [ -z "$REQUESTED_REF" ]; then
    log "Using latest tagged Gobii release: $ref"
  else
    warn "Using overridden Gobii ref: $ref"
  fi

  checkout_ref "$ref"
  start_stack
  print_success "$ref"
}

main "$@"
