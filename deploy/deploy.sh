#!/usr/bin/env bash
# Deploys one image tag (a git SHA) of the whole stack, or rolls back.
#
#   deploy.sh <git-sha>     deploy ghcr images tagged <git-sha>
#   deploy.sh --rollback    redeploy the previously deployed tag
#   deploy.sh --status      print the current and previous tags
#
# Order (docs/deployment.md section 14): databases up -> apply_schema.py
# with the NEW image -> check_production_config.py -> start everything and
# wait for health -> smoke test through Caddy. If anything after the schema
# step fails and a previous tag exists, the previous tag is started again
# and the script exits 1. The schema is additive only, so an older image
# keeps working against it.
#
# Environment: SKIP_PULL=1 uses local images (tests); SMOKE_INSECURE=1
# accepts Caddy's internal certificate (a localhost test, never production).
source "$(dirname "$0")/lib.sh"

SKIP_PULL="${SKIP_PULL:-0}"
SMOKE_INSECURE="${SMOKE_INSECURE:-0}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-300}"

smoke_test() {
  local site curl_opts=(-fsS --max-time 15 -o /dev/null -w '%{http_code}')
  site="$(env_get SITE_ADDRESS)"
  [[ -n "$site" ]] || die "SITE_ADDRESS is not set in prod.env"
  # Talk to Caddy on this host, with the real hostname (SNI and TLS) but
  # without depending on hairpin NAT or DNS.
  curl_opts+=(--resolve "$site:443:127.0.0.1")
  [[ "$SMOKE_INSECURE" == "1" ]] && curl_opts+=(-k)
  local path code
  for path in /health/live /health/ready /login; do
    code="$(curl "${curl_opts[@]}" "https://$site$path" || true)"
    [[ "$code" == "200" ]] || { log "smoke test: https://$site$path answered ${code:-nothing}"; return 1; }
  done
  log "smoke test passed: /health/live, /health/ready, /login over HTTPS"
}

start_tag() {
  local tag="$1"
  compose_tag "$tag" up -d --wait --wait-timeout "$WAIT_TIMEOUT" --remove-orphans
}

deploy() {
  local tag="$1" previous
  valid_tag "$tag" || die "not a git SHA: $tag"
  require_env_file
  mkdir -p "$STATE_DIR"
  previous="$(current_tag)"

  if [[ "$SKIP_PULL" != "1" ]]; then
    log "pulling images for $tag"
    if ! compose_tag "$tag" pull --quiet frontend backend caddy postgres neo4j; then
      # GHCR images are private; outside a CD run there is no registry login.
      # A tag deployed before is still cached here (rollback).
      local registry service
      registry="$(env_get IMAGE_REGISTRY)"
      for service in backend frontend; do
        docker image inspect "$registry/ai-swe-agent-$service:$tag" >/dev/null 2>&1 \
          || die "cannot pull $registry/ai-swe-agent-$service:$tag and it is not cached here (deploy through the CD workflow)"
      done
      log "pull failed; using the cached images for $tag"
    fi
  fi

  log "starting databases"
  compose_tag "$tag" up -d --wait --wait-timeout "$WAIT_TIMEOUT" postgres neo4j

  # The NEW image's schema, before the new image serves traffic. Indexing
  # only checks the schema version; it never applies it.
  log "applying the schema with $tag"
  compose_tag "$tag" run --rm --no-deps -T backend python scripts/apply_schema.py

  log "checking the production configuration"
  if ! compose_tag "$tag" run --rm --no-deps -T backend python scripts/check_production_config.py; then
    die "check_production_config.py failed; nothing was switched (still on ${previous:-nothing})"
  fi

  log "starting $tag"
  if start_tag "$tag" && smoke_test; then
    if [[ -n "$previous" && "$previous" != "$tag" ]]; then
      printf '%s\n' "$previous" > "$STATE_DIR/previous_tag"
    fi
    printf '%s\n' "$tag" > "$STATE_DIR/current_tag"
    log "deployed $tag (rollback target: $(previous_tag || true))"
    # Old images only; the running and previous tags stay cached.
    docker image prune -f >/dev/null || true
    return 0
  fi

  log "deploy of $tag FAILED"
  compose_tag "$tag" ps || true
  compose_tag "$tag" logs --tail 50 backend frontend caddy || true
  if [[ -n "$previous" && "$previous" != "$tag" ]]; then
    log "rolling back to $previous"
    if start_tag "$previous" && smoke_test; then
      log "rolled back to $previous"
    else
      log "ROLLBACK to $previous ALSO FAILED; the site may be down"
    fi
  fi
  exit 1
}

rollback() {
  local target current
  target="$(previous_tag)"
  current="$(current_tag)"
  [[ -n "$target" ]] || die "no previous tag recorded in $STATE_DIR"
  log "rolling back from ${current:-none} to $target"
  deploy "$target"
}

case "${1:-}" in
  --rollback) rollback ;;
  --status) printf 'current:  %s\nprevious: %s\n' "$(current_tag)" "$(previous_tag)" ;;
  ""|-h|--help) sed -n '2,8p' "$0"; exit 2 ;;
  *) deploy "$1" ;;
esac
