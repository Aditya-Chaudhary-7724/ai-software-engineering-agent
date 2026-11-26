#!/usr/bin/env bash
# Restores a backup.sh dump.
#
#   restore.sh --verify  <dump>   restore into a SCRATCH database, compare
#                                 every table's row count with the live
#                                 database, drop the scratch database.
#                                 Changes nothing that serves traffic.
#   restore.sh --replace <dump>   REPLACE the live database with the dump:
#                                 stops frontend/backend, recreates the
#                                 database, restores, applies the schema
#                                 (idempotent), starts everything again.
#                                 Destructive; asks for the word "replace"
#                                 unless RESTORE_CONFIRM=replace.
#
# A dump from another server works the same way (scp it to the VM first).
source "$(dirname "$0")/lib.sh"

DB=ai_swe_agent
SCRATCH=ai_swe_agent_restore_check

mode="${1:-}"; dump="${2:-}"
if [[ "$mode" != "--verify" && "$mode" != "--replace" ]] || [[ -z "$dump" ]]; then
  sed -n '2,15p' "$0"; exit 2
fi
[[ -f "$dump" ]] || die "no such dump: $dump"
require_env_file
tag="$(current_tag)"
[[ -n "$tag" ]] || die "nothing deployed yet (no current tag)"

psql_c() { compose_tag "$tag" exec -T postgres psql -U ai_swe_agent -v ON_ERROR_STOP=1 -At "$@"; }

# "table rowcount" lines for every user table of database $1, sorted.
row_counts() {
  local tables t
  tables="$(psql_c -d "$1" -c "SELECT schemaname || '.' || tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1")"
  for t in $tables; do
    printf '%s %s\n' "$t" "$(psql_c -d "$1" -c "SELECT count(*) FROM $t")"
  done
}

restore_into() {
  local target="$1"
  compose_tag "$tag" exec -T postgres pg_restore -U ai_swe_agent -d "$target" --no-owner --exit-on-error < "$dump"
}

if [[ "$mode" == "--verify" ]]; then
  log "verifying $dump in scratch database $SCRATCH"
  psql_c -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null
  psql_c -d postgres -c "CREATE DATABASE $SCRATCH" >/dev/null
  trap 'psql_c -d postgres -c "DROP DATABASE IF EXISTS '"$SCRATCH"'" >/dev/null || true' EXIT
  restore_into "$SCRATCH"
  restored="$(row_counts "$SCRATCH")"
  live="$(row_counts "$DB")"
  printf '%s\n' "$restored" | sed 's/^/  restored: /'
  if [[ "$restored" == "$live" ]]; then
    log "VERIFIED: every table's row count matches the live database"
  else
    log "row counts differ from the live database (expected if data changed since the dump):"
    diff <(printf '%s\n' "$live") <(printf '%s\n' "$restored") || true
    exit 3
  fi
  exit 0
fi

if [[ "${RESTORE_CONFIRM:-}" != "replace" ]]; then
  read -r -p "This REPLACES the live database with $dump. Type 'replace' to continue: " answer
  [[ "$answer" == "replace" ]] || die "aborted"
fi
log "stopping frontend and backend"
compose_tag "$tag" stop frontend backend
log "recreating database $DB"
psql_c -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
psql_c -d postgres -c "CREATE DATABASE $DB" >/dev/null
psql_c -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
restore_into "$DB"
log "applying the schema (idempotent)"
compose_tag "$tag" run --rm --no-deps -T backend python scripts/apply_schema.py
log "starting everything"
compose_tag "$tag" up -d --wait --wait-timeout "${WAIT_TIMEOUT:-300}"
row_counts "$DB" | sed 's/^/  live: /'
log "restore complete"
