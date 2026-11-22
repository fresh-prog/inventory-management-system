import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFound
from ..jsonenc import J, enc
from ..models import Device, User
from ..security import Principal, get_principal, require
from ..services.common import now
from ..services.platform import audit

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    user_agent: str | None = Field(default=None, max_length=300)


@router.post("", status_code=201)
def register(body: DeviceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Register this phone/tablet/PC. The returned id is sent as X-Device-Id on every request."""
    d = Device(branch_id=p.branch.id, name=body.name, user_agent=body.user_agent, registered_by=p.user.id,
               last_seen_at=now())
    db.add(d)
    db.flush()
    audit(db, p, "device.register", "device", d.id, {"name": d.name})
    db.commit()
    return J({"id": d.id, "name": d.name}, 201)


@router.get("")
def list_devices(p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    rows = db.execute(select(Device, User.display_name).outerjoin(User, User.id == Device.registered_by)
                      .where(Device.branch_id == p.branch.id).order_by(Device.created_at.desc())).all()
    return J([{"id": d.id, "name": d.name, "user_agent": d.user_agent, "registered_by": who,
                 "created_at": d.created_at, "revoked_at": d.revoked_at,
                 "is_current": p.device is not None and p.device.id == d.id} for d, who in rows])


@router.post("/{device_id}/revoke")
def revoke(device_id: uuid.UUID, p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    d = db.get(Device, device_id)
    if not d or d.branch_id != p.branch.id:
        raise NotFound("Device not found")
    if d.revoked_at is None:
        d.revoked_at = now()
        audit(db, p, "device.revoke", "device", d.id, {"name": d.name})
        db.commit()
    return {"ok": True}
