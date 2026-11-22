# Backup & Disaster Recovery

## Targets
| Scenario | RPO (data that can be lost) | RTO (time to working again) | Basis (measured) |
|---|---|---|---|
| Power cut / crash of server or database | **0** confirmed transactions | **< 5 min** (boot) | Chaos test: PostgreSQL crash + `kill -9` during 499 writes. All 292 confirmed saves present; DB back in 1.6 s. |
| Branch database lost, **cloud sync connected** | Changes not yet acknowledged by the cloud (normally **< 1 min**) | **≤ 1 h** incl. human steps; technical restore < 2 min | `test_branch_database_restored_from_older_backup` (restore + restore-from-cloud recovers every acknowledged transaction) |
| Branch database lost, **local backups only** | ≤ **6 h** (backup interval, configurable down to 1 h) | **≤ 1 h**; restore of a 263 MB branch takes 22 s | `verify_restore.sh` at benchmark scale |
| Branch server destroyed (theft, fire) | as above, from the **off-site** copy | **≤ 4 h** (new hardware, install, restore) | INSTALL.md + this guide |
| Cloud database lost | **0** for branch data (branches keep 90 days of events and re-send) | **≤ 4 h** | `test_cloud_database_restored_from_older_backup_reconverges` |
| Bad release / failed migration | 0 | **< 15 min** (automatic rollback) | `test_failed_migration_leaves_previous_revision_intact`, `deploy.sh` |

For restaurants that cannot accept up to 6 hours of loss and have no cloud, set `BACKUP_INTERVAL_HOURS=1`.
At this size a backup takes about 16 s.

## What is backed up
`scripts/backup.sh`: `pg_dump` (custom format, consistent snapshot) **+ receipts/documents** (tar), each
**AES-256 encrypted** (PBKDF2, 200k iterations) and decrypt-verified, with a SHA-256 manifest. Backups are
copied to an **off-site** destination (mounted USB disk or network share via `BACKUP_OFFSITE_DIR`, or any
command such as `rclone` via `BACKUP_OFFSITE_CMD`), where the copy is checksum-verified. 60 local backups
are kept (15 days at 6-hourly).

The Compose `backup` service (PostgreSQL-16 tools, matching the server) runs this every 6 hours and runs a
**full restore test daily** (`verify_restore.sh`):
1. checksums;
2. decrypt;
3. `pg_restore` into a throwaway database;
4. schema revision;
5. **amcheck** physical index verification;
6. **business-integrity SQL** (`scripts/sql/integrity.sql`), independent of the app code: FIFO batches vs allocations, supplier ledger vs invoices, over-payments, traceability, one published recipe version;
7. documents archive.

A failed restore test creates `RESTORE_TEST_FAILED` (removed again by the next passing test), reported as
`kl_restore_test_failed 1` in the metrics: alert `RestoreTestFailed` and the cron health check.
The cloud has the same `backup` service; its restore test checks **every branch replica** with the same
business-integrity SQL.

**The backup passphrase must be stored off the server** (e.g. owner's password manager plus a sealed paper
copy). Without it the backups cannot be restored.

Measured on the 263 MB benchmark branch: **backup 16 s (126 MB encrypted)**; **verified restore 22–23 s**
(amcheck ok, 0 integrity violations). Tests: `test_encrypted_backup_offsite_copy_and_verified_restore`,
`test_tampered_backup_is_detected`, `test_restore_detects_business_corruption`.

## Integrity checks while running
* In the app: **Reports → Data checks** / `GET /api/v1/reports/integrity` (107 ms at benchmark scale). Batch quantities vs allocations, inventory value vs movements, supplier ledgers vs invoices, no negative batches, every movement traceable.
* Database constraints and triggers: batch quantities within bounds, immutable history tables, traceable movements, single published recipe version, unique supplier invoice and payment reference.

## Recovery procedures
### A. Branch database corrupted or lost
1. Stop `app` and `sync`: `docker compose stop app sync`.
2. Choose the newest backup that passes verification: `docker compose exec backup /scripts/verify_restore.sh /backups/<dir>`.
3. Restore it into the live database (this replaces it):
   ```bash
   docker compose exec -T backup sh -c 'dropdb restaurant && createdb restaurant && openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in /backups/<dir>/db.dump.enc -pass file:$BACKUP_PASSPHRASE_FILE | pg_restore --no-owner --exit-on-error -d restaurant'
   ```
4. Restore documents: decrypt `files.tar.gz.enc` and extract into the `files` volume.
5. **If cloud sync was connected:** `docker compose run --rm sync python -m app.sync.cli restore-from-cloud` (see SYNC-RUNBOOK §2.2). **Do this before staff start working.**
6. Start: `docker compose up -d`. Check **Reports → Data checks** passes and the dashboard figures are plausible.
7. Re-enter any paper receipts from after the "data as of" time.

### B. Server hardware lost
Install a new server (INSTALL.md §A) with the same `secrets/` (from the owner's off-site copy), copy the newest
off-site backup to `./backups`, then follow procedure A from step 3.

### C. Failed upgrade
`scripts/deploy/deploy.sh` does this automatically:
1. takes a pre-deploy backup and verifies it;
2. starts the new version;
3. checks health and version;
4. on failure, starts the previous version and, if the schema changed, restores the pre-deploy backup.

Manual rollback without a restore: `docker compose run --rm app alembic downgrade <previous revision>` (every
migration is tested to downgrade), then start the previous image tag.

### D. Cloud lost
Restore the cloud database from its newest verified backup (the cloud's `backup` service; same procedure as A with
`docker compose -f docker-compose.cloud.yml` and database `restaurant_cloud`) and start it.
Branches detect the lower high-water mark and re-send automatically (SYNC-RUNBOOK §2.1).

## Drills
Run quarterly, and record the result:
1. Restore yesterday's off-site backup on a spare machine.
2. Check **Data checks** and three figures against the live system.
3. Time it against the RTO.
