# Performance — reproducible benchmarks (Stage 4)

Raw results: [bench/](bench/). Hardware: 4 vCPU, 16 GB RAM, SSD, PostgreSQL 16 on the same machine. The
load generator also ran on this machine and competed for CPU, so these figures are conservative. The target
branch server is 4 cores / 8 GB.

## Reproduce
```bash
scripts/bench/reset_db.sh
```
```bash
APP_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/restaurant_bench backend/.venv/bin/python scripts/bench/generate.py 1
```
```bash
backend/.venv/bin/python scripts/bench/endpoints.py http://127.0.0.1:8201 5
```
```bash
backend/.venv/bin/python scripts/bench/load.py http://127.0.0.1:8201 --users 25 --seconds 60
```
```bash
cd backend && .venv/bin/python ../scripts/bench/sync_bench.py
```
```bash
cd frontend && node e2e/render-perf.mjs http://127.0.0.1:8202
```

## Dataset (generated through the real business services: FIFO, ledgers, re-costing, change capture)
3,000 ingredients · 60 suppliers · 300 published recipes (8–15 lines each) · 10,000 purchase invoices
(30,000 lines / batches) · 25,000 consumptions · 2,000 production runs · ~4,000 payments · 3,000 waste
entries → **80,569 stock movements, 51,110 FIFO allocations, 28,392 cost snapshots, 46,600 outbox events, 263 MB**.
Generation cost per business operation (single process): ingredient 8 ms · purchase with recipe re-costing
47 ms · consumption 9–11 ms · production run 57–65 ms · payment 13–15 ms. Change capture adds no measurable
cost (purchase 47.2 ms without vs 46.8 ms with).

## Single-request latency (median of 5, same data) — before → after
| Endpoint | Before | After | Change |
|---|---|---|---|
| Purchases list, any page or filter | 2,520–2,555 ms | **46–56 ms** | ≈50× (SQL pagination) |
| Integrity check (all records) | 3,168 ms | **107 ms** | 30× (SQL) |
| Recipes list (300, costed) | 470 ms | **42 ms** | 11× (latest snapshot) |
| Dashboard | 787 ms, 1,460 KB | **215 ms, 212 KB** | 3.7×, 7× smaller |
| Stock levels (3,000) | 348 ms | **110 ms** | column queries + fast JSON |
| Valuation report | 318 ms | **105 ms** | |
| Movements page | 85 ms | **21 ms** | index |
| Ingredient search, purchase/recipe detail, batches | 14–19 ms | 13–22 ms | already fine |

## Concurrent employees (60 s, each user on its own device, think time 0.2–1 s; mixed reads and writes)
| | Before (2 workers) | After (2 workers) | After (4 workers) | After, **60 users** (4 workers) |
|---|---|---|---|---|
| Throughput | 3.5 req/s | 34.9 req/s | 36.4 req/s (generator-bound) | **60 req/s** |
| Errors | 0 | 0 | 0 | 0 |
| API memory (peak) | 1,916 MB | 480 MB | 713 MB | 902 MB |
| p95 ingredient search | 2,678 ms | — | **100 ms** | 557 ms |
| p95 record usage (FIFO) | 3,949 ms | — | **127 ms** | 823 ms |
| p95 confirm purchase | 7,502 ms | — | **258 ms** | 1,514 ms |
| p95 dashboard | 14,693 ms | — | **412 ms** | 1,384 ms |
| p95 purchases page | 43,896 ms | — | **113 ms** | 582 ms |

**Targets (00-requirements-analysis):** search p95 < 150 ms ✅ (100) · confirm purchase p95 < 500 ms ✅ (258) ·
consumption p95 < 300 ms ✅ (127) · dashboard p95 < 1 s ✅ (412) · 25 concurrent users ✅. At 60 very
active users everything still works with no errors, but latency rises because the CPU is saturated. Plan on about
30 heavy simultaneous users per 4-core branch server.

Where the time goes now (`pg_stat_statements`, after): the database is not the bottleneck (4% CPU at 25 users).
The remaining cost is Python request handling. The commit lock that orders sync records averages 6 ms of wait
at 36 req/s.

## Long offline periods (sync)
| | Before | After (gzip + byte-bounded batches + multi-row apply) |
|---|---|---|
| Initial full copy of the large branch | 374,175 rows, **227.5 MB**, 476 s on LAN (≈15 min at 2 Mbit/s) | 396,039 rows, **31.1 MB**, 214 s on LAN (**≈2 min at 2 Mbit/s**) |
| Offline backlog, drained over a **throttled 2 Mbit/s link** | 4,672 transactions, 19.4 MB, **145 s** | 4,169 transactions, **1.4 MB, 49 s** (86 tx/s) |
| Convergence after drain (every row compared) | identical (396,039 rows) | **identical (408,610 rows)** |
Target "a 7-day backlog drains in < 10 min on 2 Mbit/s" ✅: a busy week (≈5,000 transactions) drains in about a minute.

## Frontend (Pixel 7 emulation, CPU slowed 4×, large restaurant)
| Screen | Time to usable |
|---|---|
| Sign-in → dashboard | 1.2 s |
| Ingredients (3,000) | 1.0 s · **search keystroke → result 72 ms** (local, instant) |
| Stock levels (3,000) | 3.6 s → **0.6 s** (incremental rendering) |
| Purchases page | 0.4 s |
| Recipes (300) | 0.7 s |
| New purchase form | 0.3 s |
| JS heap after the tour | 42 → 28 MB |

## Database
Indexes added for every hot lookup (migration `0004perf`, built in under 1 s on the full dataset). Every
list endpoint is paginated or bounded. Connection pool 10 + 20 overflow per worker. Background work (sync,
backups) runs in separate processes, so the UI never waits on it.
