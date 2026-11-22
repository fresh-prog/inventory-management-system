# KitchenLedger — Restaurant Costing, Purchasing & FIFO Inventory

One integrated, offline-first platform for restaurants:

1. **Ingredients & food costing**: ingredients, dimension-aware units, recipes with versions, yields, current-cost pricing.
2. **Purchasing & suppliers**: multi-line invoices, supplier balances, partial payments, credits.
3. **Stock & FIFO inventory**: automatic batches, oldest-first costing, production, waste, stock counts, valuation.

Enter information once: **confirming a purchase atomically** creates the stock batches, updates
ingredient prices and recipe costs, and updates the supplier balance.

![Dashboard](docs/screenshots/02-dashboard.png)

## Status — v0.4.0, Stage 4: production hardening ✅ (pilot-ready, not yet general release)
An adversarial audit found and fixed:
* **3 critical defects:**
  * sync records were silently missing for writes outside the web app;
  * dependencies had known CVEs;
  * backup verification ignored failures.
* **8 high-severity defects**, plus medium ones. All are covered by tests or benchmarks.

Evidence:
* 130 backend tests, including a randomized model check of FIFO and ledgers, adversarial security tests, fault injection and recovery;
* 0 known CVEs and 0 accessibility violations;
* 5 of 5 real failure simulations;
* benchmarks on a 263 MB restaurant: 25 concurrent staff within all response-time targets, and a 2 Mbit/s offline backlog that converges row for row.

**[Production-readiness report](docs/PRODUCTION-READINESS.md)** (verdict and remaining gates) ·
[Audit](docs/AUDIT.md) · [Security](docs/SECURITY.md) · [Performance](docs/PERFORMANCE.md) ·
[Backup & DR](docs/DISASTER-RECOVERY.md) · [Deployment](docs/DEPLOYMENT.md) · [Changelog](CHANGELOG.md)

Earlier stages: [MVP report](docs/MVP-REPORT.md) · [Sync test report](docs/SYNC-TEST-REPORT.md)

| | |
|---|---|
| Install & operate | [docs/INSTALL.md](docs/INSTALL.md) · **Windows:** [docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md) (double-click `Setup-Windows.cmd`) · [Docker verification](docs/DOCKER-VERIFICATION.md) · [sync recovery runbook](docs/SYNC-RUNBOOK.md) |
| API | [docs/API.md](docs/API.md) · OpenAPI [branch](docs/api/openapi.json) / [cloud](docs/api/openapi-cloud.json) · live at `/api/docs` |
| Database | [migration](backend/migrations/versions/) · [design](docs/02-database-design.md) |
| Test results | [docs/test-results.txt](docs/test-results.txt) |
| Architecture & decisions | [docs/01-architecture.md](docs/01-architecture.md) · [ADRs](docs/adr/) · [business rules](docs/03-business-rules.md) |
| Roadmap | [docs/05-roadmap.md](docs/05-roadmap.md) |
| Legal research notes (Uganda, Eritrea; not legal advice) | [docs/07-legal-and-compliance.md](docs/07-legal-and-compliance.md) |

## Quick start (developer)
```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
```
```bash
.venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --port 8000
```
```bash
cd frontend && npm ci && npm run dev
```
Then open http://localhost:5173. Optional demo data: `backend/.venv/bin/python scripts/seed_demo.py`.

## Stack
React 18 + TypeScript PWA (TanStack Query, IndexedDB drafts) · Python FastAPI modular monolith · SQLAlchemy 2 + Alembic ·
PostgreSQL 16 (branch + cloud, schema per branch) · transactional outbox sync worker · Docker Compose + Caddy (HTTPS).
