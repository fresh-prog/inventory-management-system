import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..errors import BusinessRuleError, NotFound
from ..jsonenc import enc
from ..models import PurchaseAttachment, StoredFile
from ..security import Principal, require
from ..services import files as svc

router = APIRouter(tags=["documents"])


def file_out(f: StoredFile) -> dict:
    return {"id": f.id, "name": f.original_name, "mime_type": f.mime_type, "size_bytes": f.size_bytes,
            "sha256": f.sha256, "created_at": f.created_at, "stored_locally": True,
            "cloud_status": "verified" if f.cloud_verified_at else "pending",
            "cloud_verified_at": f.cloud_verified_at}


@router.post("/purchases/{purchase_id}/attachments", status_code=201)
async def upload(purchase_id: uuid.UUID, file: UploadFile = File(...),
                 p: Principal = Depends(require("purchase.create")), db: Session = Depends(get_db)):
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise BusinessRuleError(f"The file is larger than {get_settings().max_upload_mb} MB")
    f = svc.attach_to_purchase(db, p, purchase_id, data, file.filename or "receipt")
    db.commit()
    return enc(file_out(f))


@router.get("/purchases/{purchase_id}/attachments")
def list_attachments(purchase_id: uuid.UUID, p: Principal = Depends(require("purchase.read")),
                     db: Session = Depends(get_db)):
    rows = db.scalars(select(StoredFile).join(PurchaseAttachment, PurchaseAttachment.file_id == StoredFile.id)
                      .where(PurchaseAttachment.purchase_id == purchase_id, StoredFile.branch_id == p.branch.id)
                      .order_by(StoredFile.created_at)).all()
    return enc([file_out(f) for f in rows])


@router.get("/files/{file_id}")
def download(file_id: uuid.UUID, p: Principal = Depends(require("purchase.read")), db: Session = Depends(get_db)):
    f = db.get(StoredFile, file_id)
    if not f or f.branch_id != p.branch.id:
        raise NotFound("File not found")
    path = svc.path_for(f)
    if not path.exists():
        raise NotFound("The file is missing from local storage. Restore it from backup or the cloud copy.")
    return FileResponse(path, media_type=f.mime_type, filename=f.original_name,
                        headers={"X-Content-SHA256": f.sha256, "Cache-Control": "private, max-age=3600"})
