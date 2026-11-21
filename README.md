# KitchenLedger — Restaurant Costing, Purchasing & FIFO Inventory

One integrated, offline-first platform for restaurants:

1. **Ingredients & food costing**: ingredients, dimension-aware units, recipes with versions, yields, current-cost pricing.
2. **Purchasing & suppliers**: multi-line invoices, supplier balances, partial payments, credits.
3. **Stock & FIFO inventory**: automatic batches, oldest-first costing, production, waste, stock counts, valuation.

Enter information once: **confirming a purchase atomically** creates the stock batches, updates
ingredient prices and recipe costs, and updates the supplier balance.

![Dashboard](docs/screenshots/02-dashboard.png)

## Status — Stage 2: MVP ✅
All 14 MVP acceptance criteria are demonstrated by automated tests: 61 backend tests on real PostgreSQL,
including concurrency, atomicity and backup/restore, plus 12 end-to-end UI checks on desktop, phone and tablet.
See **[docs/MVP-REPORT.md](docs/MVP-REPORT.md)** for the evidence, known limitations and next steps.
The app runs fully on a local branch server without internet. **Cloud sync is not built yet, so it is not "hybrid-ready".**

| | |
|---|---|
| Install & operate | [docs/INSTALL.md](docs/INSTALL.md) |
| API | [docs/API.md](docs/API.md) · [OpenAPI](docs/api/openapi.json) · live at `/api/docs` |
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
PostgreSQL 16 · Docker Compose + Caddy (HTTPS on the LAN).
