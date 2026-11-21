# API Documentation (v1)

* **Interactive docs:** a running server serves Swagger UI at `/api/docs` and ReDoc at `/api/redoc`.
* **Machine-readable:** [`api/openapi.json`](api/openapi.json) (53 paths, generated from the code).
* The design contract is in [04-api-contracts.md](04-api-contracts.md). Differences from that draft are listed at the end.

## Conventions
| | |
|---|---|
| Base path | `/api/v1` |
| Auth | `POST /auth/login` → `{access_token}`; send `Authorization: Bearer <token>`. Tokens last one shift (12 h by default). Changing a user's role, deactivating them or resetting their password revokes their tokens. |
| Branch scope | Taken from the token. IDs belonging to another branch return 404/422. |
| Numbers | Money and quantities are **decimal strings** (`"145.0000"`), never floats. Send them as strings. |
| Posting writes | `POST /purchases`, `/purchases/{id}/void`, `/payments`, `/payments/{id}/void`, `/supplier-credits`, `/stock/consumptions`, `/stock/adjustments`, `/stock/production-runs`, `/stock/counts/{id}/approve` **require `Idempotency-Key`**. Repeating a request with the same key returns the original response with `Idempotent-Replay: true`. Reusing a key with a different body returns 409. |
| Concurrency | `PATCH /ingredients/{id}` and `PUT /suppliers/{id}` accept `If-Match: <version>`; if the version is stale they return 409. |
| Errors | RFC 9457 problem JSON: `{type,title,status,detail,code,fields}`. Codes include `VALIDATION_ERROR` (422), `BUSINESS_RULE` (422), `INSUFFICIENT_STOCK` (422), `CONFLICT` (409), `FORBIDDEN` (403), `UNAUTHORIZED` (401), `NOT_FOUND` (404). |
| Cost basis | Responses that carry costs include `cost_basis: "fifo"` (actual batch costs) or `"current"` (latest purchase prices). |

## Endpoint summary
| Area | Endpoints |
|---|---|
| Setup & auth | `GET /setup/status`, `POST /setup` (first run only), `POST /auth/login`, `POST /auth/logout-all`, `GET /me` |
| Users & settings | `GET/POST /users`, `PATCH /users/{id}`, `PATCH /settings/branch`, `GET /audit-log` |
| Ingredients | `GET /ingredients/units`, `GET /ingredients/categories`, `GET/POST /ingredients`, `GET/PATCH /ingredients/{id}`, `GET /ingredients/{id}/price-history`, `POST /ingredients/{id}/prices` |
| Recipes | `GET/POST /recipes`, `POST /recipes/cost-preview`, `GET /recipes/{id}`, `PUT /recipes/{id}/draft`, `POST /recipes/{id}/versions/{n}/publish`, `GET /recipes/{id}/versions/{n}`, `GET /recipes/{id}/cost-history`, `GET /recipes/{id}/production-runs` |
| Suppliers | `GET/POST /suppliers`, `GET/PUT /suppliers/{id}` (profile with invoices, payments and a running statement) |
| Purchases & payments | `GET /purchases` (filters: supplier, status, payment_status, dates), `POST /purchases/validate` (dry run), `POST /purchases`, `GET /purchases/{id}`, `POST /purchases/{id}/void`, `POST /payments`, `POST /payments/{id}/void`, `POST /supplier-credits` |
| Stock | `GET /stock`, `GET /stock/ingredients/{id}/batches`, `GET /stock/movements`, `POST /stock/consumptions`, `POST /stock/adjustments`, `GET /stock/production/requirements`, `POST /stock/production-runs`, `GET/POST /stock/counts`, `GET /stock/counts/{id}`, `PUT /stock/counts/{id}/lines`, `POST /stock/counts/{id}/approve` |
| Dashboard & reports | `GET /dashboard`, `GET /reports/inventory-valuation`, `/reports/purchases`, `/reports/consumption`, `/reports/recipe-costs`, `/reports/price-changes`, `/reports/integrity`, `GET /sync/status` |
| Ops | `GET /health/live`, `GET /health/ready` |

## Example: the master-spec FIFO case
```http
POST /api/v1/stock/consumptions
Idempotency-Key: 6f1c…
{"lines": [{"ingredient_id": "…", "quantity": "15", "unit": "kg"}]}
```
```json
{"cost_basis": "fifo", "total_cost": "145.0000", "lines": [{"ingredient_name": "Beef", "quantity_base": "15000.000000",
  "base_unit": "g", "total_cost": "145.0000", "shortfall_quantity": "0", "allocations": [
   {"quantity": "10000.000000", "unit_cost": "0.009000000000", "cost": "90.0000"},
   {"quantity": "5000.000000",  "unit_cost": "0.011000000000", "cost": "55.0000"}]}]}
```

## Differences from the Stage 1 API draft
* Purchases are confirmed in one call (`POST /purchases`) after an optional dry-run validation. Drafts live on the device (IndexedDB), not on the server.
* Recipe approval and publishing are a single step (`.../publish`, which needs `recipe.publish`).
* Tokens last one shift and are revocable via `token_version`. Refresh tokens and MFA are deferred (see the MVP report).
* `/files`, `/ocr/*` and `/sync/events` are not in this release.
