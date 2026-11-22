"""Failure simulation against REAL processes (PostgreSQL crash, kill -9, outages, full disk).

Usage (run on a test machine, never in production):
    python scripts/chaos/run_chaos.py --pgctl "<pg_ctl -D DATA -o '...'>" [--out docs/chaos-results.json]

Uses its own database `restaurant_chaos`. Each scenario prints PASS/FAIL with evidence.
"""

import argparse
import json
import os
import random
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PY = str(BACKEND / ".venv/bin/python")
DB = "restaurant_chaos"
URL = f"postgresql+psycopg://app:app@localhost:5432/{DB}"
ap = argparse.ArgumentParser()
ap.add_argument("--pgctl", required=True, help="pg_ctl command prefix that controls the PostgreSQL server")
ap.add_argument("--out")
args = ap.parse_args()
results = []


def sh(cmd, check=True, **kw):
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True, **kw)


def pg(action):
    sh(f"{args.pgctl} {action}", check=False)
    for _ in range(100):
        if sh("pg_isready -h localhost -q", check=False).returncode == (0 if action != "stop -m immediate" and "stop" not in action else 2):
            return
        time.sleep(0.2)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Server:
    def __init__(self, files_dir=None):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}/api/v1"
        self.files_dir = files_dir or tempfile.mkdtemp(prefix="chaos-files-")
        self.proc = None

    def start(self):
        env = {**os.environ, "APP_DATABASE_URL": URL, "APP_FILES_DIR": self.files_dir, "APP_ENV": "production",
               "APP_JWT_SECRET": "chaos-" + "x" * 40, "APP_RATE_LIMIT_API_PER_MINUTE": "1000000",
               "APP_RATE_LIMIT_LOGIN_PER_MINUTE": "100000", "APP_MIN_FREE_MB": "0"}  # exercise real ENOSPC
        self.proc = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", str(self.port), "--workers", "2"],
                                     cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        for _ in range(150):
            try:
                if httpx.get(self.base + "/health/live", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("server did not start")

    def kill9(self):
        os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc.wait()


def fresh_db():
    env = {**os.environ, "PGPASSWORD": "postgres"}
    sh(f'psql -h localhost -U postgres -qc "DROP DATABASE IF EXISTS {DB} WITH (FORCE)" -c "CREATE DATABASE {DB} OWNER app"', env=env)
    sh(f"cd {BACKEND} && APP_DATABASE_URL={URL} .venv/bin/alembic upgrade head")


def bootstrap(srv):
    c = httpx.Client(base_url=srv.base, timeout=30)
    c.post("/setup", json={"branch_code": "CHA", "branch_name": "Chaos", "country": "UG", "currency": "USD",
                           "owner_name": "Owner", "owner_email": "owner@chaos.io", "owner_password": "chaos-password-1"})
    tok = c.post("/auth/login", json={"email": "owner@chaos.io", "password": "chaos-password-1"}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    c.headers["X-Device-Id"] = c.post("/devices", json={"name": "chaos"}).json()["id"]
    k = lambda: {"Idempotency-Key": str(uuid.uuid4())}
    sup = c.post("/suppliers", json={"code": "S1", "name": "Supplier"}).json()["id"]
    ings = [c.post("/ingredients", json={"code": f"I{i}", "name": f"Ing {i}", "base_dimension": "mass",
                                         "purchase_unit": "kg", "consumption_unit": "g"}).json()["id"] for i in range(5)]
    for i in ings:
        c.post("/purchases", headers=k(), json={"supplier_id": sup, "invoice_ref": f"SEED-{i[:8]}", "invoice_date": "2026-01-01",
               "lines": [{"ingredient_id": i, "quantity": "1000", "unit": "kg", "unit_price": "5"}]})
    return c, sup, ings


def workload(srv, c, sup, ings, seconds, log):
    """Writers recording (idempotency key, body, response) for every attempt."""
    stop = time.monotonic() + seconds

    def writer(n):
        rnd = random.Random(n)
        hc = httpx.Client(base_url=srv.base, timeout=5, headers=dict(c.headers))
        while time.monotonic() < stop:
            key = str(uuid.uuid4())
            if rnd.random() < 0.5:
                path, body = "/purchases", {"supplier_id": sup, "invoice_ref": "CH-" + key[:12], "invoice_date": "2026-01-02",
                    "lines": [{"ingredient_id": rnd.choice(ings), "quantity": str(rnd.randint(1, 9)), "unit": "kg", "unit_price": "7"}]}
            else:
                path, body = "/stock/consumptions", {"lines": [{"ingredient_id": rnd.choice(ings), "quantity": str(rnd.randint(1, 500)), "unit": "g"}]}
            try:
                r = hc.post(path, json=body, headers={"Idempotency-Key": key})
                log.append({"key": key, "path": path, "body": body, "status": r.status_code,
                            "id": (r.json().get("id") or r.json().get("group_id")) if r.status_code < 300 else None})
            except httpx.HTTPError as e:
                log.append({"key": key, "path": path, "body": body, "status": None, "error": type(e).__name__})
                time.sleep(0.2)
    ts = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    return ts


def verify(c, log, label):
    ev = {}
    integ = c.get("/reports/integrity").json()
    ev["app_integrity_ok"] = integ["ok"]
    sql = sh(f"PGPASSWORD=app psql -h localhost -U app -tA -f {ROOT}/scripts/sql/integrity.sql {DB}").stdout.strip()
    ev["sql_violations"] = len(sql.splitlines()) if sql else 0
    acked = [x for x in log if x["status"] and x["status"] < 300]
    ev["requests"] = len(log)
    ev["acknowledged_before_crash"] = len(acked)
    ev["unanswered_or_failed"] = len(log) - len(acked)
    # every acknowledged purchase exists; every retry is idempotent (replay) or completes exactly once
    lost = 0
    for x in acked:
        if x["path"] == "/purchases" and c.get(f"/purchases/{x['id']}").status_code != 200:
            lost += 1
    ev["acknowledged_but_missing"] = lost
    replays = executed = errors = 0
    for x in log:
        r = c.post(x["path"], json=x["body"], headers={"Idempotency-Key": x["key"]})
        if r.status_code < 300 and r.headers.get("idempotent-replay") == "true":
            replays += 1
            if x["id"] and (r.json().get("id") or r.json().get("group_id")) != x["id"]:
                errors += 1
        elif r.status_code < 300:
            executed += 1
            if x["status"] and x["status"] < 300:
                errors += 1  # acknowledged before, executed again = duplicate
        elif r.status_code != 422:  # 422 = business refusal (e.g. not enough stock), fine
            errors += 1
    ev["retry_replayed"], ev["retry_executed_once"], ev["retry_errors"] = replays, executed, errors
    refs = [x["body"]["invoice_ref"] for x in log if x["path"] == "/purchases"]
    dup = sh(f"PGPASSWORD=app psql -h localhost -U app -tAc \"SELECT count(*) FROM (SELECT invoice_ref FROM purchase_invoice "
             f"GROUP BY supplier_id, invoice_ref HAVING count(*) > 1) d\" {DB}").stdout.strip()
    ev["duplicate_invoices"] = int(dup)
    no_outbox = sh(f"PGPASSWORD=app psql -h localhost -U app -tAc \"SELECT count(*) FROM purchase_invoice i WHERE NOT EXISTS "
                   f"(SELECT 1 FROM outbox_event o WHERE o.aggregate_id = i.id)\" {DB}").stdout.strip()
    ev["purchases_without_sync_record"] = int(no_outbox)
    ev["app_integrity_after_retries"] = c.get("/reports/integrity").json()["ok"]
    ok = (ev["app_integrity_ok"] and ev["sql_violations"] == 0 and lost == 0 and errors == 0 and ev["duplicate_invoices"] == 0
          and ev["purchases_without_sync_record"] == 0 and ev["app_integrity_after_retries"])
    results.append({"scenario": label, "pass": ok, "evidence": ev})
    print(("PASS " if ok else "FAIL ") + label, json.dumps(ev))


# ───────────────────────── scenarios ─────────────────────────
def power_cut():
    fresh_db(); srv = Server().start(); c, sup, ings = bootstrap(srv); log = []
    ts = workload(srv, c, sup, ings, 8, log)
    time.sleep(4)
    sh(f"{args.pgctl} stop -m immediate", check=False)  # power loss: no clean shutdown, no checkpoint
    srv.kill9()
    [t.join() for t in ts]
    t0 = time.monotonic()
    sh(f"{args.pgctl} start", check=False)
    while sh("pg_isready -h localhost -q", check=False).returncode != 0:
        time.sleep(0.2)
    srv.start()
    rto = time.monotonic() - t0
    c.base_url = srv.base
    verify(c, log, f"power interruption (PostgreSQL crash + API kill -9 during writes; back in {rto:.1f}s)")
    srv.kill9()


def api_crash():
    fresh_db(); srv = Server().start(); c, sup, ings = bootstrap(srv); log = []
    ts = workload(srv, c, sup, ings, 6, log)
    time.sleep(3)
    srv.kill9()
    [t.join() for t in ts]
    srv.start(); c.base_url = srv.base
    verify(c, log, "local server restart (API kill -9 under load)")
    srv.kill9()


def db_outage():
    fresh_db(); srv = Server().start(); c, sup, ings = bootstrap(srv)
    sh(f"{args.pgctl} stop -m fast", check=False)
    while sh("pg_isready -h localhost -q", check=False).returncode == 0:
        time.sleep(0.2)
    ev = {}
    r = c.get("/dashboard"); ev["read_status"] = r.status_code; ev["read_code"] = r.json().get("code") if r.headers.get("content-type", "").startswith("application") else None
    w = c.post("/stock/consumptions", headers={"Idempotency-Key": "outage-1"},
               json={"lines": [{"ingredient_id": ings[0], "quantity": "1", "unit": "g"}]})
    ev["write_status"], ev["write_detail"] = w.status_code, w.json().get("detail") if w.status_code >= 400 else None
    ev["health_status"] = httpx.get(srv.base + "/health/ready").status_code
    sh(f"{args.pgctl} start", check=False)
    while sh("pg_isready -h localhost -q", check=False).returncode != 0:
        time.sleep(0.2)
    time.sleep(0.5)
    ev["read_after_recovery"] = c.get("/dashboard").status_code
    w2 = c.post("/stock/consumptions", headers={"Idempotency-Key": "outage-1"},
                json={"lines": [{"ingredient_id": ings[0], "quantity": "1", "unit": "g"}]})
    ev["same_write_after_recovery"] = w2.status_code
    ok = (ev["read_status"] == 503 and ev["read_code"] == "DATABASE_UNAVAILABLE" and ev["write_status"] == 503
          and ev["health_status"] == 503 and ev["read_after_recovery"] == 200 and ev["same_write_after_recovery"] == 201)
    results.append({"scenario": "database unavailable (stopped while the app runs)", "pass": ok, "evidence": ev})
    print(("PASS " if ok else "FAIL ") + "database unavailable", json.dumps(ev))
    srv.kill9()


def disk_full():
    mnt = tempfile.mkdtemp(prefix="chaos-tiny-")
    m = sh(f"mount -t tmpfs -o size=256k tmpfs {mnt}", check=False)
    if m.returncode != 0:
        results.append({"scenario": "disk full", "pass": None, "evidence": {"skipped": "cannot mount tmpfs: " + m.stderr.strip()}})
        print("SKIP disk full"); return
    try:
        fresh_db(); srv = Server(files_dir=mnt).start(); c, sup, ings = bootstrap(srv)
        inv = c.get("/purchases").json()["items"][0]["id"]
        ev = {}
        h = {k: v for k, v in c.headers.items() if k.lower() in ("authorization", "x-device-id")}
        small = httpx.post(f"{srv.base}/purchases/{inv}/attachments", headers=h,
                           files={"file": ("a.jpg", b"\xff\xd8\xff" + os.urandom(50_000), "image/jpeg")})
        ev["upload_with_space"] = small.status_code
        big = httpx.post(f"{srv.base}/purchases/{inv}/attachments", headers=h,
                         files={"file": ("b.jpg", b"\xff\xd8\xff" + os.urandom(400_000), "image/jpeg")})
        ev["upload_when_full"], ev["code"] = big.status_code, big.json().get("code")
        ev["detail"] = big.json().get("detail")
        ev["attachments_recorded"] = len(c.get(f"/purchases/{inv}/attachments").json())
        ev["partial_files_left"] = sum(1 for p in Path(mnt).rglob("*.part"))
        ev["app_still_works"] = c.get("/dashboard").status_code
        ok = (ev["upload_with_space"] == 201 and ev["upload_when_full"] in (507, 422) and ev["attachments_recorded"] == 1
              and ev["partial_files_left"] == 0 and ev["app_still_works"] == 200)
        results.append({"scenario": "disk full while saving a receipt", "pass": ok, "evidence": ev})
        print(("PASS " if ok else "FAIL ") + "disk full", json.dumps(ev))
        srv.kill9()
    finally:
        sh(f"umount {mnt}", check=False)


def broken_upload():
    fresh_db(); srv = Server().start(); c, sup, ings = bootstrap(srv)
    inv = c.get("/purchases").json()["items"][0]["id"]
    tok, dev = c.headers["Authorization"], c.headers["X-Device-Id"]
    body = (b"--XX\r\nContent-Disposition: form-data; name=\"file\"; filename=\"r.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"
            + b"\xff\xd8\xff" + os.urandom(100_000))
    s = socket.create_connection(("127.0.0.1", srv.port))
    s.sendall(f"POST /api/v1/purchases/{inv}/attachments HTTP/1.1\r\nHost: x\r\nAuthorization: {tok}\r\nX-Device-Id: {dev}\r\n"
              f"Content-Type: multipart/form-data; boundary=XX\r\nContent-Length: {len(body) + 5000}\r\n\r\n".encode() + body)
    s.close()  # phone loses Wi-Fi mid-upload
    time.sleep(1)
    ev = {"attachments_recorded": len(c.get(f"/purchases/{inv}/attachments").json()),
          "files_on_disk": sum(1 for p in Path(srv.files_dir).rglob("*") if p.is_file()),
          "app_still_works": c.get("/dashboard").status_code}
    retry = httpx.post(f"{srv.base}/purchases/{inv}/attachments", headers={"Authorization": tok, "X-Device-Id": dev},
                       files={"file": ("r.jpg", b"\xff\xd8\xff" + os.urandom(1000), "image/jpeg")})
    ev["retry_upload"] = retry.status_code
    ok = ev["attachments_recorded"] == 0 and ev["files_on_disk"] == 0 and ev["app_still_works"] == 200 and ev["retry_upload"] == 201
    results.append({"scenario": "failed invoice upload (connection lost mid-upload)", "pass": ok, "evidence": ev})
    print(("PASS " if ok else "FAIL ") + "failed upload", json.dumps(ev))
    srv.kill9()


for scenario in (power_cut, api_crash, db_outage, disk_full, broken_upload):
    try:
        scenario()
    except Exception as e:  # noqa: BLE001
        results.append({"scenario": scenario.__name__, "pass": False, "evidence": {"exception": repr(e)}})
        print("FAIL", scenario.__name__, repr(e))
        sh(f"{args.pgctl} start", check=False)
if args.out:
    json.dump({"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": results}, open(args.out, "w"), indent=1)
sys.exit(0 if all(r["pass"] is not False for r in results) else 1)
