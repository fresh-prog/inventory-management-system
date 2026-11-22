#!/usr/bin/env bash
# Backup = database dump + documents (receipts), verified, optionally encrypted and copied off-site.
#
#   scripts/backup.sh [output_dir]
#
# Environment: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE (database to back up)
#   BACKUP_FILES_DIR        documents directory to include (e.g. /data/files)          [optional]
#   BACKUP_PASSPHRASE_FILE  encrypt with AES-256 (openssl, PBKDF2); keep the passphrase off this server [recommended]
#   BACKUP_OFFSITE_DIR      copy finished backups here (mounted USB disk / network share) [optional]
#   BACKUP_OFFSITE_CMD      or run this command with the backup directory as $1 (e.g. "rclone copy $1 remote:kl") [optional]
#   BACKUP_KEEP             number of local backups to keep (default 30)
set -euo pipefail
OUT_DIR="${1:-./backups}"
DB="${PGDATABASE:-restaurant}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DIR="$OUT_DIR/$DB-$STAMP"
mkdir -p "$DIR"
start=$(date +%s)

pg_dump --format=custom --compress=6 --no-owner --file="$DIR/db.dump" "$DB"
pg_restore --list "$DIR/db.dump" >/dev/null                       # archive is readable
if [[ -n "${BACKUP_FILES_DIR:-}" && -d "${BACKUP_FILES_DIR}" ]]; then
  tar -C "$BACKUP_FILES_DIR" -czf "$DIR/files.tar.gz" .
  tar -tzf "$DIR/files.tar.gz" >/dev/null                         # archive is readable
fi
if [[ -n "${BACKUP_PASSPHRASE_FILE:-}" ]]; then
  for f in "$DIR"/db.dump "$DIR"/files.tar.gz; do
    [[ -f "$f" ]] || continue
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$f" -out "$f.enc" -pass "file:$BACKUP_PASSPHRASE_FILE"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$f.enc" -pass "file:$BACKUP_PASSPHRASE_FILE" | cmp -s - "$f"  # decrypts back
    rm -f "$f"
  done
fi
( cd "$DIR" && sha256sum ./* > SHA256SUMS )
cat > "$DIR/MANIFEST" <<MAN
database=$DB
created_utc=$STAMP
host=$(hostname)
encrypted=$([[ -n "${BACKUP_PASSPHRASE_FILE:-}" ]] && echo yes || echo no)
schema_revision=$(psql -tAc "SELECT version_num FROM alembic_version" "$DB" 2>/dev/null || echo unknown)
seconds=$(( $(date +%s) - start ))
MAN

if [[ -n "${BACKUP_OFFSITE_DIR:-}" ]]; then
  mkdir -p "$BACKUP_OFFSITE_DIR"
  cp -r "$DIR" "$BACKUP_OFFSITE_DIR/"
  ( cd "$BACKUP_OFFSITE_DIR/$(basename "$DIR")" && sha256sum --check --quiet SHA256SUMS )   # verified copy
fi
if [[ -n "${BACKUP_OFFSITE_CMD:-}" ]]; then
  sh -c "$BACKUP_OFFSITE_CMD" -- "$DIR"
fi
# retention (local only; off-site retention is managed at the destination)
ls -1dt "$OUT_DIR"/"$DB"-*/ 2>/dev/null | tail -n +$(( ${BACKUP_KEEP:-30} + 1 )) | while read -r old; do
  case "$old" in "$OUT_DIR"/"$DB"-*) rm -r -- "$old" ;; esac
done
echo "$DIR"
