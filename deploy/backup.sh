#!/usr/bin/env bash
# Nightly PostgreSQL backup (cron, installed by bootstrap.sh).
#
#   backup.sh            dump, keep the newest BACKUP_KEEP (default 7) dumps
#                        locally, and upload off-VM if configured
#
# pg_dump -Fc runs inside the postgres container (read-only for the data).
# Off-VM copy (optional, recommended): set OFFSITE_BACKUP_PAR_URL in prod.env
# to an Oracle Object Storage pre-authenticated request URL for a bucket
# (write-only, "objects with prefix"; Always Free storage). The URL is a
# secret: it is passed to curl through a config file on stdin, never on the
# command line or in a log. Bucket-side retention: an Object Storage
# lifecycle rule (docs/deployment.md).
#
# What is NOT backed up: Neo4j and the cloned repositories. Both are derived
# data: re-indexing the demo repositories (seed_demo.py --reindex) rebuilds them.
source "$(dirname "$0")/lib.sh"

BACKUP_KEEP="${BACKUP_KEEP:-7}"

require_env_file
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

tag="$(current_tag)"
[[ -n "$tag" ]] || die "nothing deployed yet (no current tag)"

name="ai_swe_agent-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial="$BACKUP_DIR/.$name.partial"
umask 077
log "dumping PostgreSQL to $BACKUP_DIR/$name"
if ! compose_tag "$tag" exec -T postgres pg_dump -U ai_swe_agent -d ai_swe_agent -Fc > "$partial"; then
  rm -f "$partial"
  die "pg_dump failed"
fi
# A dump that pg_restore cannot list is not a backup.
if ! compose_tag "$tag" exec -T postgres pg_restore --list < "$partial" > /dev/null; then
  rm -f "$partial"
  die "the dump is not readable by pg_restore"
fi
mv "$partial" "$BACKUP_DIR/$name"
log "dump ok: $(du -h "$BACKUP_DIR/$name" | cut -f1)"

# Local rotation: newest BACKUP_KEEP dumps.
ls -1t "$BACKUP_DIR"/ai_swe_agent-*.dump 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | while read -r old; do
  rm -f -- "$old"
  log "rotated out $(basename "$old")"
done

par_url="$(env_get OFFSITE_BACKUP_PAR_URL)"
if [[ -z "$par_url" ]]; then
  log "off-VM copy: not configured (OFFSITE_BACKUP_PAR_URL unset); the dump exists on this VM only"
  exit 0
fi
[[ "$par_url" == https://*/ ]] || die "OFFSITE_BACKUP_PAR_URL must be an https URL ending in /"
if printf 'url = "%s%s"\nupload-file = "%s"\nfail\nsilent\nshow-error\nmax-time = 600\n' \
    "$par_url" "$name" "$BACKUP_DIR/$name" | curl -K - -o /dev/null; then
  log "off-VM copy: uploaded $name"
else
  die "off-VM upload failed (the local dump is kept)"
fi
