# ADR-0001: Modular monolith rather than microservices

**Context.** The three modules must update purchases, batches, balances and price history atomically. Each branch runs on modest hardware, maintained by non-specialists, often with no internet.

**Decision.** One deployable FastAPI application with strictly separated domain modules (`ingredients`, `recipes`, `purchasing`, `suppliers`, `inventory`, `identity`, `sync`, `audit`, `files`, `ocr`). A background worker runs from the same image. Modules interact through in-process service interfaces; no module writes another module's tables directly.

**Consequences.**
+ Real ACID transactions across modules: no distributed sagas for "confirm purchase".
+ One container plus PostgreSQL per branch: simple to install, back up and upgrade.
− Modules scale together. That is acceptable at restaurant scale (tens of users per branch).
* Exit path: module boundaries and the outbox events let a module (for example OCR) move out later if load requires it.

**Rejected:** microservices (distributed transactions, operational cost, no measured need); a serverless/cloud-only design (fails the offline requirement).
