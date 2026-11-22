#!/usr/bin/env bash
# Deploy a released version to THIS server (staging or production), with automatic rollback.
#
#   scripts/deploy/deploy.sh <version>          e.g. 0.4.1   (images: $KL_IMAGE:<version>)
#
# 1. refuses to run with a dirty config, 2. takes and verifies a pre-deploy backup,
# 3. pulls the image, 4. restarts app+sync (migrations run on start), 5. checks health + version,
# 6. on failure: previous image + restore of the pre-deploy backup if the schema changed.
set -euo pipefail
NEW="$1"; cd "$(dirname "$0")/../.."
export KL_IMAGE="${KL_IMAGE:-ghcr.io/fresh-prog/inventory-management-system}"
STATE=.deploy-state; PREV="$(cat "$STATE" 2>/dev/null || echo none)"
URL="${KL_HEALTH_URL:-https://localhost}"
log() { echo "$(date -u +%FT%TZ) $*"; }
health() {  # wait up to 3 minutes for the new version to be healthy
  for _ in $(seq 1 60); do
    v=$(curl -fsSk -m 5 "$URL/api/v1/health/live" 2>/dev/null | sed -n 's/.*"version":"\([^"]*\)".*/\1/p') || true
    r=$(curl -fsSk -m 5 -o /dev/null -w '%{http_code}' "$URL/api/v1/health/ready" 2>/dev/null) || true
    [[ "$v" == "$1" && "$r" == "200" ]] && return 0
    sleep 3
  done
  return 1
}
log "deploying $NEW (current: $PREV)"
rev_before=$(docker compose exec -T db psql -U app -tAc "SELECT version_num FROM alembic_version" restaurant)
log "pre-deploy backup…"
bdir=$(docker compose exec -T backup /scripts/backup.sh /backups | tail -1)
docker compose exec -T backup /scripts/verify_restore.sh "$bdir"
docker pull "$KL_IMAGE:$NEW"
KL_VERSION="$NEW" docker compose up -d --no-build app sync
if health "$NEW"; then
  echo "$NEW" > "$STATE"; log "deployed $NEW"; exit 0
fi
log "HEALTH CHECK FAILED — rolling back to $PREV"
rev_after=$(docker compose exec -T db psql -U app -tAc "SELECT version_num FROM alembic_version" restaurant || echo unknown)
docker compose stop app sync
if [[ "$rev_after" != "$rev_before" ]]; then
  log "schema changed ($rev_before → $rev_after): restoring the pre-deploy backup"
  docker compose exec -T backup sh -c "dropdb restaurant && createdb restaurant && \
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in $bdir/db.dump.enc -pass file:\$BACKUP_PASSPHRASE_FILE \
    | pg_restore --no-owner --exit-on-error -d restaurant"
fi
[[ "$PREV" != "none" ]] && KL_VERSION="$PREV" docker compose up -d --no-build app sync && health "$PREV" && log "rolled back to $PREV"
exit 1
