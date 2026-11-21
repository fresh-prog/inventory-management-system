# Hybrid Sync — Test Report (Stage 3)

Date: 2026-11-21 · Environment: PostgreSQL 16, Python 3.13, Chromium (Playwright).
Branch and cloud run as **separate servers with separate databases**: in the automated tests the cloud is a real uvicorn server on its own database, and in the live run they are separate OS processes plus the background worker process.

## 1. How the tests work
* **Fault injection:** a network layer between branch and cloud can refuse connections (`down`), or let the cloud process a request and then lose the response (`drop`, the worst case for acknowledgements). It can also cut uploads after N chunks or tamper with bytes in transit.
* **Real restarts:** the cloud server is stopped and started; the branch app is re-created with a fresh engine (a new process with nothing in memory). Databases are restored from real `pg_dump` backups.
* **Convergence proof:** every test ends with `assert_converged`. Every row of every replicated table (32 tables, minus local-only columns) is serialised canonically on both sides and compared **exactly**. Integrity checks (batch quantities vs allocations, inventory value, supplier ledger vs invoices) must pass on the branch **and** on the cloud replica.

## 2. Results
**Backend: 85 tests passed (61 MVP + 24 sync), run twice in a row with no flakes.** Frontend: type check and unit tests pass. End-to-end: 12 MVP UI checks plus 10 two-server UI checks pass. Full output: [test-results.txt](test-results.txt).

### Acceptance requirements → evidence
| Requirement | Evidence | Result |
|---|---|---|
| No acknowledged local transaction is silently lost | `test_lost_acknowledgements_never_duplicate`, `test_cloud_database_restored_from_older_backup_reconverges` (cloud loses acked events → branch re-sends them), `test_branch_database_restored_from_older_backup` (branch loses them → recovered from the cloud), `test_restored_branch_that_skips_the_runbook_is_halted_not_merged` (halts and keeps both histories) | ✅ |
| Repeated synchronisation does not duplicate | Lost acks; **forced re-send of every acked event** → cloud inbox count unchanged; duplicate command delivery applied once; 2 devices submit the same purchase concurrently → 1 purchase | ✅ |
| Stock calculations remain consistent | Integrity checks on both sides in every scenario; 10 concurrent consumptions from 2 devices → correct FIFO remainder (1 kg @ 12 = 12.00) on branch and cloud | ✅ |
| Supplier balances reconcile | `supplier_ledger_matches_invoices` on both sides; remote payment vs local payment → remote one rejected, balances 40.00 → 0.00 identical | ✅ |
| FIFO allocations remain correct | `stock_allocation` rows replicated byte-for-byte; batch remaining = original − allocations on the replica | ✅ |
| Local operations remain functional without internet | `test_offline_day_then_full_convergence`: purchases, payments, consumption and waste offline; live E2E with the cloud process stopped | ✅ |
| Cloud data eventually converges with local history | Exact row-image equality in 18 scenarios; live system: 482 rows identical after the E2E run | ✅ |
| Unsynchronised operations remain recoverable | Outbox survives restarts, crashes, upgrades and cloud restores; failed events are never deleted (only re-queued or superseded by a snapshot); retention deletes only *acked* events after 90 days | ✅ |

### Section-by-section coverage
| Brief item | Tests |
|---|---|
| 3. Transaction identification | `test_every_transaction_is_identified_and_written_atomically` (id, branch, device, user, time, operation, schema version, status; commit-ordered unique sequences), `test_writes_require_a_registered_unrevoked_device` |
| 4. Durable sync: atomic outbox | Failure injected mid-purchase → no business rows **and** no outbox row |
| 4. Pending / retry / failed queues, backoff + jitter | `test_offline_day…` (retry with `next_attempt_at`, back-off respected), `test_backoff_is_exponential_with_jitter`, `test_failed_event_blocks_later_events_until_operator_acts` (10 rejections → failed; later events not sent; operator snapshot fixes it) |
| 4. Acknowledgements, duplicate detection | `test_forged_acknowledgement_is_not_trusted`, lost-ack and re-send tests |
| 4. Batching, dependency order, monitoring | `test_extended_offline_period_drains_in_batches` (302 events in 4 batches + heartbeat, in order), failed-event blocking, `/sync/status` and cloud monitor asserted |
| 4. Recovery after restarts | `test_cloud_restart_and_branch_restart_mid_sync` |
| 5. Financial and inventory safety | Single writer + immutable tables; DB triggers block UPDATE/DELETE of movements, allocations, ledger and audit (MVP tests); corrections are reversal transactions |
| 6. Conflicts: concurrent purchases, payments, consumption, price updates, recipe versions, branch configuration, duplicate device submissions | `test_simultaneous_devices…`, `test_remote_payment_cannot_overpay…`, `test_price_updates_newest_effective_date_wins…`, `test_recipe_version_and_branch_settings_conflicts`, `test_remote_ingredient_edit_conflict_and_success`, `test_duplicate_command_delivery_applies_once` |
| 7. Offline media with checksums | `test_receipt_stored_locally_and_resumable_checksum_verified_upload` (stored offline, served locally with SHA-256; non-image rejected; upload cut after chunk 1 → resumed from the cloud's offset, not from 0; "in cloud" only after verification), `test_corrupted_upload_is_rejected_and_resent` |
| 8. Recovery: internet disconnection, local/cloud/app restart, interrupted upload/sync, database recovery, extended offline, upgrade with pending | All of the above + `test_schema_versions_unknown_to_cloud_wait_instead_of_failing`, `test_upgrade_migration_keeps_pending_transactions` (real downgrade/upgrade of the branch schema with a pending event) |
| 2. Cloud: MFA, isolation | `test_cloud_people_api_requires_mfa_and_isolates_branches` (no token without TOTP; enrolment mandatory; branch-scoped users see only their branches; unsigned/forged machine calls refused) |

### Measured
| Measurement | Result | Note |
|---|---|---|
| Drain after an outage of 302 transactions | **1.8 s**, 4 batches of ≤100 + heartbeat | Over localhost: this measures processing cost, not a slow link. The 2 Mbps / 7-day target is not yet measured (Stage 5). |
| Full backend suite | ~50 s | includes real `pg_dump`/`pg_restore` cycles |

## 3. Live two-server run (UI, real processes)
Script: `frontend/e2e/sync-flows.mjs`, in three phases around real process stops and starts:
1. **connect:** cloud sign-in requires password plus an authenticator code; an admin creates a branch and gets a one-time code; the branch owner registers from Settings; the initial snapshot is acknowledged; a receipt shows *saved locally*, then *in cloud ✓* after checksum verification.
2. **offline (cloud process killed):** the branch records FIFO usage normally. "Sync now" reports failure, with no false success. The indicator shows unsynchronised changes.
3. **recovered (cloud restarted + background worker):** the indicator turns to *All changes synced* without anyone pressing anything. The central overview, branch dashboard (labelled *data as of*) and sync monitor render. HQ sends two ingredient edits while a manager edits Eggs at the branch: the Salt change is **applied**, the Eggs change is **conflict** with the reason, and the branch edit is kept.

Afterwards the live databases were compared row by row: they **diverged in one table** (`supplier_ingredient`).
Root cause: re-saving an ingredient deletes and re-inserts its supplier links in one transaction, and change capture kept the delete but dropped the re-insert. **Fixed** in `capture.build_rows`. A regression test was added; it fails on the old code and passes on the new. The live system was repaired with the documented operator action (*Send full copy*) → 482/482 rows identical.

Screenshots: [cloud add branch](screenshots/c1-cloud-add-branch.png) · [branch in sync](screenshots/s1-branch-sync-in-sync.png) · [receipt local-only](screenshots/s2-receipt-local-only.png) · [branch offline queue](screenshots/s3-branch-offline-queue.png) · [all synced](screenshots/s4-branch-all-synced.png) · [cloud overview](screenshots/c2-cloud-overview.png) · [cloud branch dashboard](screenshots/c3-cloud-branch-dashboard.png) · [sync monitor](screenshots/c4-cloud-sync-monitor.png) · [branch remote changes](screenshots/s5-branch-remote-changes.png) · [cloud remote changes with conflict](screenshots/c5-cloud-remote-changes.png)

## 4. Other defects found and fixed during this stage
* Branch settings edits did not bump a version, so stale remote edits were silently accepted. Added `settings_version` and an `If-Match` check.
* With nothing to send, the branch never contacted the cloud. So it could not learn that the cloud had been restored, and the cloud could not tell idle from offline. Added a heartbeat.
* Network failures could eventually mark events "failed". Connectivity errors now retry forever; only cloud rejections count towards "failed".
* Concurrent duplicate deliveries could race the duplicate check. Check and apply now run in one transaction with a branch row lock.
* Migration constraint names were double-prefixed by the naming convention, which broke downgrades.

## 5. Known limitations
* **Docker images and the Compose files were not run here** (no Docker daemon in this environment). The services were run as plain processes instead.
* **WAN conditions not measured:** latency, a 2 Mbps link and a 7-day backlog over a slow link are still to do (Stage 5 load tests).
* **Branch restore after new local activity** (halted state) needs a manual, support-assisted reconciliation (runbook §2.2). `restore-from-cloud` refuses histories that contain a snapshot.
* **Devices offline from the branch server** keep drafts only; posting always goes through the branch server (by design, ADR-0003).
* **Remote management UI** offers four forms (payment, ingredient edit, quoted price, branch settings). The API also supports ingredient/supplier creation, supplier edits, recipe drafts and publishing, and device revocation.
* **Secrets at rest:** cloud TOTP secrets and the branch's sync key are stored unencrypted in their databases (protected by DB access control and encrypted backups). Encrypting them with a key-management service is planned.
* **Clock:** request signatures allow ±5 minutes. Branch servers must keep time (NTP); a badly drifted clock shows as persistent *cloud returned 401* retries.
* **Transport:** HTTPS + HMAC signing; mTLS (ADR-0003) is not implemented. Cloud file storage is local disk (no object storage yet).
* **Rule for developers:** business writes must go through the ORM (bulk SQL bypasses change capture). The convergence tests catch violations, but code review must enforce it.
* **Data residency:** enabling the cloud sends business and personal data (user names, supplier contacts) to the cloud host. For Uganda, check DPPA s.19 cross-border rules before choosing a host outside Uganda (see 07-legal).
