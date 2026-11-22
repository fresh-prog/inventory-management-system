#!/usr/bin/env bash
# Scheduled backups inside the `backup` service: every BACKUP_INTERVAL_HOURS (default 6) a verified,
# encrypted backup of the database + documents; once a day a full restore test.
set -uo pipefail
INTERVAL=$(( ${BACKUP_INTERVAL_HOURS:-6} * 3600 ))
last_verify=0
while true; do
  if dir=$(/scripts/backup.sh /backups); then
    echo "$(date -u +%FT%TZ) backup ok: $dir"
    if (( $(date +%s) - last_verify > 86400 )); then
      if /scripts/verify_restore.sh "$dir"; then last_verify=$(date +%s); echo "restore test ok"
      else echo "$(date -u +%FT%TZ) RESTORE TEST FAILED for $dir" >&2; touch /backups/RESTORE_TEST_FAILED; fi
    fi
  else
    echo "$(date -u +%FT%TZ) BACKUP FAILED" >&2
  fi
  sleep "$INTERVAL"
done
