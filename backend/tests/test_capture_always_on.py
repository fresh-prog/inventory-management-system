"""Regression (found by the Stage 4 benchmark): change capture depended on an incidental import.
A process that writes business data through the services WITHOUT importing any web route must
still produce outbox records."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[1]

SCRIPT = r"""
import os, uuid
from decimal import Decimal as D
from datetime import date
from app.db import make_engine, make_sessionmaker
from app.models import Branch, Device, User, UserBranchRole, Supplier
from app.security import Principal
from app.services import ingredients, purchasing
S = make_sessionmaker(make_engine(os.environ["APP_DATABASE_URL"]), capture=True)
with S() as db:
    b = Branch(code="CLI", name="CLI", country="UG", currency="USD", currency_decimals=2)
    u = User(email=f"cli-{uuid.uuid4().hex[:6]}@x.io", display_name="cli", password_hash="x")
    db.add_all([b, u]); db.flush()
    db.add(UserBranchRole(user_id=u.id, branch_id=b.id, role="owner")); d = Device(branch_id=b.id, name="cli")
    s = Supplier(code="CLISUP", name="S"); db.add_all([d, s]); db.commit()
    p = Principal(user=u, branch=b, role="owner", device=d)
    db.info["ctx"] = {"branch_id": b.id, "user_id": u.id, "device_id": d.id, "op": "cli"}
    ing = ingredients.create_ingredient(db, p, {"code": "CLIING", "name": "x", "base_dimension": "mass",
                                               "purchase_unit": "kg", "consumption_unit": "g"}); db.commit()
    purchasing.confirm_purchase(db, p, {"supplier_id": s.id, "invoice_ref": "CLI-1", "invoice_date": date(2026, 1, 1),
        "lines": [{"ingredient_id": ing.id, "quantity": D(1), "unit": "kg", "unit_price": D(5)}]}); db.commit()
print("ok")
"""


def test_capture_is_active_in_a_bare_process(engine):
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    r = subprocess.run([sys.executable, "-c", SCRIPT], env=env, cwd=BACKEND, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    with engine.connect() as c:
        topics = c.execute(text("SELECT topic FROM outbox_event")).scalars().all()
    assert "purchase.confirmed" in topics, topics
