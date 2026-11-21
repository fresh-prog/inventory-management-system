# ADR-0004: Exact decimal money and base-unit quantities

* Money: PostgreSQL `NUMERIC(18,4)`, Python `Decimal`, JSON **strings** (e.g. `"145.0000"`). Floats are rejected by the domain layer.
* Unit costs: `NUMERIC(20,8)` (cost per gram can be very small, e.g. UGX/g).
* Quantities are stored in the ingredient's **base unit** (g, ml or unit) as `NUMERIC(18,6)`. The entered unit and quantity are also kept for display and audit.
* Every money column has a sibling `currency CHAR(3)` (ISO 4217). Arithmetic across currencies raises an error.
* Full precision internally. Rounding (ROUND_HALF_UP to the currency's minor units: UGX 0, ERN 2, USD 2) happens only at posting and display. Rounding differences are recorded explicitly, never absorbed silently.
