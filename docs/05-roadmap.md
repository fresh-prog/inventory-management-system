# 05 — Implementation Roadmap

Each stage ends with: automated tests green · calculations verified · DB integrity checked ·
permissions tested · failure cases tested · docs updated · committed and pushed. Nothing is
marked done unless it was verified.

| Stage | Scope | Exit criteria |
|---|---|---|
| **1 — Foundation (this stage)** | Repo inspection, requirements analysis, 8 ADRs, DB design (DDL validated on PG16), business rules, API contracts, roadmap, acceptance tests, legal research notes; pure domain engines: units, current-cost recipe costing, FIFO allocation, payables, invoice validation | ✅ 22 unit tests passing; schema loads and rejects invalid data |
| 2 — Platform skeleton | Docker Compose (Postgres, API, worker, Caddy), FastAPI app, settings, structured logging, health checks, Alembic migrations from the draft schema, UUIDv7, idempotency middleware, error format, CI (lint, type check, tests with a real Postgres) | `docker compose up` works; migrations up/down; CI green |
| 3 — Identity & security + Ingredients/Recipes (M1) | Users, roles, Argon2, sessions, TOTP MFA, RBAC, audit log; ingredient CRUD, conversions, recipes/versions/approval, costing API, cost snapshots | Permission matrix tests; costing API tests |
| 4 — Purchasing & suppliers (M2) | Suppliers, purchase drafts, confirm transaction (atomic multi-table), void, payments & allocations, credits, ledger, statements, file uploads | Atomicity tests (fault injection mid-transaction → nothing persisted); duplicate/idempotency tests |
| 5 — FIFO inventory (M3) | Batches, consumption, production runs, adjustments, reversals, stock counts, valuation; concurrency (row locks) | Parallel-consumer test (N threads) never over-allocates; invariant job passes |
| 6 — Frontend PWA | React/TS app shell, navigation, dashboard, all module screens, IndexedDB cache/drafts/queue, sync indicator, responsive + accessibility (WCAG 2.1 AA) | Playwright E2E on phone/tablet/desktop viewports |
| 7 — OCR | Upload/photo, local engine, review screen, ingredient matching and alias learning | Labelled invoice set: field-accuracy report; zero auto-posting |
| 8 — Offline/cloud sync | Outbox publisher, cloud inbox, acks, master-data down-sync, conflict queue, remote "as of" views, MFA for remote access | Chaos tests: network cut, restart mid-sync, duplicate delivery → no loss or duplication |
| 9 — Hardening & ops | Encrypted backups + restore drill, disk monitoring, rate limiting, load tests (k6) against the NFR targets, security review, installer/upgrade procedure | NFR report; restore drill passed |
| 10 — Pilot | One restaurant, data migration, training material, feedback loop | Owner sign-off |

Future (designed for, not built): POS integration and sales deductions, multi-branch transfers,
supplier price comparison, purchase orders, menu engineering, labour and operating expenses,
financial reporting/exports.
