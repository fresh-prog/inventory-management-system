"""RFC 9457 problem-details errors."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    status = 400
    code = "BAD_REQUEST"
    title = "Bad request"

    def __init__(self, detail: str, fields: dict | None = None, **extra):
        super().__init__(detail)
        self.detail = detail
        self.fields = fields or {}
        self.extra = extra


class NotFound(AppError):
    status, code, title = 404, "NOT_FOUND", "Not found"


class Forbidden(AppError):
    status, code, title = 403, "FORBIDDEN", "Forbidden"


class Unauthorized(AppError):
    status, code, title = 401, "UNAUTHORIZED", "Authentication required"


class Conflict(AppError):
    status, code, title = 409, "CONFLICT", "Conflict"


class BusinessRuleError(AppError):
    status, code, title = 422, "BUSINESS_RULE", "Business rule violated"


class InsufficientStock(BusinessRuleError):
    code, title = "INSUFFICIENT_STOCK", "Insufficient stock"


def problem(status: int, code: str, title: str, detail: str, fields=None, **extra):
    body = {"type": f"about:blank#{code.lower()}", "title": title, "status": status,
            "detail": detail, "code": code, "fields": fields or {}, **extra}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


async def app_error_handler(_: Request, exc: AppError):
    return problem(exc.status, exc.code, exc.title, exc.detail, exc.fields, **exc.extra)


async def validation_error_handler(_: Request, exc: RequestValidationError):
    fields = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path"))
        fields[loc or "request"] = err["msg"]
    return problem(422, "VALIDATION_ERROR", "Invalid input",
                   "Some fields are invalid. Please check and try again.", fields)
