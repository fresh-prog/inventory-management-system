# 01 — System Architecture

Status: **Draft for owner review** · Stage 1

## 1. Shape: an offline-first modular monolith deployed in two tiers

```
                         ┌───────────────────────── CLOUD (optional for daily ops) ─────────────────────────┐
                         │  FastAPI (same codebase, "cloud" profile)   PostgreSQL (all branches, per-branch   │
                         │  Sync inbox ◄──── HTTPS + mTLS ────┐        row isolation)   Object storage (files) │
                         │  Remote mgmt UI, consolidated reports  │     Encrypted backups                      │
                         └─────────────────────────────────────┼──────────────────────────────────────────────┘
                                                               │  outbox → inbox, ack per event
 RESTAURANT LAN  ┌─────────────────────────────────────────────┼──────────────────────────────┐
                 │  BRANCH SERVER (Docker Compose)              │                              │
                 │  ┌──────────┐  ┌──────────────────────────────────────────┐  ┌───────────┐  │
                 │  │  Caddy   │─►│ FastAPI app (modular monolith)            │─►│PostgreSQL │  │
                 │  │ HTTPS    │  │  ingredients │ purchasing │ inventory     │  │ (system of│  │
                 │  │ (LAN CA) │  │  identity │ audit │ sync │ files │ ocr     │  │  record)  │  │
                 │  └────▲─────┘  └──────────────┬───────────────────────────┘  └───────────┘  │
                 │       │                       │ background worker (same image):            │
                 │       │                       │ outbox publisher, OCR jobs, backups,       │
                 │       │                       │ recipe-cost recalculation, disk monitor    │
                 └───────┼───────────────────────┴────────────────────────────────────────────┘
                         │ HTTPS on LAN
        ┌────────────────┴──────────────┐
        │ React + TypeScript PWA         │  phones · tablets · desktops
        │ IndexedDB: reference-data cache, drafts, command queue (idempotency keys) │
        └────────────────────────────────┘
```

* **One codebase, two deployment profiles** (`branch`, `cloud`). There is no
  separate cloud product to keep in step.
* **The branch server is authoritative** for everything that happens in the
  branch: purchases, payments recorded there, stock, FIFO. The internet is
  never on the critical path.
* **The cloud** receives each branch's immutable event stream, serves remote
  managers (labelled with "data as of <last sync>"), holds backups, and
  publishes master-data changes made centrally (for example HQ recipes)
  down to branches.

## 2. Backend module layout (planned)

```
backend/app/
  domain/          pure business rules, no I/O (units, costing, fifo, payables, invoice)  ← Stage 1
  modules/
    ingredients/   models · repository · service · api
    recipes/
    purchasing/
    suppliers/
    inventory/
    identity/      users, roles, sessions, MFA
    sync/          outbox, inbox, acknowledgements, conflict queue
    audit/
    files/         uploads, antivirus hook, content hashing
    ocr/           engine adapters + draft extraction
  platform/        db session/transactions, settings, logging, errors, idempotency
  main.py
```

Rule: modules talk to each other only through **service interfaces in-process**,
never by reaching into another module's tables. Cross-module writes that must be
atomic (such as confirming a purchase) are orchestrated by one application
service inside **one database transaction**.

## 3. The "confirm purchase" transaction (the heart of the integration)

Everything below runs in one PostgreSQL transaction (`SERIALIZABLE` is not needed;
`READ COMMITTED` + row locks + unique constraints are enough):

1. Check the idempotency key (unique index) → if already processed, return the original result.
2. Validate the invoice arithmetic (`domain/invoice.py`) and supplier/ingredient state.
3. Insert `purchase_invoice` (status `confirmed`) and `purchase_line`s.
4. For each stock line: convert to the ingredient base unit, insert a `stock_batch` and a `stock_movement(RECEIPT)`.
5. Insert an `ingredient_price_history` row and update `ingredient_current_cost`.
6. Insert a `supplier_ledger_entry(INVOICE, +total)`.
7. Insert `audit_log` rows.
8. Insert `outbox_event`s: `purchase.confirmed` (for sync) and `ingredient.cost_changed` (for recipe recalculation).
9. COMMIT. Any failure rolls back all of it, so a purchase can never be partially posted.

Recipe-cost recalculation and dashboard refresh happen **after** commit, through
the outbox (a background worker). They are derived projections: they can be
rebuilt at any time and never block the purchase.

## 4. Consumption / FIFO transaction

1. Idempotency check.
2. `SELECT … FROM stock_batch WHERE ingredient_id=? AND branch_id=? AND remaining_quantity>0 AND NOT quarantined ORDER BY received_at, sequence FOR UPDATE`.
3. `domain.fifo.allocate_fifo()` decides the allocations.
4. Update the batches, insert a `stock_movement` (CONSUMPTION/PRODUCTION/ADJUSTMENT) plus one `stock_allocation` row per batch touched.
5. Outbox + audit, then COMMIT.

Row locks serialise concurrent consumers of the same ingredient. Different
ingredients do not block each other. A `CHECK (remaining_quantity >= 0)`
constraint is the last line of defence.

## 5. Frontend

* React 18 + TypeScript + Vite, installable PWA (service worker, offline shell).
* TanStack Query for server state; IndexedDB (via Dexie) for:
  * a reference-data cache (ingredients, suppliers, units) → instant local search;
  * drafts (purchases, counts, OCR reviews);
  * a command queue: each command carries a client-generated UUIDv7 and an idempotency key.
* A sync indicator shows: connected to branch / branch online to cloud / N pending.
* A success toast appears **only after the server acknowledges** the command. Queued items show "Saved on this device — not yet posted".

## 6. Cross-cutting

| Concern | Approach |
|---|---|
| IDs | UUIDv7 (time-ordered, generated offline, globally unique) |
| Money/quantities | `NUMERIC` in the DB, `Decimal` in Python, strings in JSON. Never floats. (ADR-0004) |
| Migrations | Alembic; forward-only in production; `pg_dump` taken before every migration |
| Logging | Structured JSON (structlog), correlation ID per request |
| Health | `/health/live`, `/health/ready` (DB, disk, queue depth, last sync) |
| Backups | WAL archiving + nightly `pg_dump`, encrypted (age/GPG), copied to the cloud; restore tested monthly |
| Security | see ADR-0008 |

Architecture decisions are recorded in [`docs/adr/`](adr/).
