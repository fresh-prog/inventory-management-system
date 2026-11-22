"""Sequential latency of individual heavy endpoints (median of N runs), for before/after comparisons.
Usage: python scripts/bench/endpoints.py http://127.0.0.1:8200 [runs] [--out f.json]"""
import json
import statistics
import sys
import time

import httpx

base = sys.argv[1].rstrip("/") + "/api/v1"
runs = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 7
out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
c = httpx.Client(timeout=300)
tok = c.post(base + "/auth/login", json={"email": "bench@example.com", "password": "bench-password-1"}).json()["access_token"]
c.headers["Authorization"] = f"Bearer {tok}"
sup = c.get(base + "/suppliers").json()[0]["id"]
inv = c.get(base + "/purchases", params={"limit": 1}).json()["items"][0]["id"]
rec = next(r["id"] for r in c.get(base + "/recipes").json() if r["published_version_no"])
ing = c.get(base + "/ingredients", params={"limit": 1}).json()["items"][0]["id"]
targets = {
    "dashboard": "/dashboard", "stock levels (3,000 items)": "/stock", "ingredients (all 3,000)": "/ingredients?limit=5000",
    "ingredient search": "/ingredients?q=item%2001&limit=50", "purchases page 1": "/purchases?limit=50",
    "purchases page 100": "/purchases?limit=50&offset=5000", "purchases unpaid filter": "/purchases?limit=50&payment_status=unpaid",
    "purchase detail": f"/purchases/{inv}", "supplier file (~170 invoices)": f"/suppliers/{sup}",
    "recipes list (300, costed)": "/recipes", "recipe detail": f"/recipes/{rec}", "recipe cost history": f"/recipes/{rec}/cost-history",
    "ingredient batches": f"/stock/ingredients/{ing}/batches", "movements page": "/stock/movements?limit=100",
    "valuation report": "/reports/inventory-valuation", "consumption report (year)": "/reports/consumption?date_from=2025-01-01&date_to=2026-12-31",
    "integrity check (all records)": "/reports/integrity",
}
res = {}
for name, path in targets.items():
    t = []
    for _ in range(runs):
        s = time.perf_counter(); r = c.get(base + path); t.append((time.perf_counter() - s) * 1000)
        if r.status_code != 200:
            break
    if r.status_code != 200:
        res[name] = {"median_ms": None, "status": r.status_code}
        print(f"{name:<34}{'n/a':>10}      (HTTP {r.status_code} in this version)")
        continue
    res[name] = {"median_ms": round(statistics.median(t), 1), "max_ms": round(max(t), 1), "bytes": len(r.content)}
    print(f"{name:<34}{res[name]['median_ms']:>10} ms   {len(r.content) / 1024:8.0f} KB")
if out:
    json.dump(res, open(out, "w"), indent=1)
