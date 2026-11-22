#!/usr/bin/env bash
# Restore a backup into a NEW database (never overwrites the live one).
# Usage: scripts/restore.sh <backup file> <new_database_name>
# Then point APP_DATABASE_URL at the new database, start the app and run /api/v1/reports/integrity.
set -euo pipefail
FILE="$1"; TARGET="$2"
if [[ "$FILE" == *.enc ]]; then
  : "${BACKUP_PASSPHRASE_FILE:?set BACKUP_PASSPHRASE_FILE to decrypt}"
  PLAIN="$(mktemp)"; trap 'rm -f "$PLAIN"' EXIT
  openssl enc -d -aes-256-cbc -pbkdf2 -in "$FILE" -out "$PLAIN" -pass "file:$BACKUP_PASSPHRASE_FILE"
  FILE="$PLAIN"
elif [[ -f "$FILE.sha256" ]]; then
  sha256sum --check --quiet "$FILE.sha256"
fi
if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$TARGET'" | grep -q 1; then
  echo "Database $TARGET already exists — choose a new name." >&2; exit 1
fi
createdb "$TARGET"
pg_restore -l "$FILE" | grep -vE "EXTENSION - (pg_stat_statements|amcheck)|COMMENT - EXTENSION (pg_stat_statements|amcheck)" > "$FILE.toc"
pg_restore --no-owner --exit-on-error -L "$FILE.toc" --dbname="$TARGET" "$FILE"
rm -f "$FILE.toc"
echo "Restored into $TARGET"
