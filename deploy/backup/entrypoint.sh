#!/usr/bin/env bash
# Scheduled backups inside the `backup` service: every BACKUP_INTERVAL_HOURS (default 6) a verified,
# encrypted backup of the database + documents; once a day a full restore test.
set -uo pipefail
# Never wait for a password prompt (there is nobody to answer it): fail instead.
exec </dev/null
# libpq ignores a password file that others can read, and Docker Desktop on Windows/macOS mounts secrets
# with loose permissions, so use a private copy. The restore test also connects to scratch databases,
# so the copy applies the password to every database on the server (database field "*").
# PGPASSFILE points at that copy (also for commands run with `docker compose exec backup ...`).
SRC="${PGPASS_SECRET:-${PGPASSFILE:-}}"
if [[ -n "$SRC" && -r "$SRC" && "$SRC" != /tmp/pgpass ]]; then
  install -m 600 /dev/null /tmp/pgpass
  tr -d '\r' < "$SRC" | awk -F: 'BEGIN{OFS=":"} NF>=5 {$3="*"} {print}' > /tmp/pgpass
fi
export PGPASSFILE=/tmp/pgpass
INTERVAL=$(( ${BACKUP_INTERVAL_HOURS:-6} * 3600 ))
last_verify=0
while true; do
  if dir=$(/scripts/backup.sh /backups); then
    echo "$(date -u +%FT%TZ) backup ok: $dir"
    if (( $(date +%s) - last_verify > 86400 )); then
      if /scripts/verify_restore.sh "$dir"; then last_verify=$(date +%s); rm -f /backups/RESTORE_TEST_FAILED; echo "restore test ok"
      else echo "$(date -u +%FT%TZ) RESTORE TEST FAILED for $dir" >&2; touch /backups/RESTORE_TEST_FAILED; fi
    fi
  else
    echo "$(date -u +%FT%TZ) BACKUP FAILED" >&2
  fi
  sleep "$INTERVAL"
done
