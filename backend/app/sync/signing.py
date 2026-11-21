"""HMAC request signing between a branch server and the cloud, and signed acknowledgements.

Every request carries X-Branch-Id, X-Timestamp and X-Signature = HMAC-SHA256(secret,
"<ts>\\n<METHOD>\\n<path>\\n<sha256(body)>"). Requests older than 5 minutes are rejected (replay
protection on top of HTTPS). Acks are HMAC(secret, "ack:<event_id>:<sequence>") so the branch only
marks an event synced on an acknowledgement it can verify."""

import hashlib
import hmac
import time

MAX_SKEW_SECONDS = 300


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def sign(secret: str, method: str, path: str, ts: str, body: bytes) -> str:
    msg = f"{ts}\n{method.upper()}\n{path}\n{body_hash(body)}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def headers(secret: str, cloud_branch_id: str, method: str, path: str, body: bytes) -> dict:
    ts = str(int(time.time()))
    return {"X-Branch-Id": cloud_branch_id, "X-Timestamp": ts, "X-Signature": sign(secret, method, path, ts, body)}


def verify(secret: str, method: str, path: str, ts: str, body: bytes, signature: str) -> bool:
    try:
        if abs(time.time() - int(ts)) > MAX_SKEW_SECONDS:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(sign(secret, method, path, ts, body), signature or "")


def ack_signature(secret: str, event_id: str, sequence: int) -> str:
    return hmac.new(secret.encode(), f"ack:{event_id}:{sequence}".encode(), hashlib.sha256).hexdigest()


def file_receipt(secret: str, file_id: str, sha256: str) -> str:
    return hmac.new(secret.encode(), f"file:{file_id}:{sha256}".encode(), hashlib.sha256).hexdigest()


def derive_branch_secret(master_key: str, cloud_branch_id: str, version: int) -> str:
    """The cloud stores no branch secrets: they are derived from the master key."""
    return hmac.new(master_key.encode(), f"branch:{cloud_branch_id}:{version}".encode(), hashlib.sha256).hexdigest()
