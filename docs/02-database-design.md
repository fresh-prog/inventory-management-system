# 02 — Database Design

The full DDL is in [`schema/schema_draft.sql`](schema/schema_draft.sql) (40 tables). It was loaded into
PostgreSQL 16, and constraint smoke tests passed (see "Verification" below). Stage 2
turns it into Alembic migrations.

## Entity overview

```
branch ─┬─ device            app_user ── user_branch_role ── role ── role_permission
        │
        ├─ ingredient_branch_setting ── ingredient ─┬─ ingredient_conversion   (cross-dimension rules)
        ├─ ingredient_current_cost  ────────────────┤  (CURRENT cost basis)
        ├─ ingredient_price_history ────────────────┤  (append-only)
        │                                           ├─ supplier_ingredient ── supplier ── supplier_item_alias
        │                                           └─ stock_item (ingredient | finished_good)
        │
        ├─ purchase_invoice ── purchase_line ──1:1── stock_batch ── stock_allocation ── stock_movement
        │       │                                                        │
        │       ├─ stored_file (original receipt)                        ├─ stock_shortfall
        │       └─ payment_allocation ── supplier_payment               └─ production_run ── recipe_version
        ├─ supplier_credit                                                                  │
        ├─ supplier_ledger_entry (Σ = payable balance)          recipe ── recipe_version ── recipe_line
        ├─ stock_count ── stock_count_line                                       └─ recipe_cost_snapshot
        └─ outbox_event · inbox_event · sync_conflict · idempotency_record · audit_log
```

## Key integrity rules enforced by the database

| Rule | Mechanism |
|---|---|
| Stock never negative; never more than received | `CHECK (remaining_quantity >= 0 AND remaining_quantity <= original_quantity)` |
| One batch per confirmed purchase line | `stock_batch.purchase_line_id UNIQUE` |
| Same supplier invoice cannot be confirmed twice | partial unique index `(supplier_id, invoice_ref) WHERE status='confirmed'` |
| Duplicate submissions (offline retries) | `UNIQUE (branch_id, idempotency_key)` on invoices, payments, movements, production |
| Duplicate payment references | partial unique `(supplier_id, method, reference)` |
| Adjustments need a reason | `CHECK (type <> 'adjustment' OR reason IS NOT NULL)` |
| One published version per recipe | partial unique index on `recipe_version (recipe_id) WHERE status='published'` |
| Ledger entry created once per source document | `UNIQUE (entry_type, source_id)` on `supplier_ledger_entry` |
| Sync events applied at most once | `inbox_event PRIMARY KEY (source, event_id)` |
| Deterministic FIFO order + fast lookup | `sequence` from a DB sequence; partial index `stock_batch_fifo` on open batches |

Rules the database cannot express, such as "allocations ≤ outstanding balance" and
"FIFO allocation correctness", are enforced in the domain layer **inside the same
transaction, under row locks** (the invoice row is locked `FOR UPDATE` before
allocating a payment). They are also re-checked by nightly invariant jobs.

## Raw vs. production vs. finished goods (double-deduction prevention)

* `stock_item.kind = 'ingredient'`: raw stock, created by purchases.
* `production_run` issues ingredients **once** (`movement_type = production_issue`, FIFO-costed). Its output can create a `finished_good` batch whose unit cost = FIFO issue cost ÷ actual output.
* Future POS sales of pre-produced items consume the `finished_good` stock item, **never** the raw ingredients. Made-to-order dishes (no production run) deduct raw ingredients from the recipe at the moment of sale. Each menu item is configured as one or the other, never both.

## Verification performed in Stage 1

The schema was loaded into PostgreSQL 16.  These inserts were all correctly **rejected**:
remaining > original · negative remaining · same supplier invoice confirmed twice ·
reused idempotency key · adjustment without a reason.
