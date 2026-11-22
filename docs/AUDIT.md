# Technical Audit — Stage 4 (Production Hardening)

Date: 2026-11-22 · Scope: the whole repository (frontend, backend, database, business logic, FIFO,
recipe costing, purchasing, payments, sync, file storage, auth, deployment, logging, tests).

**Method.** I treated the existing code as untrusted and tried to break it:
- dependency CVE scans;
- adversarial security tests: every route without a token, every write as a read-only user, token forgery, cross-branch access, injection, uploads, leaks, rate limits;
- a **model-based randomized simulation** that checks FIFO, ledgers and invoice status against an independent Python model, plus fault injection at 9 points;
- database profiling (`pg_stat_statements`) under realistic load on a large generated restaurant;
- real failure simulation: PostgreSQL crash, `kill -9`, database outage, full disk, broken uploads;
- backup tamper tests, a WCAG accessibility scan, and render timing on a throttled phone.

Every finding below is **fixed and covered by a test or benchmark unless marked OPEN**. "Regression test fails on old code" means the test was run against the pre-fix code to prove it catches the defect.

## Critical

| # | Finding | Impact | Fix & evidence |
|---|---|---|---|
| C1 | **Change capture only worked if some web route happened to import the capture module.** A process writing through the services without importing it produced transactions with **no sync record**. The first benchmark dataset (80,000 transactions) had **0 outbox records**. | Silent, permanent loss of cloud sync for anything written outside the web app (CLI tools, scripts, future jobs). | Capture registers whenever models load; a capture-enabled session refuses to start if the hooks are missing. `test_capture_always_on.py` runs a bare process (**fails on old code**). The regenerated dataset has 46,600 outbox records. |
| C2 | **Known-vulnerable dependencies:** PyJWT 2.10 (15 advisories), Starlette 0.48 (6), React Router 6 (open redirect, high), Vite 6.3 / Vitest 3 (critical/high, dev). | Token-handling and framework vulnerabilities in production. | FastAPI 0.165 / Starlette 1.10, PyJWT 2.15, React Router 7.18, Vite 6.4.6, Vitest 4.1.12. `pip-audit` and `npm audit`: **0 known vulnerabilities**. CI now fails on any known CVE. |
| C3 | **The backup verification script ignored failures** (`check && echo ok` under `set -e`). A **tampered backup was reported as verified**, and a failed `pg_restore` would have passed. | False assurance that backups can be restored. | Every check is explicit pass/fail. `test_tampered_backup_is_detected`, `test_restore_detects_business_corruption`. |

## High

| # | Finding | Fix & evidence |
|---|---|---|
| H1 | The purchases list loaded **every** invoice and line on every request, then paginated in Python: **2.5 s per page at 10,000 invoices; 29 s median under 25 users.** | SQL filtering, status and pagination: **46–56 ms**. Consistency test against the detail figures. |
| H2 | Numeric inputs were unbounded: `1e30`, `NaN`, `Infinity` reached the database (overflow → 500). | Bounds matching the column types, NaN/Inf rejected → 422. Parametrised tests. |
| H3 | No per-client rate limiting: password spraying across accounts and registration-code guessing were possible. | Sliding-window limits (login, setup, cloud login, branch registration, global ceiling). `test_login_is_rate_limited_per_client`. |
| H4 | No security headers from the app itself (relied on Caddy). Uploaded documents were served inline in the app's origin. | nosniff, frame-deny, referrer, permissions and CSP headers; documents served with a `sandbox` CSP. Test. |
| H5 | **Missing indexes**, e.g. `recipe_line.ingredient_id`: re-costing scanned all recipe lines on every purchase line (29 s of DB time during generation, more than every other query combined). 11 lookup columns unindexed. | Migration `0004perf`. |
| H6 | Dashboard sent 1.4 MB (all stock rows, unused) in 787 ms. Recipes list re-costed all recipes per request (470 ms). Integrity check loaded every batch into Python (3.2 s). | Dashboard **209 ms / 212 KB**, recipes **42 ms**, integrity **107 ms** (SQL). |
| H7 | API docs exposed in production; the public health endpoint revealed sync errors and queue sizes. | Docs off in production; sync details only via the authenticated `/sync/status`. Test. |
| H8 | File storage could leave **partial files** (disk full) or **orphan files** (database failure after write), and had no free-space reserve, so receipts could fill the disk the database needs. | Cleanup on every failure path; configurable reserve (`APP_MIN_FREE_MB`); `507 Storage full`. Chaos: disk full and broken upload leave nothing behind. |

## Medium

| # | Finding | Fix & evidence |
|---|---|---|
| M1 | Waste adjustments had no source id, so a movement was not traceable to its document. | The adjustment is its own source; migration `0003audit` back-fills; DB `CHECK (source_id IS NOT NULL)`. Test resolves every movement to a record or audit entry. |
| M2 | Inventory value was computed two ways (stock page sum of rounded rows vs dashboard), differing by up to N × 0.00005. Found by the randomized model. | One definition everywhere. |
| M3 | CSV export allowed spreadsheet formula injection (`=HYPERLINK(...)`). | Cells starting with `= + - @` are neutralised. Unit test. |
| M4 | A NUL byte in a search or text field → 500. | Rejected at validation (422) plus a DB error handler. |
| M5 | Unbounded text and list sizes (100 KB notes, 1,000-line requests accepted). | 4,000-character default and per-field limits; list caps. |
| M6 | Changing an ingredient's conversion rule changed recipe costs **without** a new cost snapshot (stale "current cost"). | Conversion changes re-cost affected recipes. `test_recipe_list_cost_equals_live_cost_including_conversion_changes`. |
| M7 | The ingredient list in the UI was silently capped at 500. | 5,000 limit with incremental rendering. |
| M8 | Sync batches were bounded by count only. Snapshot batches could be ~50,000 rows per request; sync sent uncompressed JSON (227 MB initial copy). | Byte-bounded batches (2 MB), gzip with a decompression-bomb cap, multi-row apply: initial copy **31 MB**, backlog 14× smaller. Tests. |
| M9 | Accessibility: green badge contrast 4.39:1 (needs 4.5:1); links distinguished by colour only. | Fixed; axe-core: **0 violations on 28 page views**. |
| M10 | Only a global sync indicator existed; individual records did not show whether they were saved locally, pending, or cloud-confirmed. | Per-record status (`local / pending / cloud / attention`) on purchases and payments; "In cloud ✓" only after a signed acknowledgement. |
| M11 | FastAPI's generic JSON encoder cost 5× more than necessary on large lists (80 ms vs 16 ms). | Exact-decimal fast encoder; pre-rendered responses. |
| M12 | 2 API workers left 2 of 4 cores idle under load. | Default `APP_WORKERS=4`. |
| M13 | The Stock page rendered 3,000 rows at once: 3.6 s on a mid-range phone. | Incremental rendering: 0.6 s. |
| M14 | Restoring a database with monitoring extensions (`pg_stat_statements`) failed for non-superusers. | Restore skips monitoring-only extensions. |

## Verified correct (no defect found)
* Authentication on every non-public route (enumerated from the router) and the read-only role blocked on **every** write route.
* Forged, `alg=none`, expired and wrong-audience tokens rejected; cross-branch access to purchases, files, counts, voids, credits and devices blocked.
* SQL injection probes inert (all queries parameterised).
* FIFO, costs, supplier balances and invoice statuses match the independent model across 3 × 160 random operations, including 52 blocked over-consumptions, 14 rejected over-payments and 20 voids.
* Every purchase line has exactly one batch and receipt; stock cost reconciles with invoice subtotal minus discount.
* Fault injection at 9 points: no partial writes, no burnt idempotency keys.
* History is never rewritten: price history, recipe versions, cost snapshots, FIFO costs and the ledger.
* Migrations: a failure mid-migration leaves the previous revision and data untouched; every migration downgrades and upgrades.

## OPEN — accepted risks / not verifiable here
| # | Item | Why open / mitigation |
|---|---|---|
| O1 | **Docker images have never been built or run** (no Docker daemon in this environment). | The CI `image` job builds both images and smoke-tests the app image. It must pass once before any deployment. |
| O2 | No independent penetration test. | Commission one before onboarding customers' data at scale. |
| O3 | Access tokens are held in `localStorage` (XSS would expose them). | Mitigated by a strict CSP (no inline scripts), React escaping, no `dangerouslySetInnerHTML`, 12-hour expiry and server-side revocation. HttpOnly-cookie sessions are planned. |
| O4 | Cloud TOTP secrets and the branch sync key are stored unencrypted at rest. | Protected by DB access control and encrypted backups. KMS encryption is planned. |
| O5 | Rate limits are per process (4 workers ⇒ up to 4× the nominal limit). | Adequate for brute-force deterrence; a shared limiter would need Redis. |
| O6 | The outbox commit lock serialises commits per branch (6 ms mean wait at 36 req/s). | Measured acceptable; it is a scaling limit beyond about 60 busy users per branch. |
| O7 | WAN tested for **bandwidth only** (2 Mbit/s throttle), not latency or packet loss. | Retries, backoff and idempotency are designed for it; a field pilot will confirm. |
| O8 | Off-site backup tested to a mounted directory, not a real cloud bucket. | `BACKUP_OFFSITE_CMD` hook (e.g. rclone) to be tested at installation. |
| O9 | Database-volume disk-full not simulated (only the documents volume). | Disk alerts at 10% and 3%; document uploads refused below the reserve. |
| O10 | Legal and tax requirements are research notes, not professional advice. | Professional review required (see 07-legal). |
