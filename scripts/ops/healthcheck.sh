#!/usr/bin/env bash
# Cron-friendly alerting for sites without Prometheus. Run every 5 minutes:
#   */5 * * * * KL_URL=https://restaurant.local KL_METRICS_TOKEN=... KL_ALERT_WEBHOOK=https://... /opt/kl/scripts/ops/healthcheck.sh
# Posts a JSON message to KL_ALERT_WEBHOOK (Slack/Teams/ntfy/email gateway) when a check fails.
set -uo pipefail
URL="${KL_URL:-http://127.0.0.1:8000}"
problems=()
m=$(curl -fsS -m 10 -H "Authorization: Bearer ${KL_METRICS_TOKEN:-}" "$URL/api/v1/metrics") || problems+=("server not responding")
val() { echo "$m" | awk -v k="$1" '$1==k {print $2}'; }
if [[ -n "${m:-}" ]]; then
  [[ "$(val kl_database_up)" == "1" ]] || problems+=("database down")
  awk -v r="$(val kl_disk_free_ratio)" 'BEGIN{exit !(r < 0.10)}' && problems+=("disk under 10% free")
  [[ "$(val kl_restore_test_failed)" == "1" ]] && problems+=("restore test of the newest backup FAILED")
  age=$(val kl_last_backup_age_seconds); { [[ "$age" == "-1" ]] || (( ${age%.*} > 93600 )); } && problems+=("no backup in 26h")
  [[ "$(echo "$m" | awk '$1=="kl_sync_events{status=\"failed\"}" {print $2}')" == "0" ]] || problems+=("sync: rejected change")
  [[ "$(val kl_sync_halted)" != "1" ]] || problems+=("sync halted")
fi
if (( ${#problems[@]} )); then
  msg="KitchenLedger $(hostname): ${problems[*]}"
  echo "$msg" >&2
  [[ -n "${KL_ALERT_WEBHOOK:-}" ]] && curl -fsS -m 10 -H 'Content-Type: application/json' \
      -d "{\"text\": \"$msg\"}" "$KL_ALERT_WEBHOOK" >/dev/null
  exit 1
fi
echo "ok"
