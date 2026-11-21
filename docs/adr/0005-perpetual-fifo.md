# ADR-0005: Perpetual FIFO with row-locked batches and immutable movements

* Every confirmed purchase line creates a `stock_batch`. Batches are never deleted. Only `remaining_quantity` decreases, and only through movements.
* FIFO order: `(received_at, sequence)`; `sequence` is a per-branch bigint from a DB sequence, which keeps ordering deterministic.
* Allocation logic is a pure function (`app/domain/fifo.py`). The service layer locks the candidate batches with `FOR UPDATE`, in FIFO order, which prevents deadlocks.
* Each consumption writes a `stock_movement` header plus `stock_allocation` lines (batch, qty, unit_cost). These are immutable. Corrections are reversal movements that restore quantity to the **same** batches at the **same** costs.
* Insufficient stock: policy `BLOCK` (default) or `ALLOW_SHORTFALL`, which requires the `stock.override` permission and a reason. A shortfall is stored as a `stock_shortfall` record costed at the current cost and is resolved at the next receipt or stock count.
* Quarantined batches (expired, disputed) are excluded from allocation.
* Invariant check (nightly and in tests): for each batch, `original − Σ allocations + Σ reversals = remaining`.
