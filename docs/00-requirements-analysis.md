# 00 — Requirements Analysis

Status: **Draft for owner review** · Stage 1 · 2026-11-21

## 1. Repository inspection

The repository started empty (initial commit with a one-line README). There is
no existing code, schema, data or deployment to migrate or preserve.

## 2. Scope summary

One integrated application (modular monolith) with three business modules that
share one database and one transaction boundary:

| Module | Owns |
|---|---|
| M1 Ingredients & Costing | ingredients, units/conversions, recipes, recipe versions, current-cost projections |
| M2 Purchasing & Suppliers | suppliers, purchase invoices, OCR drafts, payments, payment allocations, credits |
| M3 Inventory (FIFO) | stock batches, stock movements, FIFO allocations, production, adjustments, stock counts |

Shared platform: identity & roles, branches & devices, audit log, outbox/inbox
sync, file storage, settings.

## 3. Ambiguities and the defaults chosen

Each item below is a decision I made to keep moving. **Every one is open for you
to change** — reply with the item number.

| # | Ambiguity | Default chosen (until you decide otherwise) |
|---|---|---|
| A1 | Which currencies? | One currency per branch (e.g. UGX in Kampala, ERN in Asmara). No FX conversion in MVP; cross-branch reports are per currency. |
| A2 | Are menu prices tax-inclusive? | Costing uses **tax-exclusive** selling prices. A per-branch setting will strip VAT from inclusive prices in a later stage. |
| A3 | "Current purchase-price basis" for recipe costing | **Latest confirmed purchase unit cost** per ingredient per branch. Weighted-average-of-on-hand is a planned alternative setting. |
| A4 | Inventory valuation method | Perpetual FIFO only (no LIFO/average). |
| A5 | Behaviour on insufficient stock | **Block** by default. Users with `stock.override` can post with a recorded *shortfall* that is costed at the current cost and flagged for reconciliation at the next stock count. |
| A6 | Is purchase-price VAT part of ingredient cost? | Recoverable input VAT is **excluded** from ingredient cost (configurable per branch for non-VAT-registered restaurants, where it is included). |
| A7 | Who is the system of record? | The **branch server** for its own transactions; the cloud aggregates, backs up and offers remote management. (ADR-0003) |
| A8 | Can tablets post transactions with no connection to the branch server? | No. Devices can save **drafts** offline (IndexedDB), but posting (FIFO, balances) needs the branch server, which runs on the restaurant LAN without internet. |
| A9 | OCR engine | Pluggable. Default local engine (Tesseract-class) on the branch server; optional cloud OCR when online. OCR only ever produces a draft. (ADR-0007) |
| A10 | Finished goods | Data model includes finished-goods items and batches from day one; UI comes in a later phase. |
| A11 | Recipe approval workflow | Draft → Approved → Published; only one published version per recipe at a time; published versions are immutable. |
| A12 | Number of languages | English in MVP; the UI is built for translation (Tigrinya, Arabic, Luganda and Swahili can be added later). |
| A13 | Invoice-total tolerance | 0.01 per line and 0.05 per invoice in the currency's major unit; any difference is shown, never hidden. Configurable. |

## 4. Open questions for the owner

1. How many branches at launch, and in which countries?
2. Is each restaurant VAT-registered (Uganda: EFRIS obligations)?
3. Do you have a POS today? Which one? (Affects the sales-integration design.)
4. What hardware will run the branch server (mini-PC, NAS, old laptop)?
5. Preferred cloud provider/region? This affects data-residency decisions (see 07-legal).
6. Do suppliers issue credit notes, or are returns handled informally?

## 5. Non-functional targets (proposed)

| Metric | Target on branch hardware (4-core, 8 GB RAM, SSD) |
|---|---|
| Ingredient/supplier search | p95 < 150 ms server, instant client-side from local cache |
| Confirm purchase (≤ 30 lines) | p95 < 500 ms |
| Consume / production posting (≤ 30 ingredients) | p95 < 300 ms |
| Dashboard load | p95 < 1 s |
| Concurrent users per branch | 25 without degradation |
| Sync backlog after 7 days offline | drains in < 10 min on a 2 Mbps link |
| RPO (branch) | ≤ 15 min (WAL archiving + nightly encrypted backup) |
| RTO (branch server replacement) | < 2 h from cloud copy |

Load tests (k6/Locust) arrive in the hardening stage to verify these numbers.
