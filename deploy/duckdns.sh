#!/usr/bin/env bash
# Points the DuckDNS hostname at this VM's public IP (cron every 5 minutes,
# installed by bootstrap.sh). DuckDNS detects the caller's IP when `ip=` is
# empty. The token is a secret: it goes to curl through a config file on
# stdin (not argv, not a log); only DuckDNS's OK/KO answer is printed.
#
# prod.env: DUCKDNS_SUBDOMAIN (the part before .duckdns.org), DUCKDNS_TOKEN.
source "$(dirname "$0")/lib.sh"

require_env_file
subdomain="$(env_get DUCKDNS_SUBDOMAIN)"
token="$(env_get DUCKDNS_TOKEN)"
[[ "$subdomain" =~ ^[a-z0-9-]{1,63}$ ]] || die "DUCKDNS_SUBDOMAIN must be the bare name, e.g. myname (not myname.duckdns.org)"
[[ "$token" =~ ^[0-9a-f-]{36}$ ]] || die "DUCKDNS_TOKEN is missing or not a DuckDNS token"

answer="$(printf 'url = "https://www.duckdns.org/update?domains=%s&token=%s&ip="\nsilent\nmax-time = 20\n' "$subdomain" "$token" | curl -K - || true)"
if [[ "$answer" == "OK" ]]; then
  log "duckdns: $subdomain.duckdns.org updated"
else
  die "duckdns: update refused (answer: ${answer:-none})"
fi
