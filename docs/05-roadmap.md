# 05 — Implementation Roadmap

Each stage ends with: automated tests green · calculations verified · DB integrity checked ·
permissions tested · failure cases tested · docs updated · committed and pushed. Nothing is
marked done unless it was verified.

| Stage | Scope | Status / exit criteria |
|---|---|---|
| **1 — Foundation** | Requirements analysis, 8 ADRs, DB design, business rules, API contracts, acceptance tests, legal notes, pure domain engines | ✅ Done |
| **2 — MVP** | Platform (FastAPI, PostgreSQL, Alembic, auth/RBAC, logging, idempotency, audit, outbox), ingredients, recipe costing, purchasing and suppliers, FIFO inventory, recipe production, stock management, dashboard and reports, offline foundation, responsive PWA | ✅ Done — all 14 MVP acceptance criteria; see [MVP-REPORT.md](MVP-REPORT.md) |
| **3 — Hybrid sync** | Change-capture outbox, cloud API with per-branch replica schemas, signed acks, commands with conflict policies, device registration, local receipts with resumable verified upload, TOTP for remote access, recovery tooling | ✅ Done — see [SYNC-TEST-REPORT.md](SYNC-TEST-REPORT.md) |
| **4 — Production hardening** | Audit, security, integrity, performance, failure simulation, backup/DR, deployment pipeline, monitoring, UX/accessibility | ✅ Done — see [PRODUCTION-READINESS.md](PRODUCTION-READINESS.md); gates G1–G3 before the pilot |
| 5 — Receipt OCR (attachments already done) | Secure uploads, local OCR engine, review screen, ingredient matching with supplier aliases, original kept with the purchase | Labelled invoice set: field-accuracy report; zero auto-posting |
| 6 — Remaining hardening (gates G4–G6) | Docker install verified on target hardware, WAN sync load test (2 Mbps, 7-day backlog), secret encryption at rest, refresh tokens/mTLS, nightly encrypted off-site backups and restore drill, per-IP rate limits, k6 load tests against the NFRs, security review, translations, WCAG 2.1 AA audit | NFR report; restore drill passed |
| 7 — Pilot | One restaurant, data migration, training material, feedback loop | Owner sign-off |

Future (designed for, not built): POS integration and sales deductions, multi-branch transfers,
supplier price comparison, purchase orders, menu engineering, labour and operating expenses,
financial reporting/exports.
