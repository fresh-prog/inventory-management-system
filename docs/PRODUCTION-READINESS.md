# Production-Readiness Report — v0.4.0

Date: 2026-11-22 · Detailed findings: [AUDIT.md](AUDIT.md) · Test output: [test-results.txt](test-results.txt)

## Verdict
**No critical or high defect remains open in the code.** All 3 critical and 8 high findings of the audit are
fixed and covered by tests or benchmarks.

**The system is ready for a supervised pilot in one restaurant, but it is _not yet approved for general
commercial production.** Several things could not be verified in this environment and must pass before
general release:

| Gate | Status | Needed before |
|---|---|---|
| G1. Docker images built and run; full install on target branch hardware using `deploy.sh`; restore drill on that hardware | **Not done here** (no Docker daemon). The CI `image` job builds and smoke-tests the images. | Pilot |
| G2. Off-site backup to the real destination (USB/NAS/cloud bucket) verified with `verify_restore.sh` | Tested with a mounted directory only | Pilot |
| G3. Legal and tax review (Uganda: PDPO registration, cross-border transfer, EFRIS; Eritrea: tax-record rules) | Research notes only (07-legal) | Pilot (with real customer data) |
| G4. Independent penetration test | Not done | General release |
| G5. Field validation on real restaurant internet (latency, packet loss, long outages) | Bandwidth-throttled lab test only | General release |
| G6. Encrypt TOTP secrets and sync keys at rest; HttpOnly-cookie sessions | Planned (AUDIT O3, O4) | General release |

## Acceptance areas and evidence
| Area | Result | Evidence |
|---|---|---|
| **Functional completeness** | ✅ All MVP features (ingredients, recipes and costing, purchases, suppliers and payments, FIFO stock, production, waste, counts, reports) plus hybrid sync, receipts, remote management | MVP-REPORT, SYNC-TEST-REPORT; 12 UI end-to-end flows on desktop, phone and tablet |
| **Security** | ✅ with open items G4 and G6 | SECURITY.md; 18 adversarial tests (auth on **every** route, read-only role blocked on **every** write route, token forgery, cross-branch, injection, uploads, leaks, rate limits, headers); 0 known CVEs |
| **Data integrity** | ✅ | Randomized model check (3 × 160 operations) matches an independent model exactly; every purchase line ⇒ exactly one batch and receipt; every movement traceable (DB constraint); history never rewritten; fault injection at 9 points ⇒ no partial writes; independent SQL integrity checks |
| **FIFO accuracy** | ✅ | Model check of every allocation (batch, quantity, cost) incl. 52 blocked over-consumptions; concurrency tests (20 parallel consumers; 2 devices); power-cut chaos ⇒ 0 integrity violations |
| **Recipe costing accuracy** | ✅ | Spec examples (4.50/0.30 = 15.00); current vs FIFO basis kept separate; list cost always equals live re-costing incl. conversion changes; versions immutable |
| **Supplier balance accuracy** | ✅ | Model check of every balance and invoice status incl. partial payments, credits, voids, 14 rejected over-payments; concurrent payments ⇒ one wins; ledger = invoices on branch and cloud |
| **Offline reliability** | ✅ | Chaos: power cut (DB crash + `kill -9`, 499 writes) ⇒ every confirmed save present, retries exactly-once, 0 duplicates; DB outage ⇒ clear "nothing saved" and self-recovery; disk full; broken upload |
| **Synchronisation** | ✅ | 26 sync tests (lost acks, restarts, cloud/branch restores, conflicts, zip-bomb cap, size-bounded batches); full-scale: **408,610 rows identical** after a throttled 2 Mbit/s offline backlog |
| **Performance** | ✅ targets met | PERFORMANCE.md: 25 users ⇒ search p95 100 ms, usage 127 ms, purchase 258 ms, dashboard 412 ms; 60 users ⇒ 0 errors; phone screens ≤ 1.2 s |
| **Backup & recovery** | ✅ (G1, G2 pending) | DISASTER-RECOVERY.md: encrypted DB + documents, off-site copy, tamper detection, daily restore test with amcheck and integrity SQL; 263 MB branch backed up in 16 s and restored in 22 s; RPO/RTO defined |
| **Deployment readiness** | ⚠ G1 | DEPLOYMENT.md: dev/staging/prod, secrets, versioning, CI with audits and image smoke test, release pipeline with approval gate, migration safety (failure and rollback tested), metrics and alerting |
| **Legal** | ⚠ G3 | 07-legal-and-compliance.md: research notes only, professional verification required |

## Application experience review
| Item | Finding / state |
|---|---|
| Navigation | Six sections plus Settings; phone bottom bar with Reports/Settings in the top bar; deep links for tabs and filters |
| Search | Local and instant (72 ms per keystroke on a slow phone with 3,000 ingredients); server search parameterised and bounded |
| Form validation | Server is the authority (field-level messages from 422 responses); live totals and mismatch hints on invoices; payments must allocate exactly |
| Loading behaviour | Spinners on buttons and pages; placeholders keep the previous page while paging; large lists render incrementally |
| Error messages | Plain language, e.g. "Nothing was saved", "Not enough stock: requested …, available …", "Storage full", rate-limit wait time; no stack traces |
| Offline indicators | Top bar: server unreachable (drafts only) / saved locally, N not yet in the cloud / all in the cloud / needs attention |
| **Local vs pending vs cloud** | Success toasts only after the **server** commits. Device drafts are labelled "saved on this device · not posted". Records carry **"Saved on server" / "Saved · cloud pending" / "In cloud ✓"** (only after a signed acknowledgement) / "Cloud sync issue". Receipts show "saved locally", then "in cloud ✓" only after checksum verification. |
| Mobile / tablet layouts | 0 px horizontal overflow on iPhone 13 and iPad; line items stack on phones |
| Accessibility | axe-core WCAG 2.1 A/AA: **0 violations across 28 page views** (desktop and phone) after fixing contrast and link styling |
| Confirmation workflows | Review step before confirming purchases, production and stock counts; voids need a reason; destructive actions are confirm dialogs; idempotency keys make double-taps harmless |

## Residual risks accepted for the pilot
See AUDIT.md "OPEN" (O1–O10). The most important for a pilot:
* run gates G1–G3;
* keep the backup passphrase off-site;
* enable monitoring (metrics + alerts or the cron health check) from day one.
