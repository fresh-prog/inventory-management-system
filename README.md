# Restaurant Ingredient Costing, Purchasing & FIFO Inventory Platform

One integrated, offline-first platform for restaurants:

1. **Ingredients & Food Costing**: ingredients, unit conversion, recipes, yields, current-cost pricing.
2. **Purchasing & Suppliers**: invoices (manual + OCR review), supplier payments, balances.
3. **Stock & FIFO Inventory**: batches, automatic FIFO consumption costing, adjustments, counts, valuation.

Each fact is entered once: confirming a purchase atomically creates stock batches, updates
prices, supplier balances and recipe costs.

## Status: Stage 1, Foundation (design + verified domain core)

| Area | Where |
|---|---|
| Requirements analysis, ambiguities, open questions | [docs/00-requirements-analysis.md](docs/00-requirements-analysis.md) |
| Architecture | [docs/01-architecture.md](docs/01-architecture.md) |
| Architecture Decision Records | [docs/adr/](docs/adr/) |
| Database design (DDL validated on PostgreSQL 16) | [docs/02-database-design.md](docs/02-database-design.md), [docs/schema/schema_draft.sql](docs/schema/schema_draft.sql) |
| Business rules | [docs/03-business-rules.md](docs/03-business-rules.md) |
| API contracts | [docs/04-api-contracts.md](docs/04-api-contracts.md) |
| Roadmap | [docs/05-roadmap.md](docs/05-roadmap.md) |
| Acceptance tests | [docs/06-acceptance-tests.md](docs/06-acceptance-tests.md) |
| Legal research notes (Uganda, Eritrea) | [docs/07-legal-and-compliance.md](docs/07-legal-and-compliance.md) |
| Domain engines (pure Python, no I/O) | [backend/app/domain/](backend/app/domain/) |

Planned stack: React + TypeScript PWA · Python/FastAPI modular monolith · PostgreSQL
(branch + cloud) · IndexedDB · Docker · Caddy (HTTPS).

## Run the tests

```bash
cd backend
python -m pytest -q
```
