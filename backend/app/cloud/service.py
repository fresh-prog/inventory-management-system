"""Cloud services: branch provisioning/registration, in-order event application, commands, files."""

import hashlib
import os
import secrets
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import Engine, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import BusinessRuleError, Conflict, NotFound
from ..sync.apply import apply_rows, reset_replica
from ..sync.signing import ack_signature, derive_branch_secret, file_receipt
from ..sync.spec import KNOWN_SCHEMA_VERSIONS, SNAPSHOT
from .models import BranchCommand, CloudAudit, CloudBranch, CloudFile, CloudInbox

BACKEND_DIR = Path(__file__).resolve().parents[2]


def now() -> datetime:
    return datetime.now(UTC)


def code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def branch_secret(b: CloudBranch) -> str:
    return derive_branch_secret(get_settings().cloud_master_key, str(b.id), b.secret_version)


def audit(db: Session, user_id, branch_id, action: str, detail: dict | None = None) -> None:
    from ..jsonenc import enc
    db.add(CloudAudit(user_id=user_id, cloud_branch_id=branch_id, action=action, detail=enc(detail) if detail else None))


# ───────────────────────── branches ─────────────────────────
def create_branch(db: Session, user_id, code: str, name: str) -> tuple[CloudBranch, str]:
    reg = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    b = CloudBranch(code=code.upper(), name=name, registration_code_hash=code_hash(reg),
                    registration_expires_at=now() + timedelta(days=7))
    db.add(b)
    db.flush()
    audit(db, user_id, b.id, "branch.create", {"code": b.code})
    return b, reg


def new_registration_code(db: Session, user_id, b: CloudBranch) -> str:
    if b.status not in ("awaiting_registration",):
        raise BusinessRuleError("This branch is already registered. Rotate its key instead.")
    reg = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    b.registration_code_hash, b.registration_expires_at = code_hash(reg), now() + timedelta(days=7)
    audit(db, user_id, b.id, "branch.registration_code")
    return reg


def provision_schema(engine: Engine, schema: str) -> None:
    """Create/upgrade the branch's isolated replica schema with the SAME migrations the branch runs."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["url"] = engine.url.render_as_string(hide_password=False)
    cfg.attributes["schema"] = schema
    alembic_command.upgrade(cfg, "head")


def register(db: Session, engine: Engine, reg_code: str, info: dict) -> tuple[CloudBranch, str]:
    b = db.scalar(select(CloudBranch).where(CloudBranch.registration_code_hash == code_hash(reg_code))
                  .with_for_update())
    if not b or b.status != "awaiting_registration" or not b.registration_expires_at or b.registration_expires_at < now():
        raise BusinessRuleError("The registration code is invalid or has expired.")
    local_id = uuid.UUID(info["branch_id"])
    if db.scalar(select(CloudBranch.id).where(CloudBranch.branch_id == local_id)):
        raise Conflict("This branch server is already registered to another cloud branch.")
    b.branch_id = local_id
    b.schema_name = "br_" + local_id.hex
    b.currency = info.get("currency")
    b.status, b.registered_at, b.registration_code_hash = "active", now(), None
    db.commit()
    provision_schema(engine, b.schema_name)
    audit(db, None, b.id, "branch.registered", {"branch_server_id": info["branch_id"], "name": info.get("name")})
    db.commit()
    return b, branch_secret(b)


@contextmanager
def replica_session(SessionMaker, b: CloudBranch):
    """Read-only session on one branch's replica (central reporting, validation)."""
    if not b.schema_name:
        raise BusinessRuleError("This branch has not synchronised yet.")
    db = SessionMaker()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text(f'SET LOCAL search_path TO "{b.schema_name}", public'))
        yield db
    finally:
        db.close()


# ───────────────────────── event application ─────────────────────────
def apply_events(engine: Engine, SessionMaker, b_id: uuid.UUID, events: list[dict], reported: dict | None) -> dict:
    """Apply events strictly in sequence order, one transaction per event, serialised per branch
    by a row lock. Stops at the first problem; everything acknowledged before it is durable.
    Duplicates are re-acknowledged, never re-applied."""
    with SessionMaker() as db:
        b = db.get(CloudBranch, b_id)
        b.last_seen_at = now()
        if reported is not None:
            b.branch_reported = reported
        db.commit()
        secret, schema = branch_secret(b), b.schema_name
    acks, stopped, high_water = [], None, None
    for ev in events:
        eid, seq = uuid.UUID(ev["id"]), int(ev["sequence"])
        with engine.begin() as conn:
            row = conn.execute(select(CloudBranch.last_applied_seq, CloudBranch.status, CloudBranch.halted_reason)
                               .where(CloudBranch.id == b_id).with_for_update()).one()
            high_water = row.last_applied_seq
            if row.status == "halted":
                stopped = {"reason": "halted", "detail": row.halted_reason}
                break
            if conn.execute(select(CloudInbox.sequence).where(
                    CloudInbox.cloud_branch_id == b_id, CloudInbox.event_id == eid)).scalar() is not None:
                acks.append({"id": ev["id"], "sequence": seq, "signature": ack_signature(secret, ev["id"], seq),
                             "duplicate": True})
                continue
            if seq <= row.last_applied_seq:
                reason = (f"Sequence regression: event {eid} has sequence {seq} but {row.last_applied_seq} is "
                          f"already applied. The branch database may have been restored from an older backup.")
                conn.execute(update(CloudBranch).where(CloudBranch.id == b_id)
                             .values(status="halted", halted_reason=reason))
                stopped = {"reason": "sequence_regression", "detail": reason, "event_id": ev["id"]}
                break
            if ev.get("schema_version") not in KNOWN_SCHEMA_VERSIONS:
                stopped = {"reason": "schema_version", "event_id": ev["id"],
                           "detail": f"Cloud does not know schema {ev.get('schema_version')}; upgrade the cloud first."}
                break
            sp = conn.begin_nested()
            try:
                conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
                payload = ev["payload"]
                if ev["kind"] == SNAPSHOT and payload.get("reset"):
                    reset_replica(conn)
                n = apply_rows(conn, payload.get("rows", []))
                conn.execute(insert(CloudInbox.__table__).values(
                    cloud_branch_id=b_id, event_id=eid, sequence=seq, kind=ev["kind"], topic=ev["topic"][:64],
                    device_id=ev.get("device_id"), user_id=ev.get("user_id"),
                    schema_version=ev["schema_version"], created_at=datetime.fromisoformat(ev["created_at"]),
                    row_count=n, payload=payload))
                if payload.get("command_result"):
                    cr = payload["command_result"]
                    conn.execute(update(BranchCommand).where(BranchCommand.id == uuid.UUID(cr["command_id"]),
                                                             BranchCommand.cloud_branch_id == b_id)
                                 .values(status=cr["status"], result=cr.get("result"), resolved_at=now()))
                conn.execute(update(CloudBranch).where(CloudBranch.id == b_id).values(
                    last_applied_seq=seq, last_event_at=datetime.fromisoformat(ev["created_at"]),
                    last_schema_version=ev["schema_version"], last_error=None))
                sp.commit()
            except Exception as e:  # noqa: BLE001 — reported to the branch, which retries with backoff
                sp.rollback()
                msg = f"{type(e).__name__}: {str(e).splitlines()[0][:400]}"
                conn.execute(update(CloudBranch).where(CloudBranch.id == b_id).values(last_error=msg))
                stopped = {"reason": "apply_failed", "event_id": ev["id"], "detail": msg}
                break
            conn.execute(text("SET LOCAL search_path TO public"))
        high_water = seq
        acks.append({"id": ev["id"], "sequence": seq, "signature": ack_signature(secret, ev["id"], seq)})
    if high_water is None:
        with SessionMaker() as db:
            high_water = db.get(CloudBranch, b_id).last_applied_seq
    return {"acks": acks, "high_water": high_water, "stopped": stopped}


# ───────────────────────── commands ─────────────────────────
COMMAND_TYPES = {"ingredient.create", "ingredient.update", "ingredient.price", "supplier.create", "supplier.update",
                 "branch_settings.update", "recipe.save_draft", "recipe.publish", "payment.post", "device.revoke"}


def validate_command(SessionMaker, b: CloudBranch, ctype: str, payload: dict) -> dict:
    """Validate shape with the branch's own input schemas and check the target exists in the
    replica. The branch re-validates everything when it executes the command."""
    from ..models import Branch, Device, Ingredient, Recipe, Supplier
    from ..schemas import inputs as I
    if ctype not in COMMAND_TYPES:
        raise BusinessRuleError(f"Unknown command type {ctype}")
    p = dict(payload)
    shapes = {"ingredient.create": I.IngredientIn, "ingredient.price": I.PriceIn, "supplier.create": I.SupplierIn,
              "supplier.update": I.SupplierIn, "payment.post": I.PaymentIn}
    with replica_session(SessionMaker, b) as db:
        if ctype == "ingredient.update":
            I.IngredientPatch(**p["changes"])
            ing = db.get(Ingredient, uuid.UUID(p["ingredient_id"]))
            if not ing:
                raise NotFound("Ingredient not found in this branch")
            p.setdefault("base_version", ing.version)
        elif ctype == "ingredient.price":
            I.PriceIn(**p["price"])
            if not db.get(Ingredient, uuid.UUID(p["ingredient_id"])):
                raise NotFound("Ingredient not found in this branch")
        elif ctype == "supplier.update":
            I.SupplierIn(**p["supplier"])
            s = db.get(Supplier, uuid.UUID(p["supplier_id"]))
            if not s:
                raise NotFound("Supplier not found in this branch")
            p.setdefault("base_version", s.version)
        elif ctype == "branch_settings.update":
            I.BranchSettingsIn(**p["changes"])
            br = db.get(Branch, b.branch_id)
            p.setdefault("base_version", br.settings_version)
        elif ctype == "recipe.save_draft":
            I.RecipeDraftIn(**p["draft"])
            r = db.get(Recipe, uuid.UUID(p["recipe_id"]))
            if not r:
                raise NotFound("Recipe not found in this branch")
            p.setdefault("base_version_no", max((v.version_no for v in r.versions), default=0))
        elif ctype == "recipe.publish":
            if not db.get(Recipe, uuid.UUID(p["recipe_id"])):
                raise NotFound("Recipe not found in this branch")
            int(p["version_no"])
        elif ctype == "device.revoke":
            if not db.get(Device, uuid.UUID(p["device_id"])):
                raise NotFound("Device not found")
        elif ctype in shapes:
            key = {"ingredient.create": "ingredient", "supplier.create": "supplier", "payment.post": "payment"}[ctype]
            shapes[ctype](**p[key])
    return p


def create_command(db: Session, SessionMaker, b: CloudBranch, user, ctype: str, payload: dict) -> BranchCommand:
    if b.status != "active":
        raise BusinessRuleError("Commands can only be sent to an active, registered branch")
    p = validate_command(SessionMaker, b, ctype, payload)
    cmd = BranchCommand(cloud_branch_id=b.id, type=ctype, payload=p, created_by=user.id, created_by_name=user.email)
    db.add(cmd)
    db.flush()
    audit(db, user.id, b.id, "command.create", {"type": ctype, "command_id": cmd.id})
    return cmd


def pending_commands(db: Session, b: CloudBranch, after_seq: int, limit: int = 50) -> list[BranchCommand]:
    cmds = db.scalars(select(BranchCommand).where(BranchCommand.cloud_branch_id == b.id,
                                                  BranchCommand.sequence > after_seq)
                      .order_by(BranchCommand.sequence).limit(limit)).all()
    for c in cmds:
        if c.status == "queued":
            c.status, c.delivered_at = "delivered", now()
    db.commit()
    return cmds


# ───────────────────────── files (resumable, checksum-verified) ─────────────────────────
def files_root() -> Path:
    p = Path(get_settings().files_dir).resolve() / "cloud"
    p.mkdir(parents=True, exist_ok=True)
    return p


def file_status(db: Session, b: CloudBranch, file_id: uuid.UUID) -> dict:
    f = db.get(CloudFile, file_id)
    if not f or f.cloud_branch_id != b.id:
        return {"received_bytes": 0, "status": "absent"}
    return {"received_bytes": f.received_bytes, "status": f.status}


def receive_chunk(db: Session, b: CloudBranch, file_id: uuid.UUID, offset: int, sha256: str, size: int,
                  chunk: bytes) -> dict:
    f = db.scalar(select(CloudFile).where(CloudFile.id == file_id).with_for_update())
    if f and f.cloud_branch_id != b.id:
        raise Conflict("File id belongs to another branch")
    if f is None:
        f = CloudFile(id=file_id, cloud_branch_id=b.id, sha256=sha256, size_bytes=size, received_bytes=0,
                      storage_key=f"{b.id}/{file_id}")
        db.add(f)
        db.flush()
    if f.status == "verified":
        return {"received_bytes": f.received_bytes, "status": "verified"}
    if f.sha256 != sha256 or f.size_bytes != size:
        raise Conflict("File metadata changed between chunks")
    if offset != f.received_bytes:
        db.rollback()
        raise Conflict("Offset mismatch — resume from received_bytes", received_bytes=f.received_bytes)
    if offset + len(chunk) > size:
        raise BusinessRuleError("Chunk exceeds declared size")
    part = files_root() / (f.storage_key + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    with open(part, "r+b" if part.exists() else "wb") as fh:
        fh.seek(offset)
        fh.write(chunk)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())
    f.received_bytes = offset + len(chunk)
    db.commit()
    return {"received_bytes": f.received_bytes, "status": f.status}


def complete_file(db: Session, b: CloudBranch, SessionMaker, file_id: uuid.UUID) -> dict:
    from ..models import StoredFile
    f = db.scalar(select(CloudFile).where(CloudFile.id == file_id).with_for_update())
    if not f or f.cloud_branch_id != b.id:
        raise NotFound("Upload not started")
    if f.status != "verified":
        with replica_session(SessionMaker, b) as rdb:
            meta = rdb.get(StoredFile, file_id)
            if meta is None:
                raise Conflict("File metadata has not synchronised yet; retry after the next event push")
            if meta.sha256 != f.sha256:
                raise Conflict("Checksum does not match the synchronised file record")
        part = files_root() / (f.storage_key + ".part")
        if f.received_bytes != f.size_bytes or not part.exists():
            raise Conflict("Upload incomplete", received_bytes=f.received_bytes)
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
        if digest != f.sha256:
            part.unlink(missing_ok=True)
            f.received_bytes = 0
            db.commit()
            raise Conflict("Checksum mismatch — upload discarded, resend the file")
        os.replace(part, files_root() / f.storage_key)
        f.status, f.verified_at = "verified", now()
        db.commit()
    return {"status": "verified", "sha256": f.sha256, "receipt": file_receipt(branch_secret(b), str(f.id), f.sha256)}
