#!/usr/bin/env bash
# Restore a backup into a THROWAWAY database and prove it is usable. Never touches the live DB.
#   scripts/verify_restore.sh <backup_dir>
# Checks: checksums · decrypt · pg_restore · schema revision · physical index integrity (amcheck)
#         · business integrity (scripts/sql/integrity.sql) · documents archive · row counts.
# Every check is explicit: any failure prints FAILED and exits non-zero.
set -uo pipefail
DIR="$1"; HERE="$(cd "$(dirname "$0")" && pwd)"
TMPDB="verify_$(date +%s)_$$"
WORK="$(mktemp -d)"
cleanup() { rm -r -- "$WORK"; dropdb -w --if-exists "$TMPDB" >/dev/null 2>&1 || true; }
trap cleanup EXIT
fail() { echo "FAILED: $*"; exit 1; }
start=$(date +%s)

[[ -f "$DIR/SHA256SUMS" ]] || fail "no SHA256SUMS in $DIR"
( cd "$DIR" && sha256sum --check --quiet SHA256SUMS ) || fail "checksum mismatch (backup damaged or tampered)"
echo "checksums: ok"

dump="$DIR/db.dump"
if [[ -f "$DIR/db.dump.enc" ]]; then
  [[ -n "${BACKUP_PASSPHRASE_FILE:-}" ]] || fail "encrypted backup: set BACKUP_PASSPHRASE_FILE"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$DIR/db.dump.enc" -out "$WORK/db.dump" \
    -pass "file:$BACKUP_PASSPHRASE_FILE" || fail "cannot decrypt the database dump"
  dump="$WORK/db.dump"
  echo "decrypt: ok"
fi
[[ -f "$dump" ]] || fail "no database dump"

createdb -w "$TMPDB" || fail "cannot create scratch database"
# monitoring-only extensions (e.g. pg_stat_statements) need a superuser and hold no business data
pg_restore -l "$dump" | grep -vE "EXTENSION - (pg_stat_statements|amcheck)|COMMENT - EXTENSION (pg_stat_statements|amcheck)" > "$WORK/toc"
pg_restore --no-owner --exit-on-error -L "$WORK/toc" --dbname="$TMPDB" "$dump" || fail "pg_restore"
echo "restore: ok"
# A cloud database holds the control plane plus one replica schema per branch (br_<id>), each with the
# branch's own tables: every replica gets the same integrity checks as a branch database.
if [[ "$(psql -tAc "SELECT to_regclass('public.alembic_version_cloud') IS NOT NULL" "$TMPDB")" == "t" ]]; then
  rev=$(psql -tAc 'SELECT version_num FROM alembic_version_cloud' "$TMPDB") || fail "no cloud schema version"
  mapfile -t SCHEMAS < <(psql -tAc "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'br\_%' ORDER BY 1" "$TMPDB")
  echo "schema revision: cloud $rev, ${#SCHEMAS[@]} branch replica(s)"
else
  rev=$(psql -tAc 'SELECT version_num FROM alembic_version' "$TMPDB") || fail "no schema version"
  SCHEMAS=(public)
  echo "schema revision: $rev"
fi

if psql -qc "CREATE EXTENSION IF NOT EXISTS amcheck" "$TMPDB" >/dev/null 2>&1; then
  psql -qtAc "SELECT bt_index_check(c.oid) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
              JOIN pg_am am ON am.oid = c.relam WHERE am.amname = 'btree' AND c.relpersistence = 'p'" "$TMPDB" >/dev/null \
    || fail "index corruption detected (amcheck)"
  echo "index integrity (amcheck): ok"
else
  echo "index integrity (amcheck): skipped (requires a superuser connection)"
fi

for sch in "${SCHEMAS[@]}"; do
  out=$(PGOPTIONS="-c search_path=$sch" psql -tA -v ON_ERROR_STOP=1 -f "$HERE/sql/integrity.sql" "$TMPDB") \
    || fail "integrity query error ($sch)"
  if [[ -n "$out" ]]; then echo "$out" | head -20; fail "business integrity ($sch): $(echo "$out" | wc -l) violation(s)"; fi
done
echo "business integrity: ok (0 violations in ${#SCHEMAS[@]} schema(s))"

for f in files.tar.gz files.tar.gz.enc; do
  [[ -f "$DIR/$f" ]] || continue
  if [[ "$f" == *.enc ]]; then
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$DIR/$f" -pass "file:$BACKUP_PASSPHRASE_FILE" | tar -tz >/dev/null \
      || fail "documents archive unreadable"
  else
    tar -tzf "$DIR/$f" >/dev/null || fail "documents archive unreadable"
  fi
  echo "documents archive: ok"
done

for sch in "${SCHEMAS[@]}"; do
rows=$(PGOPTIONS="-c search_path=$sch" psql -tAc "SELECT 'purchases=' || (SELECT count(*) FROM purchase_invoice) || ' batches=' || (SELECT count(*) FROM stock_batch)
                  || ' movements=' || (SELECT count(*) FROM stock_movement) || ' payments=' || (SELECT count(*) FROM supplier_payment)" "$TMPDB") \
  || fail "row count query ($sch)"
echo "rows ($sch): $rows"
done
echo "verify_restore: PASSED in $(( $(date +%s) - start ))s"
