#!/usr/bin/env bash
# Consistent online backup of the branch database (pg_dump custom format, single snapshot).
# Optional encryption: set BACKUP_PASSPHRASE_FILE to a file holding a passphrase (uses openssl AES-256).
# Usage: scripts/backup.sh [output_dir]      (reads PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE)
set -euo pipefail
OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT_DIR/${PGDATABASE:-restaurant}-$STAMP.dump"
pg_dump --format=custom --compress=6 --no-owner --file="$FILE" "${PGDATABASE:-restaurant}"
pg_restore --list "$FILE" >/dev/null   # verify the archive is readable
sha256sum "$FILE" > "$FILE.sha256"
if [[ -n "${BACKUP_PASSPHRASE_FILE:-}" ]]; then
  openssl enc -aes-256-cbc -pbkdf2 -salt -in "$FILE" -out "$FILE.enc" -pass "file:$BACKUP_PASSPHRASE_FILE"
  rm -f "$FILE"; FILE="$FILE.enc"
fi
# keep the 30 most recent backups
ls -1t "$OUT_DIR"/*.dump* 2>/dev/null | grep -v sha256 | tail -n +31 | xargs -r rm -f
echo "$FILE"
