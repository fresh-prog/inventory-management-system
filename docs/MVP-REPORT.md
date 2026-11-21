# MVP Delivery Report — Stage 2

Date: 2026-11-21 · Branch: `claude/happy-bardeen-2wr1tt`

## 1. What was delivered

| Phase | Delivered |
|---|---|
| 1 Foundation | Monorepo (`backend/`, `frontend/`, `scripts/`, `docs/`), FastAPI modular monolith, PostgreSQL 16, Alembic migration (incl. sequences and append-only triggers), Argon2id auth with lockout, 5 roles / 22 permissions, problem-JSON errors, structured JSON logs with request IDs, health checks, idempotency keys, audit log, transactional outbox, responsive React/TypeScript PWA, Docker/Caddy/CI files |
| 2 Ingredients | Create/edit with optimistic locking, categories, purchase and consumption units, ingredient-specific conversion rules (never assumes density), supplier associations, reference/quoted prices, price history (append-only), current cost, minimum stock |
| 3 Recipe costing | Recipes with versions (draft → published → retired), gross/net lines with prep loss (applied once), cooking shrinkage (yield only), portions, target food cost, suggested price, food cost %, gross contribution, live cost preview, cost snapshots on publish and on every price change, cost history with per-portion change |
| 4 Purchases & suppliers | Multi-line invoices with line/invoice discounts and tax, arithmetic checks, duplicate-invoice protection, dry-run validation, atomic confirmation, voids; supplier profiles, statements (ledger), unpaid/partially paid/paid status, payments split across invoices, credits, payment voids, duplicate-reference protection |
| 5 FIFO inventory | A batch per purchase line, receipt movements, deterministic FIFO (received date, sequence), row-locked allocation, immutable movements and allocations, valuation, insufficient-stock block with an authorised override |
| 6 Recipe consumption | Production runs from the **published** version (by portions or batches), requirement preview, FIFO-costed deduction exactly once, idempotent; production is typed `production`, separate from the reserved `sale` type and the `finished_good` stock kind |
| 7 Stock management | Stock levels, low-stock alerts, waste/adjustments with required reasons and permissions, physical counts (enter → review → approve) with variances (shortage FIFO-costed, surplus at current cost), movement history |
| 8 Dashboard & reports | Inventory value, month purchases, payables, low stock, recent purchases, price changes, recipe food cost, FIFO consumption (production, usage, waste, count shortages). Reports: valuation (CSV export), usage and waste, purchases by supplier, recipe costs, price changes, **integrity reconciliation** |
| 9 Offline foundation | Runs entirely on the local branch server (no internet needed): local auth and database, durable outbox written in the same transaction as each change, connection indicator, app shell cached by a service worker, device drafts in IndexedDB that survive crashes and disconnections, idempotent retries. **Cloud sync is not implemented, so the app is not labelled hybrid-ready.** |

## 2. MVP acceptance criteria

| # | Criterion | Evidence (automated unless noted) | Result |
|---|---|---|---|
| 1 | Correct ingredient unit costs | `test_api_ingredients.py`: 25 kg @ 50 → 0.002/g; litres, units, oil with 1 l = 910 g; refused without a rule; the newer-dated price wins; `test_units.py` | ✅ |
| 2 | Correct recipe calculations | `test_api_recipes.py::test_recipe_cost_portion_price_and_yield`: batch 92.00, portion 4.60, food cost 30.67 %, contribution 10.40 | ✅ |
| 3 | Correct yield calculations | Same test: raw 6000 g → usable 5800 g → cooked 4350 g (25 % shrink) → 217.5 g/portion; net line grossed up once | ✅ |
| 4 | Correct recommended selling prices | 4.50 ÷ 0.30 = **15.00** (`test_brief_example_recommended_price`, API and domain) | ✅ |
| 5 | Correct supplier balances | `test_partial_and_full_payments_and_balances`; ledger = Σ outstanding invoices (integrity check) | ✅ |
| 6 | Correct partial payments | 100 invoice: 30 → partially paid (70 left); split payment 70+20 across two invoices; over-allocation by 0.01 rejected; credits | ✅ |
| 7 | Correct purchase-to-stock integration | `test_confirm_purchase_updates_everything_once`: exactly 1 invoice, 3 lines, 3 batches, 3 movements, 3 price-history rows, 1 ledger entry; discount spread over lines, VAT excluded from stock cost | ✅ |
| 8 | Correct FIFO allocations | Master example: 10 kg @ 9 + 5 kg @ 11 = **145**; partial batch; spanning 3 batches; same-timestamp tie-break | ✅ |
| 9 | Correct remaining inventory valuation | After the example: B 5 kg @ 11 + C 10 kg @ 12 = **175**; dashboard = direct SQL sum | ✅ |
| 10 | Correct stock adjustments | Reason required ("other" needs a note), FIFO-costed waste, waste-by-reason report, count variances −1.5 kg (−13.50) and +500 g (+1.00) | ✅ |
| 11 | No duplicate transactions | Idempotent replays (purchase, consumption, production); 6 concurrent identical submissions → 1 purchase; duplicate invoice ref → 409; duplicate payment ref → 409; **20 concurrent 1 kg consumptions of 10 kg → exactly 10 succeed, never negative**; 5 concurrent full payments → exactly 1 succeeds | ✅ |
| 12 | No unauthorised financial changes | Role tests (kitchen/viewer cannot pay or buy; a manager cannot void payments; an override needs `stock.override`); branch isolation; revoked sessions; DB triggers block UPDATE/DELETE on movements, allocations, ledger and audit | ✅ |
| 13 | Reliable database recovery | Atomic rollback (failure injected mid-purchase leaves zero rows); `test_backup_and_restore_round_trip`: encrypted backup → restore to a new DB → identical figures, triggers intact | ✅ |
| 14 | Responsive mobile interface | Playwright on iPhone 13, iPad and 1440 px desktop: zero horizontal overflow, bottom navigation on phones, stacked line items; screenshots below | ✅ (automated + visual review) |

Full output: [test-results.txt](test-results.txt) — **61 backend tests, 4 frontend unit tests, type check, 12 end-to-end UI checks — all passing.**

## 3. Screenshots
Desktop: [dashboard](screenshots/02-dashboard.png) · [ingredients](screenshots/03-ingredients.png) · [FIFO batches](screenshots/04-ingredient-fifo-batches.png) ·
[price history](screenshots/05-ingredient-price-history.png) · [recipe costing](screenshots/06-recipe-costing.png) · [recipe cost history](screenshots/07-recipe-cost-history.png) ·
[recipe editor](screenshots/08-recipe-editor-live-cost.png) · [new purchase](screenshots/09-new-purchase.png) · [confirm](screenshots/10-purchase-confirm.png) ·
[purchase detail](screenshots/11-purchase-detail.png) · [suppliers](screenshots/12-suppliers.png) · [supplier statement](screenshots/13-supplier-statement.png) ·
[stock levels](screenshots/14-stock-levels.png) · [usage with FIFO result](screenshots/15-usage-fifo-result.png) · [production](screenshots/16-production.png) ·
[stock count](screenshots/17-stock-count.png) · [movements](screenshots/18-movements.png) · [valuation report](screenshots/19-report-valuation.png) ·
[integrity checks](screenshots/20-integrity-checks.png) · [users](screenshots/21-settings-users.png) · [server unreachable, draft kept](screenshots/22-server-unreachable-draft-kept.png)
Phone: [dashboard](screenshots/m1-dashboard.png) · [new purchase](screenshots/m2-new-purchase.png) · [stock](screenshots/m3-stock.png) · [recipe](screenshots/m4-recipe.png) · Tablet: [dashboard](screenshots/t1-dashboard-tablet.png)

## 4. Defects found and fixed during verification
* Idempotency check used `rowcount`, which does not reliably report an `ON CONFLICT` skip → a replay re-executed. Fixed by checking with `RETURNING` (caught by tests).
* `FOR UPDATE` failed through an eager outer join on batch → supplier. Fixed by loading the supplier separately.
* The new append-only trigger exposed an UPDATE after insert in adjustments. Removed the update.
* The SPA static route compared relative and absolute paths, so `sw.js` and the manifest were served as HTML. Fixed and checked against path traversal.
* At startup with the server unreachable, the user was logged out and their draft hidden. Fixed with a cached profile and "drafts only" mode.
* Draft autosave race: typing before IndexedDB opened was not saved. Fixed.
* Phone purchase form was cramped. Line items now stack on small screens.

## 5. Known limitations (honest list)
* **Cloud synchronisation is not built.** Events accumulate durably in the outbox (visible in the UI), but there is no publisher, cloud inbox, acknowledgements or conflict queue yet. **The app is not hybrid-ready.**
* **Devices cannot post while the branch server is unreachable**, by design (ADR-0003): they keep drafts only. Purchase, usage and stock-count forms are saved as drafts on the device; production, waste and payment forms are not yet.
* **Docker image build not executed here** (no Docker daemon in this environment). The developer setup was fully tested.
* **MFA and refresh tokens are not implemented.** Tokens are 12-hour bearer tokens stored in localStorage (revocable server-side). MFA is required before any remote/cloud access is enabled.
* Rate limiting is per account (lockout after 5 failures). There is no per-IP limit yet.
* No OCR, file attachments, sub-recipes, purchase orders, POS integration, multi-currency or multi-branch transfers (as planned).
* The insufficient-stock override records the shortfall on the movement, but there is not yet a dedicated screen to resolve shortfalls (they are corrected by the next stock count).
* Stock count variances are measured against the system quantity at **approval** time. Movements recorded during a long count affect this. Counting during service is discouraged until count freezing is added.
* Recipe re-costing runs inside the purchase transaction (simple and consistent; fine at restaurant scale). It moves to a background worker if recipe counts grow large.
* Load tests against the response-time targets in 00-requirements-analysis have not been run yet (Stage 9).
* English UI only. Legal and tax items in 07-legal still require professional review (for example, EFRIS fields are captured but not validated).

## 6. Next-stage plan (proposed)
1. **Stage 3 — Hybrid sync:** outbox publisher worker, cloud profile with inbox and signed acks, master-data down-sync with version conflicts in a review queue, remote read-only dashboards marked "as of", MFA (TOTP) and refresh tokens for remote access; chaos tests (cut network, kill mid-sync, duplicate delivery).
2. **Stage 4 — Receipt OCR & attachments:** secure uploads, local OCR engine, review screen, ingredient matching with supplier aliases; never auto-posted.
3. **Stage 5 — Hardening:** Docker install verified on target hardware, nightly encrypted backups with off-site copy and a monthly restore drill, k6 load tests against the NFRs, per-IP rate limits, security review, translations.
4. **Stage 6 — Pilot** in one restaurant, then POS integration (sales consume finished goods or recipes) and supplier price comparison.
