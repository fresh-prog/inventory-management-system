"""RFC 6238 TOTP (30 s, 6 digits, SHA-1) — compatible with Google Authenticator, Authy, etc."""

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote


def new_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def code_at(secret: str, t: float, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = struct.pack(">Q", int(t // step))
    h = hmac.new(key, counter, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def verify(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    now = time.time()
    return any(hmac.compare_digest(code_at(secret, now + i * 30), code) for i in range(-window, window + 1))


def otpauth_uri(secret: str, email: str, issuer: str = "KitchenLedger Cloud") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&period=30&digits=6"
