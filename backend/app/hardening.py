"""HTTP hardening: security headers, rate limiting and safe error responses."""

import logging
import threading
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, DBAPIError, OperationalError
from starlette.middleware.base import BaseHTTPMiddleware

from .errors import problem
from .logging_setup import request_id_var

log = logging.getLogger("app.error")

SPA_CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
           "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; "
           "base-uri 'self'; form-action 'self'")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        path = request.url.path
        if path.startswith("/api/v1/files/"):
            # user-supplied documents: never executable in our origin
            h["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
        elif path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        else:
            h.setdefault("Content-Security-Policy", SPA_CSP)
        if request.url.scheme == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp


class RateLimiter(BaseHTTPMiddleware):
    """Sliding-window limits per client IP (per process). Sensitive endpoints get tight limits;
    everything else a generous ceiling that only abuse reaches. Behind Caddy the client IP comes
    from X-Forwarded-For via uvicorn --proxy-headers."""

    def __init__(self, app, rules: list[tuple[str, int, int]]):
        super().__init__(app)
        self.rules = rules  # (path prefix, max requests, window seconds); first match wins
        self.hits: dict[tuple[str, str], deque] = {}
        self.lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        rule = next(((p, n, w) for p, n, w in self.rules if path.startswith(p)), None)
        if rule and request.method != "OPTIONS":
            prefix, limit, window = rule
            ip = request.client.host if request.client else "?"
            now = time.monotonic()
            with self.lock:
                q = self.hits.setdefault((prefix, ip), deque())
                while q and q[0] <= now - window:
                    q.popleft()
                if len(q) >= limit:
                    retry = int(window - (now - q[0])) + 1
                    return JSONResponse({"type": "about:blank#rate_limited", "title": "Too many requests",
                                         "status": 429, "code": "RATE_LIMITED",
                                         "detail": f"Too many requests. Please wait {retry} s and try again.",
                                         "fields": {}}, status_code=429, headers={"Retry-After": str(retry)},
                                        media_type="application/problem+json")
                q.append(now)
                if len(self.hits) > 50_000:  # bound memory under address-spraying
                    self.hits.clear()
        return await call_next(request)


def install_error_handlers(app) -> None:
    async def data_error(_: Request, exc: DataError):
        return problem(422, "INVALID_VALUE", "Invalid input",
                       "A value is out of range or contains invalid characters.")

    async def db_unavailable(_: Request, exc: DBAPIError):
        if isinstance(exc, OperationalError) or exc.connection_invalidated:
            log.error("database unavailable: %s", str(exc).splitlines()[0][:200])
            return problem(503, "DATABASE_UNAVAILABLE", "Service temporarily unavailable",
                           "The restaurant database is not reachable right now. Nothing was saved. "
                           "Please try again in a moment.")
        return await unexpected(_, exc)

    async def unexpected(request: Request, exc: Exception):
        # This handler runs outside the request-logging middleware, whose context has already been reset:
        # take the id from the request so the user's reference matches the server log entry.
        rid = getattr(request.state, "request_id", None) or request_id_var.get()
        token = request_id_var.set(rid)
        try:
            log.exception("unhandled error on %s %s", request.method, request.url.path)
        finally:
            request_id_var.reset(token)
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
            return problem(507, "STORAGE_FULL", "Storage full",
                           "The server's disk is full. Nothing was saved. Ask the administrator to free space.")
        resp = problem(500, "INTERNAL_ERROR", "Something went wrong",
                       f"An unexpected error occurred and nothing was saved. Reference: {rid}",
                       request_id=rid)
        resp.headers["x-request-id"] = rid
        return resp

    app.add_exception_handler(DataError, data_error)
    app.add_exception_handler(DBAPIError, db_unavailable)
    app.add_exception_handler(Exception, unexpected)
