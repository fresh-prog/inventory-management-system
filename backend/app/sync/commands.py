"""Execute remote-management commands from the cloud ON THE BRANCH, with the branch's own
business rules, row locks and validations. Explicit conflict policies — never last-write-wins:

* ingredient/supplier/branch-settings updates carry the version the cloud user saw. If the branch
  changed it since, the command is a CONFLICT (nothing applied; current values returned).
* recipe drafts carry the latest version number seen; a newer version on the branch = CONFLICT.
* payments are validated against the branch's live supplier ledger under row locks; an
  allocation larger than the outstanding balance is REJECTED (e.g. paid locally meanwhile).
* manual prices are append-only history; "newest effective date wins" decides the current cost
  regardless of arrival order.
Each command is applied at most once (inbox primary key) and its result travels back to the cloud
in the same outbox event as the changes it made.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..errors import AppError, Conflict
from ..models import Branch, Device, Ingredient, InboxEvent, Recipe, Supplier, SyncState, User, UserBranchRole
from ..security import Principal, hash_password
from ..services import ingredients as ing_svc
from ..services import purchasing, recipes
from ..services.common import now
from ..services.platform import audit, emit

SYSTEM_EMAIL = "cloud-remote@system.local"


def ensure_system_user(SessionMaker, branch_id: uuid.UUID) -> None:
    """The branch-side identity for remote commands (cannot log in). Committed on its own."""
    with SessionMaker() as db:
        if db.scalar(select(User.id).where(User.email == SYSTEM_EMAIL)) is None:
            import secrets
            u = User(email=SYSTEM_EMAIL, display_name="Cloud (remote)", is_active=False,
                     password_hash=hash_password(secrets.token_urlsafe(32)))
            db.add(u)
            db.flush()
            db.add(UserBranchRole(user_id=u.id, branch_id=branch_id, role="viewer"))
            db.info["op"] = "system.user"
            db.commit()


def system_principal(db: Session, branch: Branch) -> Principal:
    u = db.scalar(select(User).where(User.email == SYSTEM_EMAIL))
    return Principal(user=u, branch=branch, role="owner", device=None)


def _execute(db: Session, p: Principal, cmd: dict) -> dict:
    t, pl = cmd["type"], cmd["payload"]
    if t == "ingredient.update":
        ing = ing_svc.get_ingredient(db, uuid.UUID(pl["ingredient_id"]))
        if ing.version != int(pl["base_version"]):
            raise Conflict(f"{ing.name} was changed at the branch (version {ing.version}, you edited "
                           f"version {pl['base_version']}).", current={"version": ing.version, "name": ing.name})
        ing_svc.update_ingredient(db, p, ing, _decimals(pl["changes"]), None)
        return {"ingredient_id": str(ing.id), "version": ing.version}
    if t == "ingredient.create":
        ing = ing_svc.create_ingredient(db, p, _decimals(pl["ingredient"]))
        return {"ingredient_id": str(ing.id)}
    if t == "ingredient.price":
        from datetime import date
        ing = ing_svc.get_ingredient(db, uuid.UUID(pl["ingredient_id"]))
        pr = pl["price"]
        h = ing_svc.record_manual_price(db, p, ing, Decimal(pr["quantity"]), pr["unit"], Decimal(pr["amount"]),
                                        date.fromisoformat(pr.get("effective_date") or date.today().isoformat()),
                                        pr.get("note"))
        return {"price_history_id": str(h.id)}
    if t in ("supplier.create", "supplier.update"):
        data = dict(pl["supplier"])
        if t == "supplier.create":
            s = Supplier(**{**data, "code": data["code"].upper()})
            db.add(s)
        else:
            s = db.get(Supplier, uuid.UUID(pl["supplier_id"]))
            if s is None:
                raise Conflict("Supplier no longer exists at the branch")
            if s.version != int(pl["base_version"]):
                raise Conflict(f"Supplier {s.name} was changed at the branch.", current={"version": s.version})
            for k, v in data.items():
                setattr(s, k, v.upper() if k == "code" else v)
            s.version += 1
        db.flush()
        audit(db, p, f"supplier.{t.split('.')[1]}", "supplier", s.id, {"via": "cloud"})
        emit(db, p.branch.id, "supplier.upserted", s.id, {"id": s.id})
        return {"supplier_id": str(s.id), "version": s.version}
    if t == "branch_settings.update":
        b = db.get(Branch, p.branch.id, with_for_update=True)
        if b.settings_version != int(pl["base_version"]):
            raise Conflict("Branch settings were changed at the branch.", current={"settings_version": b.settings_version})
        for k, v in _decimals(pl["changes"]).items():
            if k in ("default_target_food_cost_rate", "include_tax_in_cost", "name") and v is not None:
                setattr(b, k, v)
        b.settings_version += 1
        audit(db, p, "settings.update", "branch", b.id, {"via": "cloud", "changes": pl["changes"]})
        return {"settings_version": b.settings_version}
    if t == "recipe.save_draft":
        r = db.get(Recipe, uuid.UUID(pl["recipe_id"]))
        if r is None:
            raise Conflict("Recipe no longer exists at the branch")
        latest = max(v.version_no for v in r.versions)
        if latest != int(pl["base_version_no"]):
            raise Conflict(f"{r.name} has a newer version (v{latest}) at the branch.", current={"latest_version": latest})
        draft = dict(_decimals(pl["draft"]))
        draft["lines"] = [_decimals({**ln, "ingredient_id": uuid.UUID(ln["ingredient_id"])}) for ln in draft["lines"]]
        v = recipes.save_draft(db, p, r, draft)
        return {"recipe_id": str(r.id), "version_no": v.version_no}
    if t == "recipe.publish":
        r = db.get(Recipe, uuid.UUID(pl["recipe_id"]))
        if r is None:
            raise Conflict("Recipe no longer exists at the branch")
        v = recipes.publish(db, p, r, int(pl["version_no"]))
        return {"recipe_id": str(r.id), "version_no": v.version_no}
    if t == "payment.post":
        data = _decimals(pl["payment"])
        data["supplier_id"] = uuid.UUID(str(data["supplier_id"]))
        from datetime import date
        data["paid_on"] = date.fromisoformat(str(data["paid_on"]))
        data["allocations"] = [{"invoice_id": uuid.UUID(str(a["invoice_id"])), "amount": Decimal(str(a["amount"]))}
                               for a in data["allocations"]]
        pay = purchasing.post_payment(db, p, data)
        return {"payment_id": str(pay.id), "supplier_balance": str(purchasing.supplier_balance(db, p.branch.id, pay.supplier_id))}
    if t == "device.revoke":
        d = db.get(Device, uuid.UUID(pl["device_id"]))
        if d and d.revoked_at is None:
            d.revoked_at = now()
            audit(db, p, "device.revoke", "device", d.id, {"via": "cloud"})
        return {"device_id": pl["device_id"]}
    raise Conflict(f"This branch does not support command {t}; upgrade the branch server.")


DECIMAL_KEYS = {"quantity", "amount", "min_stock", "default_target_food_cost_rate", "portions_per_batch",
                "portion_size_qty", "cooking_shrinkage_rate", "target_food_cost_rate", "selling_price",
                "prep_loss_rate", "from_qty", "to_qty"}


def _decimals(d: dict) -> dict:
    return {k: (Decimal(str(v)) if k in DECIMAL_KEYS and v is not None else v) for k, v in d.items()}


def apply_command(SessionMaker, branch_id: uuid.UUID, cmd: dict) -> str:
    """Apply one command exactly once. Returns applied | rejected | conflict | duplicate."""
    cid = uuid.UUID(cmd["id"])
    ensure_system_user(SessionMaker, branch_id)
    db: Session = SessionMaker()
    try:
        branch = db.get(Branch, branch_id)
        p = system_principal(db, branch)
        db.info["ctx"] = {"branch_id": branch_id, "user_id": p.user.id, "device_id": None, "op": f"cloud.{cmd['type']}"}
        inserted = db.execute(insert(InboxEvent).values(source="cloud", event_id=cid, sequence=cmd["sequence"],
                                                        type=cmd["type"], status="applied")
                              .on_conflict_do_nothing().returning(InboxEvent.event_id)).scalar()
        if inserted is None:
            db.rollback()
            _advance(SessionMaker, cmd["sequence"])
            return "duplicate"
        try:
            result = _execute(db, p, cmd)
            status = "applied"
        except AppError as e:
            db.rollback()
            status = "conflict" if isinstance(e, Conflict) else "rejected"
            result = {"error": e.detail, **{k: v for k, v in e.extra.items()}}
            db.info["ctx"] = {"branch_id": branch_id, "user_id": p.user.id, "device_id": None, "op": f"cloud.{cmd['type']}"}
            db.execute(insert(InboxEvent).values(source="cloud", event_id=cid, sequence=cmd["sequence"],
                                                 type=cmd["type"], status=status, result=result))
        from ..jsonenc import enc
        result = enc({**result, "by": cmd.get("created_by")})
        if status == "applied":
            db.execute(InboxEvent.__table__.update().where(InboxEvent.source == "cloud", InboxEvent.event_id == cid)
                       .values(result=result))
        db.info["op"] = f"cloud.{cmd['type']}"
        db.info["command_result"] = {"command_id": str(cid), "status": status, "result": result}
        st = db.get(SyncState, 1, with_for_update=True)
        st.last_command_seq = max(st.last_command_seq, int(cmd["sequence"]))
        db.commit()  # changes + inbox + command result (outbox) + progress: one atomic commit
        return status
    finally:
        db.close()


def _advance(SessionMaker, seq: int) -> None:
    with SessionMaker() as db:
        st = db.get(SyncState, 1, with_for_update=True)
        st.last_command_seq = max(st.last_command_seq, int(seq))
        db.commit()
