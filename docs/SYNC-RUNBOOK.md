# Synchronisation — Operational Recovery Runbook

Golden rules:
1. **The restaurant keeps working without the cloud.** Never stop service just because sync is down.
2. **Never delete outbox rows, inbox rows or the `files` volume** to "fix" sync. Every procedure below preserves them.
3. **Take a backup before any recovery step** (INSTALL.md → Backups) of the side you are touching.

Where to look:
* **Branch:** Settings → Cloud sync (state, queues, rejected events, last error). From the server: `docker compose exec sync python -m app.sync.cli status`.
* **Cloud:** the branch page → Sync monitor (state, data-as-of, last applied #, branch queue, recent events).

| Symptom | Meaning | Action |
|---|---|---|
| Branch: "N changes not yet in the cloud", last error *cloud unreachable* | Internet or cloud down | Nothing. The worker retries with backoff. Check the router; check the cloud's `/api/v1/health/ready`. |
| Cloud: branch **offline** (no heartbeat for 15 min) | Branch server off, worker stopped, or no internet | Check the branch server is on and `docker compose ps sync` is running; restart it with `docker compose restart sync`. |
| Branch: **Blocked by a rejected change** (failed queue > 0) | The cloud refused one event 10 times; later events wait (ordering) | Read `last_error` in the failed list. If it says the cloud must be upgraded → upgrade the cloud (§4). Otherwise press **Send full copy** (snapshot supersedes the stuck event), or **Retry rejected** after the cloud-side cause is fixed. Report the event id to support. |
| Branch: events stay `retry` with *cloud does not know schema …* | The branch was upgraded before the cloud | Upgrade the cloud (§4). The events apply automatically afterwards. |
| Cloud/branch: **Halted — sequence regression** | The branch database went back in time (restored backup) and created new changes | §2.2. Do not resume the branch in the cloud until it is reconciled. |
| Branch: repeated *cloud returned 401: Bad signature or clock skew* | The branch server's clock is more than 5 minutes off (common after long power cuts without NTP) | Fix the time (enable NTP on the host); sync resumes automatically. If the clock is right, the branch key may have been rotated → re-register (§3). |
| Receipt shows "not yet in cloud" for a long time | Upload waiting (offline) or the metadata event has not synced yet | Usually nothing. If the branch log says *local file is corrupt*, restore the `files` volume from backup. |
| Command stuck at **delivered** in the cloud | Branch received it but has not reported a result (e.g. crashed mid-apply) | It is re-applied safely on the next cycle (inbox de-duplication). If it persists, check the branch worker log. |

## 1. Planned events

### 1.1 Internet outage / extended offline period
Nothing to do. Changes stay `pending` or `retry`. When the internet returns, the queue drains in order, in batches of 100.
Remote dashboards say "data as of <time>" so nobody mistakes old data for live data.

### 1.2 Restarting the branch server or worker
`docker compose restart app sync` (or a power cut). All state is in PostgreSQL: anything sent but not acknowledged is re-sent and de-duplicated by the cloud.

### 1.3 Restarting the cloud
`docker compose -f docker-compose.cloud.yml restart cloud`. Branches back off and retry, then converge.

## 2. Database recovery

### 2.1 Cloud database lost / restored from an older backup
1. Restore the cloud database from its latest backup (`pg_restore --clean` inside the cloud db container) and start the cloud.
2. Each branch's next heartbeat sees the cloud's high-water mark is behind its acknowledged events, re-queues them and re-sends them automatically (retained for 90 days).
3. Verify on each branch page: state **in sync**, and *data checks passed* on the dashboard.
4. If a branch has been disconnected for longer than the retention period: press **Send full copy** on that branch.

### 2.2 Branch database lost / restored from an older backup
**Do this before opening the restaurant, with no devices posting:**
1. Restore the branch database backup and the `files` volume.
2. Start only the database and run:
   ```bash
   docker compose run --rm sync python -m app.sync.cli restore-from-cloud
   ```
   This replays every newer event the cloud holds (purchases, payments, stock movements, …) onto the branch and moves its sequences past them. **Acknowledged transactions are recovered from the cloud.**
3. Start everything: `docker compose up -d`. Check **Reports → Data checks** passes and the dashboard figures look right.
4. Transactions made at the branch that were **never acknowledged** before the disk failure are in neither copy. Re-enter them from paper receipts. The cloud's "data as of" time tells you from when.

If `restore-from-cloud` reports that the history contains a full snapshot, restore a newer branch backup, or contact support to rebuild the branch from the cloud replica.

If the branch was already opened and posted new changes (the cloud shows **halted — sequence regression**):
* Nothing is lost: the branch keeps its new events (pending), and the cloud keeps everything it acknowledged.
* Do **not** press *Resume* in the cloud yet. Export both histories (`python -m app.sync.cli status`, the cloud events list) and reconcile with support. The fix is to restore-from-cloud into a fresh copy of the branch database, then re-enter the new local transactions through the app, so they get new sequence numbers and new FIFO allocations. Then press *Resume* in the cloud.

## 3. Rotating or disconnecting a branch
* **Lost or stolen device:** Settings → Devices → Revoke (or from the cloud: a `device.revoke` command).
* **Branch sync key compromised:** increase the branch's `secret_version` in the cloud (admin) and re-register the branch with a new registration code. **Do not change `APP_CLOUD_MASTER_KEY`**: that disconnects every branch at once.

## 4. Software upgrades with pending transactions
1. Back up both sides.
2. **Upgrade the cloud first:** `docker compose -f docker-compose.cloud.yml up -d --build`. `app.cloud.cli migrate` upgrades the control plane and every branch replica schema.
3. Upgrade branches: `docker compose up -d --build`. Pending events are untouched by the migration. Events produced before change capture existed (pre-Stage-3) are marked *superseded* and replaced by an automatic full snapshot.
4. Verify each branch shows **in sync**.

## 5. Health checks to automate
* Branch `GET /api/v1/health/ready` → alert if `sync_failed_events > 0`, `sync_halted` is set, or `cloud_last_success_at` is older than 24 h while online.
* Cloud: alert when any branch is `halted` or `error`, or `offline` for more than 24 h during opening hours.
* Disk: `disk_free_percent < 10` on either side.
