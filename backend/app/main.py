import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from .api import auth, ingredients, purchases, recipes, reports, stock, suppliers
from .config import get_settings
from .db import get_db
from .errors import AppError, app_error_handler, validation_error_handler
from .logging_setup import RequestLogMiddleware, configure_logging

log = logging.getLogger("app")


def create_app() -> FastAPI:
    s = get_settings()
    s.validate_for_production()
    configure_logging()
    app = FastAPI(title="Restaurant Costing, Purchasing & FIFO Inventory API", version="0.2.0",
                  docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(RequestLogMiddleware)
    if s.env != "production":
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"],
                           allow_headers=["*"], allow_credentials=False)

    api = APIRouter(prefix="/api/v1")
    for r in (auth, ingredients, recipes, suppliers, purchases, stock, reports):
        api.include_router(r.router)

    @api.get("/health/live", tags=["ops"])
    def live():
        return {"status": "ok"}

    @api.get("/health/ready", tags=["ops"])
    def ready(db: Session = Depends(get_db)):
        checks = {}
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:  # pragma: no cover
            checks["database"] = f"error: {e.__class__.__name__}"
        usage = shutil.disk_usage("/")
        free_pct = round(usage.free / usage.total * 100, 1)
        checks["disk_free_percent"] = free_pct
        checks["disk"] = "ok" if free_pct > 10 else "low"
        pending = db.execute(text("SELECT count(*) FROM outbox_event WHERE acked_at IS NULL")).scalar()
        checks["sync_pending_events"] = pending
        checks["mode"] = "local_branch"
        ok = checks["database"] == "ok"
        return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)

    app.include_router(api)

    dist = s.frontend_dist and Path(s.frontend_dist).resolve()
    if dist and dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (dist / path).resolve()
            if path and f.is_file() and dist in f.parents:  # no path traversal outside dist
                return FileResponse(f)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
