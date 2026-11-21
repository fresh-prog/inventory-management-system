# ADR-0006: Separate current-cost and FIFO cost bases

| | **Current cost (pricing)** | **Actual cost (FIFO)** |
|---|---|---|
| Question | "What should this dish sell for at today's prices?" | "What did the stock we used actually cost?" |
| Source | `ingredient_current_cost` = latest confirmed purchase unit cost (per branch) | Unit costs of the batches the FIFO engine allocated |
| Used by | Recipe costing, recommended price, food-cost % alerts, menu engineering | Consumption/production cost, waste cost, inventory valuation, COGS reports |
| Code | `app/domain/costing.py` | `app/domain/fifo.py` |
| Stored as | `recipe_cost_snapshot` (per recipe version, per cost change) | `stock_allocation` rows |

Every report and screen shows a **"Cost basis: Current" or "Cost basis: FIFO actual"** label. A report never mixes the two in one figure. A variance report (actual vs. theoretical) shows them side by side, labelled.
