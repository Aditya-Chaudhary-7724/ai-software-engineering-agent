# Shared helpers for the deploy scripts (sourced, never executed directly).
#
# Layout on the VM (bootstrap.sh creates it): every file of deploy/ is copied
# flat into DEPLOY_DIR (default: the directory of the calling script), next
# to prod.env (chmod 600), state/ (current and previous image tags) and
# backups/.
#
# Secrets: prod.env is never sourced into a shell (a value could contain
# shell syntax) and never printed. env_get reads one key at a time.

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "${BASH_SOURCE[1]:-$0}")" && pwd)}"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/prod.env}"
STATE_DIR="${STATE_DIR:-$DEPLOY_DIR/state}"
BACKUP_DIR="${BACKUP_DIR:-$DEPLOY_DIR/backups}"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Value of KEY in prod.env (last assignment wins, like docker compose), or
# empty. Surrounding quotes are stripped.
env_get() {
  local key="$1" line value
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  value="${line#*=}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

require_env_file() {
  [[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE (copy it from your machine; see docs/deployment.md)"
  local mode
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")"
  [[ "$mode" == "600" || "$mode" == "400" ]] || die "$ENV_FILE must be chmod 600 (it is $mode)"
}

valid_tag() { [[ "$1" =~ ^[0-9a-f]{7,40}$ ]]; }

# docker compose for this stack with IMAGE_TAG=$1.
compose_tag() {
  local tag="$1"; shift
  IMAGE_TAG="$tag" docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

current_tag() { cat "$STATE_DIR/current_tag" 2>/dev/null || true; }
previous_tag() { cat "$STATE_DIR/previous_tag" 2>/dev/null || true; }
