#!/usr/bin/env bash
# Creates (or completes) the production env file on the OPERATOR'S machine,
# outside the repository, and checks it. Never prints a value.
#
#   deploy/make-prod-env.sh            create/complete ~/.config/ai-swe-agent/prod.env
#   deploy/make-prod-env.sh --check    list what is still missing (names only)
#   deploy/make-prod-env.sh --enable-demo 1,2,3
#                                      after seed_demo.py: set DEMO_REPOSITORY_IDS
#                                      and generate DEMO_API_SERVICE_TOKEN
#
# Demo mode stays OFF until --enable-demo: check_production_config.py (run by
# every deploy) refuses a demo token without demo repositories, and the
# repositories only exist after the first deploy has indexed them.
#
# Generates every value nobody needs to know (database passwords, service
# tokens, the session secret) with the OS's CSPRNG, only where the key is
# still empty, so re-running never rotates an existing secret. The values
# you must supply (Groq key and model ids, DuckDNS, the operator password
# hash) are left for you to paste in with an editor.
set -euo pipefail

TARGET="${PROD_ENV:-$HOME/.config/ai-swe-agent/prod.env}"
TEMPLATE="$(cd "$(dirname "$0")" && pwd)/prod.env.example"
GENERATED=(POSTGRES_PASSWORD NEO4J_PASSWORD API_SERVICE_TOKEN SESSION_SECRET)
REQUIRED=(SITE_ADDRESS DUCKDNS_SUBDOMAIN DUCKDNS_TOKEN IMAGE_REGISTRY "${GENERATED[@]}" OPERATOR_PASSWORD_HASH GROQ_API_KEY LLM_MODEL DEMO_LLM_MODEL)

value_of() { grep -E "^$1=" "$TARGET" | tail -n 1 | cut -d= -f2- || true; }

# Sets KEY=VALUE in place (first occurrence; appended if absent), mode 600.
set_value() {
  local tmp
  tmp="$(mktemp "$TARGET.XXXXXX")"
  awk -v k="$1" -v v="$2" 'BEGIN { done = 0 } $0 ~ "^" k "=" && !done { print k "=" v; done = 1; next } { print } END { if (!done) print k "=" v }' "$TARGET" > "$tmp"
  mv "$tmp" "$TARGET"
  chmod 600 "$TARGET"
}

# base64url, no padding: no `$`, quotes or spaces; 64 characters.
new_secret() { openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n'; }

check() {
  local missing=() key
  for key in "${REQUIRED[@]}"; do [[ -n "$(value_of "$key")" ]] || missing+=("$key"); done
  local mode; mode="$(stat -f '%Lp' "$TARGET" 2>/dev/null || stat -c '%a' "$TARGET")"
  [[ "$mode" == "600" ]] || echo "WARNING: $TARGET is mode $mode, expected 600"
  local llm demo site
  llm="$(value_of LLM_MODEL)"; demo="$(value_of DEMO_LLM_MODEL)"; site="$(value_of SITE_ADDRESS)"
  [[ -z "$llm" || "$llm" != "$demo" ]] || echo "PROBLEM: DEMO_LLM_MODEL must differ from LLM_MODEL"
  [[ -z "$site" || "$site" == *.duckdns.org ]] || echo "NOTE: SITE_ADDRESS is not a duckdns.org name"
  [[ -z "$site" || "$site" == "$(value_of DUCKDNS_SUBDOMAIN).duckdns.org" ]] || echo "PROBLEM: SITE_ADDRESS should be <DUCKDNS_SUBDOMAIN>.duckdns.org"
  if grep -qE '^[A-Z_]+=.*\$' "$TARGET"; then echo "PROBLEM: a value contains \$ (docker compose would interpolate it)"; fi
  if ((${#missing[@]})); then
    echo "Still missing: ${missing[*]}"
    return 1
  fi
  echo "OK: every required key is set in $TARGET"
}

if [[ "${1:-}" == "--enable-demo" ]]; then
  ids="${2:-}"
  [[ "$ids" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]] || { echo "usage: $0 --enable-demo 1,2,3"; exit 2; }
  [[ -f "$TARGET" ]] || { echo "no $TARGET yet"; exit 1; }
  set_value DEMO_REPOSITORY_IDS "$ids"
  if [[ -z "$(value_of DEMO_API_SERVICE_TOKEN)" ]]; then set_value DEMO_API_SERVICE_TOKEN "$(new_secret)"; echo "generated DEMO_API_SERVICE_TOKEN"; fi
  echo "demo mode configured for repositories $ids (copy the file to the VM and redeploy)"
  exit 0
fi

if [[ "${1:-}" == "--check" ]]; then
  [[ -f "$TARGET" ]] || { echo "no $TARGET yet; run without --check first"; exit 1; }
  check
  exit $?
fi

umask 077
mkdir -p "$(dirname "$TARGET")"
chmod 700 "$(dirname "$TARGET")"
[[ -f "$TARGET" ]] || cp "$TEMPLATE" "$TARGET"
chmod 600 "$TARGET"

for key in "${GENERATED[@]}"; do
  [[ -z "$(value_of "$key")" ]] || continue
  set_value "$key" "$(new_secret)"
  echo "generated $key"
done
check || true
