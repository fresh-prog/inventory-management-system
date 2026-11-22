"""HTTP load test: N concurrent employees (each with its own device) for D seconds.

Usage: python scripts/bench/load.py http://127.0.0.1:8200 --users 25 --seconds 60 [--read-only] [--out f.json]
Reports per-action p50/p95/p99/max latency, throughput and errors, plus CPU/RSS of the API
workers and PostgreSQL sampled every second.
"""

import argparse
import json
import random
import statistics
import threading
import time
import uuid
from collections import defaultdict

import httpx
import psutil

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--users", type=int, default=25)
ap.add_argument("--seconds", type=int, default=60)
ap.add_argument("--read-only", action="store_true")
ap.add_argument("--out")
ap.add_argument("--email", default="bench@example.com")
ap.add_argument("--password", default="bench-password-1")
a = ap.parse_args()
API = a.base.rstrip("/") + "/api/v1"

boot = httpx.Client(timeout=60)
tok = boot.post(API + "/auth/login", json={"email": a.email, "password": a.password}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
_r = boot.get(API + "/ingredients?limit=5000", headers=H)
ings = (_r if _r.status_code == 200 else boot.get(API + "/ingredients?limit=500", headers=H)).json()["items"]
sups = [s["id"] for s in boot.get(API + "/suppliers", headers=H).json()]
recs = [r["id"] for r in boot.get(API + "/recipes", headers=H).json() if r["published_version_no"]]
invs = boot.get(API + "/purchases?limit=200&payment_status=unpaid", headers=H).json()["items"]
print(f"fixtures: {len(ings)} ingredients, {len(sups)} suppliers, {len(recs)} recipes, {len(invs)} open invoices")

lat: dict[str, list[float]] = defaultdict(list)
errors: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
lock = threading.Lock()
stop = time.monotonic() + a.seconds

READ = [("dashboard", 6), ("stock_levels", 8), ("ingredient_search", 15), ("purchases_page", 8),
        ("purchase_detail", 6), ("supplier_file", 4), ("recipe_detail", 6), ("recipes_list", 2),
        ("movements_page", 5), ("valuation_report", 1)]
WRITE = [("consume", 18), ("purchase", 5), ("payment", 3), ("production", 3)]


def act(c: httpx.Client, hdr: dict, name: str, rnd: random.Random):
    ing = rnd.choice(ings)
    idem = {**hdr, "Idempotency-Key": str(uuid.uuid4())}
    if name == "dashboard":
        return c.get(API + "/dashboard", headers=hdr)
    if name == "stock_levels":
        return c.get(API + "/stock", headers=hdr)
    if name == "ingredient_search":
        return c.get(API + "/ingredients", params={"q": ing["name"][:6], "limit": 50}, headers=hdr)
    if name == "purchases_page":
        return c.get(API + "/purchases", params={"limit": 50, "offset": rnd.randint(0, 2000)}, headers=hdr)
    if name == "purchase_detail":
        return c.get(API + f"/purchases/{rnd.choice(invs)['id']}", headers=hdr)
    if name == "supplier_file":
        return c.get(API + f"/suppliers/{rnd.choice(sups)}", headers=hdr)
    if name == "recipe_detail":
        return c.get(API + f"/recipes/{rnd.choice(recs)}", headers=hdr)
    if name == "recipes_list":
        return c.get(API + "/recipes", headers=hdr)
    if name == "movements_page":
        return c.get(API + "/stock/movements", params={"limit": 100}, headers=hdr)
    if name == "valuation_report":
        return c.get(API + "/reports/inventory-valuation", headers=hdr)
    if name == "consume":
        return c.post(API + "/stock/consumptions", headers=idem, json={"lines": [
            {"ingredient_id": ing["id"], "quantity": str(rnd.randint(1, 5)), "unit": ing["consumption_unit"]}]})
    if name == "purchase":
        lines = [{"ingredient_id": x["id"], "quantity": str(rnd.randint(1, 20)), "unit": x["purchase_unit"],
                  "unit_price": str(rnd.randint(1, 30))} for x in rnd.sample(ings, 3)]
        return c.post(API + "/purchases", headers=idem, json={"supplier_id": rnd.choice(sups),
                      "invoice_ref": "L-" + uuid.uuid4().hex[:10], "invoice_date": "2026-01-15", "lines": lines})
    if name == "payment":
        inv = rnd.choice(invs)
        return c.post(API + "/payments", headers=idem, json={"supplier_id": inv["supplier_id"], "paid_on": "2026-01-15",
                      "amount": "0.01", "method": "cash", "allocations": [{"invoice_id": inv["id"], "amount": "0.01"}]})
    if name == "production":
        return c.post(API + "/stock/production-runs", headers=idem, json={"recipe_id": rnd.choice(recs), "batches": "1",
                      "allow_shortfall": True, "override_reason": "load test"})


def user(n: int):
    rnd = random.Random(n)
    c = httpx.Client(timeout=60)
    dev = c.post(API + "/devices", headers=H, json={"name": f"load device {n}"}).json()["id"]
    hdr = {**H, "X-Device-Id": dev}
    mix = READ + ([] if a.read_only else WRITE)
    names, weights = zip(*mix)
    while time.monotonic() < stop:
        name = rnd.choices(names, weights)[0]
        t = time.perf_counter()
        try:
            r = act(c, hdr, name, rnd)
            code = r.status_code
        except httpx.HTTPError:
            code = 599
        dt = (time.perf_counter() - t) * 1000
        with lock:
            lat[name].append(dt)
            if code >= 400 and not (code == 422 and name in ("consume", "payment")):  # business refusals are fine
                errors[name][code] += 1
        time.sleep(rnd.uniform(0.2, 1.0))  # think time: a person tapping a tablet


def find_procs():
    api = [p for p in psutil.process_iter(["cmdline"]) if p.info["cmdline"] and "uvicorn" in " ".join(p.info["cmdline"])
           or (p.info["cmdline"] and "multiprocessing" in " ".join(p.info["cmdline"]) and "spawn" in " ".join(p.info["cmdline"]))]
    pg = [p for p in psutil.process_iter(["name"]) if p.info["name"] and p.info["name"].startswith("postgres")]
    return api, pg


samples = []


def sampler():
    api, pg = find_procs()
    for p in api + pg:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    while time.monotonic() < stop:
        time.sleep(1)
        row = {"api_cpu": 0.0, "api_rss_mb": 0.0, "pg_cpu": 0.0, "pg_rss_mb": 0.0}
        for group, key in ((api, "api"), (pg, "pg")):
            for p in group:
                try:
                    row[f"{key}_cpu"] += p.cpu_percent(None)
                    row[f"{key}_rss_mb"] += p.memory_info().rss / 1e6
                except psutil.Error:
                    pass
        samples.append(row)


threads = [threading.Thread(target=user, args=(i,)) for i in range(a.users)]
st = threading.Thread(target=sampler)
t0 = time.monotonic()
st.start()
[t.start() for t in threads]
[t.join() for t in threads]
st.join()
elapsed = time.monotonic() - t0


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1)


report = {"users": a.users, "seconds": round(elapsed, 1), "requests": sum(len(v) for v in lat.values()),
          "throughput_rps": round(sum(len(v) for v in lat.values()) / elapsed, 1),
          "errors": {k: dict(v) for k, v in errors.items()},
          "actions": {k: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95), "p99": pct(v, .99), "max": round(max(v), 1),
                          "mean": round(statistics.mean(v), 1)} for k, v in sorted(lat.items())},
          "resources": {"api_cpu_avg": round(statistics.mean(s["api_cpu"] for s in samples), 1) if samples else None,
                        "api_cpu_max": round(max(s["api_cpu"] for s in samples), 1) if samples else None,
                        "api_rss_mb_max": round(max(s["api_rss_mb"] for s in samples)) if samples else None,
                        "pg_cpu_avg": round(statistics.mean(s["pg_cpu"] for s in samples), 1) if samples else None,
                        "pg_rss_mb_max": round(max(s["pg_rss_mb"] for s in samples)) if samples else None}}
print(f"{'action':<20}{'n':>6}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}  (ms)")
for k, v in report["actions"].items():
    print(f"{k:<20}{v['n']:>6}{v['p50']:>9}{v['p95']:>9}{v['p99']:>9}{v['max']:>9}")
print(json.dumps({k: report[k] for k in ("users", "requests", "throughput_rps", "errors", "resources")}))
if a.out:
    json.dump(report, open(a.out, "w"), indent=1)
