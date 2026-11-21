"""Load demo data THROUGH THE API (so every business rule applies), for demos and screenshots.

Usage: python scripts/seed_demo.py http://localhost:8000
Creates a Kampala branch (UGX), suppliers, ingredients, purchases over three months,
payments, recipes, production, waste and a stock count.
"""

import sys
import uuid
from datetime import date, timedelta

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api/v1"
OWNER = {"email": "owner@demo.restaurant", "password": "demo-owner-password"}
c = httpx.Client(timeout=30)
H: dict = {}


def call(method, path, body=None, idem=True):
    h = dict(H)
    if idem and method == "POST":
        h["Idempotency-Key"] = str(uuid.uuid4())
    r = c.request(method, BASE + path, json=body, headers=h)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.content else None


def main():
    if c.get(BASE + "/setup/status").json()["needs_setup"]:
        call("POST", "/setup", {"branch_code": "KLA", "branch_name": "Mama's Kitchen — Kampala", "country": "UG",
                                "currency": "UGX", "currency_decimals": 0, "timezone": "Africa/Kampala",
                                "owner_name": "Grace Namubiru", "owner_email": OWNER["email"],
                                "owner_password": OWNER["password"]}, idem=False)
    tok = c.post(BASE + "/auth/login", json=OWNER).json()["access_token"]
    H["Authorization"] = f"Bearer {tok}"
    if call("GET", "/ingredients")["total"]:
        print("Demo data already present.")
        return
    for u in [("chef@demo.restaurant", "Joseph Okello", "kitchen"), ("buyer@demo.restaurant", "Sarah Achieng", "purchaser"),
              ("manager@demo.restaurant", "David Mugisha", "manager")]:
        call("POST", "/users", {"email": u[0], "display_name": u[1], "password": "demo-staff-password", "role": u[2]}, idem=False)

    sup = {s[0]: call("POST", "/suppliers", {"code": s[0], "name": s[1], "contact_person": s[2], "phone": s[3],
                                             "payment_terms_days": s[4], "tax_id": s[5]}, idem=False)["id"]
           for s in [("NAKA", "Nakasero Fresh Produce", "Aisha K.", "+256 700 111 222", 7, "1000123456"),
                     ("KMEAT", "Kampala Quality Meats", "Peter S.", "+256 772 333 444", 14, "1000234567"),
                     ("DAIRY", "Jesa Dairy Distributors", "Ruth N.", "+256 701 555 666", 30, "1000345678"),
                     ("WHOLE", "Owino Wholesale Dry Goods", "Musa B.", "+256 752 777 888", 14, None)]}
    I = {}
    for code, name, cat, dim, pu, cu, minq, minu, sups, conv in [
        ("BEEF", "Beef (stewing)", "Meat", "mass", "kg", "g", "10", "kg", ["KMEAT"], []),
        ("CHICK", "Chicken (whole)", "Meat", "mass", "kg", "g", "8", "kg", ["KMEAT"], []),
        ("ONION", "Red onions", "Vegetables", "mass", "kg", "g", "5", "kg", ["NAKA"], []),
        ("TOMATO", "Tomatoes", "Vegetables", "mass", "kg", "g", "5", "kg", ["NAKA"], []),
        ("MATOOKE", "Matooke", "Vegetables", "mass", "kg", "g", "20", "kg", ["NAKA"], [("unit", "1", "g", "18000")]),
        ("RICE", "Rice (Kaiso)", "Dry goods", "mass", "kg", "g", "25", "kg", ["WHOLE"], []),
        ("BEANS", "Beans (Nambale)", "Dry goods", "mass", "kg", "g", "10", "kg", ["WHOLE"], []),
        ("OIL", "Cooking oil", "Dry goods", "mass", "l", "g", "5", "kg", ["WHOLE"], [("l", "1", "g", "920")]),
        ("MILK", "Fresh milk", "Dairy", "volume", "l", "ml", "10", "l", ["DAIRY"], []),
        ("EGGS", "Eggs", "Dairy", "count", "unit", "unit", "60", "unit", ["DAIRY"], []),
        ("GNUT", "Groundnut paste", "Dry goods", "mass", "kg", "g", "2", "kg", ["WHOLE"], []),
        ("SALT", "Salt", "Dry goods", "mass", "kg", "g", "1", "kg", ["WHOLE"], []),
    ]:
        I[code] = call("POST", "/ingredients", {
            "code": code, "name": name, "category": cat, "base_dimension": dim, "purchase_unit": pu, "consumption_unit": cu,
            "min_stock": minq, "min_stock_unit": minu, "suppliers": [{"supplier_id": sup[s], "is_preferred": True} for s in sups],
            "conversions": [{"from_unit": a, "from_qty": b, "to_unit": cc, "to_qty": d} for a, b, cc, d in conv]}, idem=False)["id"]

    today = date.today()
    d = lambda days: (today - timedelta(days=days)).isoformat()
    inv = {}

    def buy(ref, s, day, lines, **kw):
        body = {"supplier_id": sup[s], "invoice_ref": ref, "invoice_date": d(day),
                "lines": [{"ingredient_id": I[i], "quantity": str(q), "unit": u, "unit_price": str(p)} for i, q, u, p in lines], **kw}
        inv[ref] = call("POST", "/purchases", body)
        return inv[ref]

    buy("KQM-1041", "KMEAT", 70, [("BEEF", 20, "kg", 16000), ("CHICK", 15, "kg", 14000)])
    buy("NFP-2201", "NAKA", 68, [("ONION", 25, "kg", 3000), ("TOMATO", 20, "kg", 3500), ("MATOOKE", 6, "unit", 25000)])
    buy("OWD-560", "WHOLE", 65, [("RICE", 50, "kg", 4200), ("BEANS", 25, "kg", 4800), ("OIL", 20, "l", 7500), ("SALT", 5, "kg", 1500),
                                 ("GNUT", 5, "kg", 12000)], discount_total="10000")
    buy("JDD-0098", "DAIRY", 60, [("MILK", 40, "l", 2200), ("EGGS", 150, "unit", 450)])
    buy("KQM-1077", "KMEAT", 35, [("BEEF", 25, "kg", 17500), ("CHICK", 10, "kg", 14500)])
    buy("NFP-2265", "NAKA", 30, [("ONION", 20, "kg", 3200), ("TOMATO", 25, "kg", 4000), ("MATOOKE", 8, "unit", 27000)])
    buy("JDD-0131", "DAIRY", 20, [("MILK", 40, "l", 2300), ("EGGS", 120, "unit", 480)])
    buy("KQM-1102", "KMEAT", 6, [("BEEF", 20, "kg", 18500)])
    buy("NFP-2310", "NAKA", 3, [("TOMATO", 15, "kg", 4500), ("ONION", 10, "kg", 3200)])

    pay = lambda s, day, amt, method, ref, allocs: call("POST", "/payments", {"supplier_id": sup[s], "paid_on": d(day), "amount": str(amt),
        "method": method, "reference": ref, "allocations": [{"invoice_id": inv[i]["id"], "amount": str(a)} for i, a in allocs]})
    pay("KMEAT", 55, 530000, "bank_transfer", "STB-88123", [("KQM-1041", 530000)])
    pay("NAKA", 60, 220000, "mobile_money", "MTN-4471902", [("NFP-2201", 220000)])
    pay("WHOLE", 50, 300000, "cash", None, [("OWD-560", 300000)])
    pay("DAIRY", 45, 155500, "mobile_money", "AIR-993321", [("JDD-0098", 155500)])
    pay("KMEAT", 10, 300000, "bank_transfer", "STB-90210", [("KQM-1077", 300000)])

    R = {}
    for code, name, cat, portions, price, target, shrink, lines, prep, cook in [
        ("BEEF-STEW", "Beef stew with rice", "Mains", 20, 15000, "0.32", "0.25",
         [("BEEF", "3", "kg", "gross", "0"), ("ONION", "800", "g", "net", "0.12"), ("TOMATO", "1", "kg", "gross", "0.05"),
          ("OIL", "200", "g", "gross", "0"), ("RICE", "2", "kg", "gross", "0"), ("SALT", "30", "g", "gross", "0")],
         "Dice beef into 3 cm cubes. Peel and slice onions. Chop tomatoes.", "Brown beef in oil, add onions, then tomatoes. Simmer 90 minutes. Serve with steamed rice."),
        ("MATOOKE-GNUT", "Matooke in groundnut sauce", "Mains", 12, 9000, "0.30", "0",
         [("MATOOKE", "1", "unit", "gross", "0.30"), ("GNUT", "500", "g", "gross", "0"), ("ONION", "300", "g", "gross", "0.10"),
          ("TOMATO", "400", "g", "gross", "0"), ("SALT", "15", "g", "gross", "0")],
         "Peel matooke. Mix groundnut paste with warm water.", "Steam matooke in banana leaves 45 minutes. Simmer sauce 30 minutes."),
        ("ROLEX", "Rolex (egg chapati roll)", "Breakfast", 1, 4000, "0.30", "0",
         [("EGGS", "2", "unit", "gross", "0"), ("ONION", "30", "g", "gross", "0"), ("TOMATO", "50", "g", "gross", "0"), ("OIL", "20", "g", "gross", "0")],
         "Slice tomato and onion thinly.", "Fry eggs with vegetables, roll in chapati."),
        ("CHICKEN-LUWOMBO", "Chicken luwombo", "Mains", 8, 18000, "0.30", "0.20",
         [("CHICK", "2.5", "kg", "gross", "0.15"), ("ONION", "400", "g", "gross", "0.10"), ("TOMATO", "500", "g", "gross", "0"), ("GNUT", "300", "g", "gross", "0"), ("SALT", "15", "g", "gross", "0")],
         "Joint chicken. Prepare banana leaf parcels.", "Steam parcels 2 hours."),
    ]:
        r = call("POST", "/recipes", {"code": code, "name": name, "category": cat, "version": {
            "portions_per_batch": str(portions), "selling_price": str(price), "target_food_cost_rate": target, "cooking_shrinkage_rate": shrink,
            "prep_instructions": prep, "cooking_instructions": cook,
            "lines": [{"ingredient_id": I[i], "quantity": q, "unit": u, "basis": b, "prep_loss_rate": l} for i, q, u, b, l in lines]}}, idem=False)
        call("POST", f"/recipes/{r['id']}/versions/1/publish", idem=False)
        R[code] = r["id"]

    for rid, portions in [("BEEF-STEW", 40), ("MATOOKE-GNUT", 24), ("CHICKEN-LUWOMBO", 16), ("BEEF-STEW", 60), ("ROLEX", 50),
                          ("MATOOKE-GNUT", 36), ("BEEF-STEW", 40), ("CHICKEN-LUWOMBO", 8), ("ROLEX", 40)]:
        call("POST", "/stock/production-runs", {"recipe_id": R[rid], "portions": str(portions)})
    call("POST", "/stock/consumptions", {"lines": [{"ingredient_id": I["MILK"], "quantity": "18", "unit": "l"}], "note": "Tea service"})
    call("POST", "/stock/consumptions", {"lines": [{"ingredient_id": I["BEANS"], "quantity": "6", "unit": "kg"},
                                                   {"ingredient_id": I["RICE"], "quantity": "8", "unit": "kg"}], "note": "Staff lunches"})
    call("POST", "/stock/adjustments", {"ingredient_id": I["TOMATO"], "quantity": "3", "unit": "kg", "reason": "spoilage", "note": "Overripe batch"})
    call("POST", "/stock/adjustments", {"ingredient_id": I["MILK"], "quantity": "2", "unit": "l", "reason": "expired"})
    call("POST", "/stock/adjustments", {"ingredient_id": I["EGGS"], "quantity": "6", "unit": "unit", "reason": "damaged", "note": "Dropped tray"})
    # a small second-version recipe to show version history + cost change
    call("PUT", f"/recipes/{R['ROLEX']}/draft", {"portions_per_batch": "1", "selling_price": "4500", "target_food_cost_rate": "0.30",
        "change_note": "Price review", "lines": [{"ingredient_id": I["EGGS"], "quantity": "2", "unit": "unit"},
        {"ingredient_id": I["ONION"], "quantity": "30", "unit": "g"}, {"ingredient_id": I["TOMATO"], "quantity": "50", "unit": "g"},
        {"ingredient_id": I["OIL"], "quantity": "20", "unit": "g"}]}, idem=False)
    call("POST", f"/recipes/{R['ROLEX']}/versions/2/publish", idem=False)
    cnt = call("POST", "/stock/counts", {"note": "Weekly dry-store count",
                                         "ingredient_ids": [I["RICE"], I["BEANS"], I["SALT"], I["GNUT"]]}, idem=False)
    lv = {x["ingredient_id"]: x for x in call("GET", "/stock")["items"]}
    call("PUT", f"/stock/counts/{cnt['id']}/lines", {"lines": [
        {"ingredient_id": I["RICE"], "quantity": str(round(float(lv[I["RICE"]]["on_hand_base"]) - 800)), "unit": "g"},
        {"ingredient_id": I["BEANS"], "quantity": str(round(float(lv[I["BEANS"]]["on_hand_base"]) + 300)), "unit": "g"},
        {"ingredient_id": I["SALT"], "quantity": str(round(float(lv[I["SALT"]]["on_hand_base"]))), "unit": "g"}]}, idem=False)
    call("POST", f"/stock/counts/{cnt['id']}/approve")
    chk = call("GET", "/reports/integrity")
    print("Demo data loaded. Integrity:", "OK" if chk["ok"] else chk)
    print(f"Sign in as {OWNER['email']} / {OWNER['password']}")


if __name__ == "__main__":
    main()
