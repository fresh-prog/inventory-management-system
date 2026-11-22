"""Local document storage (receipts). Works without internet; the cloud copy is a separate,
checksum-verified transfer handled by the sync worker."""

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import AppError, BusinessRuleError, NotFound


class StorageFull(AppError):
    status, code, title = 507, "STORAGE_FULL", "Storage full"
from ..models import PurchaseAttachment, StoredFile
from ..security import Principal
from .platform import audit, emit

# Detect the real type from the content, never trust the filename or the browser.
SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"%PDF-", "application/pdf", ".pdf"),
]


def sniff(head: bytes) -> tuple[str, str] | None:
    for sig, mime, ext in SIGNATURES:
        if head.startswith(sig):
            return mime, ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def files_root() -> Path:
    root = Path(get_settings().files_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_for(f: StoredFile) -> Path:
    p = (files_root() / f.storage_key).resolve()
    if files_root() not in p.parents:
        raise NotFound("File not found")
    return p


def store(db: Session, p: Principal, data: bytes, original_name: str) -> StoredFile:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if not data:
        raise BusinessRuleError("The file is empty")
    if len(data) > max_bytes:
        raise BusinessRuleError(f"The file is larger than {get_settings().max_upload_mb} MB")
    kind = sniff(data[:16])
    if not kind:
        raise BusinessRuleError("Only JPEG, PNG, WEBP or PDF files are accepted")
    mime, ext = kind
    digest = hashlib.sha256(data).hexdigest()
    fid = uuid.uuid4()
    key = f"{p.branch.id}/{digest[:2]}/{fid}{ext}"
    dest = files_root() / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    reserve = get_settings().min_free_mb * 1024 * 1024
    if shutil.disk_usage(files_root()).free - len(data) < reserve:
        raise StorageFull("The server is almost out of disk space, so the document was not saved. "
                          "Ask the administrator to free space (the database needs it too).")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)  # atomic: a crash never leaves a half-written file under its final name
    except OSError as e:
        tmp.unlink(missing_ok=True)
        if e.errno == 28:
            raise StorageFull("The server's disk is full, so the document was not saved.") from e
        raise
    f = StoredFile(id=fid, branch_id=p.branch.id, original_name=(original_name or "upload")[:200], mime_type=mime,
                   size_bytes=len(data), sha256=digest, storage_key=key, uploaded_by=p.user.id,
                   device_id=p.device.id if p.device else None)
    try:
        db.add(f)
        db.flush()
    except Exception:
        dest.unlink(missing_ok=True)  # never leave a file without its record
        raise
    return f


def attach_to_purchase(db: Session, p: Principal, purchase_id: uuid.UUID, data: bytes, name: str) -> StoredFile:
    from .purchasing import get_purchase
    inv = get_purchase(db, p, purchase_id)
    f = store(db, p, data, name)
    db.add(PurchaseAttachment(purchase_id=inv.id, file_id=f.id, kind="receipt"))
    audit(db, p, "file.attach", "purchase_invoice", inv.id, {"file_id": f.id, "sha256": f.sha256, "bytes": f.size_bytes})
    emit(db, p.branch.id, "file.attached", inv.id, {"file_id": f.id, "sha256": f.sha256})
    return f


def verify_local(f: StoredFile) -> bool:
    try:
        return hashlib.sha256(path_for(f).read_bytes()).hexdigest() == f.sha256
    except FileNotFoundError:
        return False
