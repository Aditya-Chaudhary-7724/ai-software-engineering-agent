#!/usr/bin/env bash
# One-shot, idempotent setup of a fresh Ubuntu (arm64) VM for this stack.
# Safe to re-run: every step checks before it changes anything. Target:
# rebuild a reclaimed Always Free VM in well under an hour.
#
#   sudo ./bootstrap.sh [<git-sha>]
#
# Run from the directory holding the deploy/ files and prod.env (chmod 600),
# e.g. /opt/ai-swe-agent. With a git SHA it also deploys that tag, which
# needs those images cached here or a GHCR login; normally the first deploy
# is a manual run of the CD workflow instead (it logs the VM in to GHCR).
#
#  1. SSH: keys only (no passwords, no root login) — only if the login user
#     already has an authorized key, so it can never lock you out.
#  2. Unattended security upgrades.
#  3. Docker Engine + compose plugin from Docker's apt repository; log rotation.
#  4. Host firewall: allow 22, 80, 443 (Oracle's Ubuntu images ship iptables
#     rules that REJECT everything but SSH; ufw elsewhere). The cloud-side
#     security list must allow 80/443 too (owner checklist).
#  5. cron: DuckDNS update every 5 minutes, nightly PostgreSQL backup.
#  6. DuckDNS update now, then deploy.sh <git-sha> if given (schema,
#     config check, health checks, smoke test, rollback on failure).
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGIN_USER="${SUDO_USER:-ubuntu}"
LOGIN_HOME="$(getent passwd "$LOGIN_USER" | cut -d: -f6)"
log() { printf '[bootstrap] %s\n' "$*"; }

[[ "$(. /etc/os-release && echo "$ID")" == "ubuntu" ]] || { echo "Ubuntu only" >&2; exit 1; }
[[ -f "$DEPLOY_DIR/prod.env" ]] || { echo "copy prod.env to $DEPLOY_DIR first (chmod 600)" >&2; exit 1; }
chmod 600 "$DEPLOY_DIR/prod.env"
chown "$LOGIN_USER:" "$DEPLOY_DIR/prod.env"

export DEBIAN_FRONTEND=noninteractive

# 1. SSH hardening ------------------------------------------------------------
if [[ -s "$LOGIN_HOME/.ssh/authorized_keys" ]]; then
  conf=/etc/ssh/sshd_config.d/60-ai-swe-agent.conf
  want=$'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nPubkeyAuthentication yes'
  if [[ "$(cat "$conf" 2>/dev/null)" != "$want" ]]; then
    printf '%s\n' "$want" > "$conf"
    sshd -t && systemctl reload ssh
    log "ssh: keys only"
  fi
else
  log "ssh: WARNING $LOGIN_USER has no authorized key; password login left as is"
fi

# 2. Unattended upgrades ------------------------------------------------------
if ! dpkg -s unattended-upgrades >/dev/null 2>&1; then
  apt-get update -q && apt-get install -y -q unattended-upgrades
fi
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
apt-get install -y -q ca-certificates curl cron >/dev/null

# 3. Docker -------------------------------------------------------------------
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  log "docker installed"
fi
daemon='{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}}'
if [[ "$(cat /etc/docker/daemon.json 2>/dev/null)" != "$daemon" ]]; then
  printf '%s\n' "$daemon" > /etc/docker/daemon.json
  systemctl restart docker
fi
systemctl enable --now docker >/dev/null
id -nG "$LOGIN_USER" | grep -qw docker || { usermod -aG docker "$LOGIN_USER"; log "added $LOGIN_USER to the docker group (re-login to use it)"; }

# 4. Host firewall ------------------------------------------------------------
if [[ -f /etc/iptables/rules.v4 ]] && command -v netfilter-persistent >/dev/null; then
  for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
    # shellcheck disable=SC2086
    if ! iptables -C INPUT $rule -m state --state NEW -j ACCEPT 2>/dev/null; then
      # Before the image's catch-all REJECT, wherever it is; append if none.
      reject="$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" { print $1; exit }')"
      if [[ -n "$reject" ]]; then
        iptables -I INPUT "$reject" $rule -m state --state NEW -j ACCEPT
      else
        iptables -A INPUT $rule -m state --state NEW -j ACCEPT
      fi
    fi
  done
  netfilter-persistent save >/dev/null 2>&1
  log "iptables: 22/80/443 allowed (persisted)"
elif command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443 >/dev/null
  ufw --force enable >/dev/null
  log "ufw: 22/80/443 allowed"
fi

# 5. Layout and cron ----------------------------------------------------------
install -d -m 700 -o "$LOGIN_USER" "$DEPLOY_DIR/state" "$DEPLOY_DIR/backups"
chmod 755 "$DEPLOY_DIR"/*.sh
cat > /etc/cron.d/ai-swe-agent <<EOF
# Installed by $DEPLOY_DIR/bootstrap.sh
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root $DEPLOY_DIR/duckdns.sh >> /var/log/ai-swe-agent-duckdns.log 2>&1
17 3 * * * root $DEPLOY_DIR/backup.sh >> /var/log/ai-swe-agent-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/ai-swe-agent
cat > /etc/logrotate.d/ai-swe-agent <<'EOF'
/var/log/ai-swe-agent-*.log {
  weekly
  rotate 4
  compress
  missingok
  notifempty
}
EOF

# 6. DNS and deploy -----------------------------------------------------------
"$DEPLOY_DIR/duckdns.sh"
if [[ -n "${1:-}" ]]; then
  "$DEPLOY_DIR/deploy.sh" "$1"
else
  log "no tag given: not deploying (run ./deploy.sh <git-sha>)"
fi
log "done"
