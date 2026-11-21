# 03 — Business Rules

Rule IDs are referenced from code and tests. ✅ = implemented and tested in Stage 1 (pure domain layer).

## Units (BR-UNIT)
| ID | Rule | Status |
|---|---|---|
| BR-UNIT-1 | Supported units: g, kg, ml, l, unit. Each belongs to one dimension (mass, volume, count). | ✅ `units.py` |
| BR-UNIT-2 | Same-dimension conversions use exact factors (1 kg = 1000 g; 1 l = 1000 ml). | ✅ |
| BR-UNIT-3 | Cross-dimension conversion (kg↔l, unit↔g) only via an ingredient-specific rule. Never assumed. | ✅ |
| BR-UNIT-4 | Stock is held in the ingredient's base unit; the entered unit and quantity are also retained. | schema |
| BR-UNIT-5 | Floats are rejected; all quantities are `Decimal`. | ✅ |

## Recipe costing — CURRENT basis (BR-COST)
| ID | Rule | Status |
|---|---|---|
| BR-COST-1 | Line cost = gross base quantity × current cost per base unit. | ✅ `costing.py` |
| BR-COST-2 | Batch cost = Σ line costs; cost per portion = batch cost ÷ portions per batch. | ✅ |
| BR-COST-3 | Recommended price = cost per portion ÷ target food-cost rate (4.50 ÷ 0.30 = 15.00). | ✅ |
| BR-COST-4 | Food-cost % = cost per portion ÷ tax-exclusive selling price; gross contribution = price − cost per portion. | ✅ |
| BR-COST-5 | A recipe is flagged when food-cost % > target. | ✅ |
| BR-COST-6 | Target food-cost % is configurable per recipe version (default per branch setting, e.g. 30 %). | schema |
| BR-COST-7 | A confirmed purchase that changes an ingredient's current cost triggers recalculation of every published recipe that uses it (directly or through sub-recipes), stored as a new `recipe_cost_snapshot`. Old snapshots are kept. | Stage 3 |
| BR-COST-8 | Current cost = latest confirmed purchase cost per base unit (net of discounts, excluding recoverable VAT). | Stage 3 |

## Yield & losses (BR-YIELD)
| ID | Rule | Status |
|---|---|---|
| BR-YIELD-1 | A recipe line is either GROSS (issued quantity) or NET (usable quantity after prep loss). | ✅ |
| BR-YIELD-2 | NET lines are grossed up **once**: gross = net ÷ (1 − prep loss). GROSS lines are never adjusted again. | ✅ |
| BR-YIELD-3 | Cooking shrinkage reduces the cooked output weight; it never increases ingredient quantities or cost. Cost is spread over the stated portions per batch. | ✅ (by design) |
| BR-YIELD-4 | Production issues the gross quantities from stock. Actual yield is recorded on the production run; the variance from expected yield is reported, not re-deducted. | Stage 5 |
| BR-YIELD-5 | Three quantities are always distinguishable: purchased (batch), issued to production (movement), usable output produced (production run). | schema |

## Purchasing (BR-PUR)
| ID | Rule | Status |
|---|---|---|
| BR-PUR-1 | Line total = qty × unit price − line discount; invoice total = Σ lines − invoice discount + tax. | ✅ `invoice.py` |
| BR-PUR-2 | Entered totals must match the computed totals within tolerance (0.01/line, 0.05/invoice); differences are shown, never hidden. | ✅ |
| BR-PUR-3 | Confirming a purchase is one atomic transaction: invoice + lines + batches + receipt movements + price history + current cost + supplier ledger + audit + outbox. | Stage 4 |
| BR-PUR-4 | A supplier invoice reference can be confirmed only once per supplier. | schema ✅ |
| BR-PUR-5 | OCR output is a draft. It never posts to stock or balances without user review and confirmation. | Stage 6 |
| BR-PUR-6 | A confirmed purchase is never edited; corrections are a void (reversing all effects, allowed only if its batches are unconsumed) or a supplier credit/adjustment. | Stage 4 |

## Payables (BR-PAY)
| ID | Rule | Status |
|---|---|---|
| BR-PAY-1 | Outstanding = invoice total − POSTED allocations − POSTED credits. Draft or voided items don't count. | ✅ `payables.py` |
| BR-PAY-2 | Status: outstanding ≤ 0 → paid; < total → partially paid; otherwise unpaid. | ✅ |
| BR-PAY-3 | An allocation cannot exceed the outstanding balance (validated under a row lock on the invoice). | ✅ (logic) |
| BR-PAY-4 | Every payment needs an idempotency key; repeats are rejected or return the original result. | ✅ (logic) + schema |
| BR-PAY-5 | Supplier balance = Σ supplier_ledger_entry; every entry links to its source document. | schema |
| BR-PAY-6 | One payment may be split across several invoices; an invoice may receive many payments. | schema |

## FIFO inventory (BR-FIFO)
| ID | Rule | Status |
|---|---|---|
| BR-FIFO-1 | Oldest eligible batch first, ordered by (received_at, sequence). | ✅ `fifo.py` |
| BR-FIFO-2 | Consumption cost = Σ(allocated qty × batch unit cost). Example A10@9, B10@11, C10@12; consume 15 → 145, remaining B5@11, C10@12. | ✅ |
| BR-FIFO-3 | Only batches of the same ingredient and branch, not quarantined and with remaining > 0, are eligible. | ✅ |
| BR-FIFO-4 | Insufficient stock blocks the transaction by default; an override (permission + reason) records a shortfall. | ✅ (logic) |
| BR-FIFO-5 | Allocations are immutable; corrections are reversals back to the same batches at the same cost. | Stage 5 |
| BR-FIFO-6 | Concurrency: batches are locked `FOR UPDATE` in FIFO order inside the transaction. | Stage 5 |
| BR-FIFO-7 | Inventory value = Σ remaining × unit cost (FIFO basis). | ✅ |
| BR-FIFO-8 | Mixing currencies within one ingredient's batches is an error. | ✅ |

## Adjustments & counts (BR-ADJ)
| ID | Rule |
|---|---|
| BR-ADJ-1 | Adjustments (spoilage, expired, damaged, prep waste, staff meal, other) require `stock.adjust` and a reason, and are FIFO-costed as waste. |
| BR-ADJ-2 | A stock count snapshots expected quantities when it starts; counted − expected = variance. Approving it (needs `stock.count.approve`, by a different user than the counter where staff allows) posts count adjustments. Increases create an adjustment batch at the current cost. |
| BR-ADJ-3 | Nothing is ever deleted; every change appears in movement history and the audit log. |

## Sync (BR-SYNC)
| ID | Rule |
|---|---|
| BR-SYNC-1 | Every record and command has a UUIDv7 and an idempotency key generated at origin. |
| BR-SYNC-2 | An outbox row is written in the same transaction as the change and kept until a verified ack (then 90 days). |
| BR-SYNC-3 | Financial and stock records are branch-owned and append-only, so there is no last-write-wins. Master-data version conflicts go to a review queue. |
| BR-SYNC-4 | The UI shows a success message only after the server commits. Device-queued items are labelled "not yet posted". |
