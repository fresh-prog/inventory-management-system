from fastapi import Header, Response
from sqlalchemy.orm import Session

from ..jsonenc import enc
from ..security import Principal
from ..services.platform import run_idempotent


def idempotent_post(db: Session, p: Principal, response: Response, key: str | None, operation: str,
                    body, fn, status_code: int = 201):
    status, result, replay = run_idempotent(db, p, key, operation, body, lambda: enc(fn()), status_code)
    response.status_code = status
    if replay:
        response.headers["Idempotent-Replay"] = "true"
    return result


IdemKey = Header(default=None, alias="Idempotency-Key")
