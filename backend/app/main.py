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

from .api import auth, devices, files, ingredients, purchases, recipes, reports, stock, suppliers, sync_admin
from .config import Settings, get_settings
from .db import get_db, make_engine, make_sessionmaker
from .errors import AppError, app_error_handler, validation_error_handler
from .logging_setup import RequestLogMiddleware, configure_logging

log = logging.getLogger("app")


def create_app(settings: Settings | None = None, database_url: str | None = None) -> FastAPI:
    """One codebase, two deployment profiles: 'branch' (restaurant server, system of record) and
    'cloud' (central replica, monitoring, remote management)."""
    s = settings or get_settings()
    s.validate_for_production()
    configure_logging()
    profile = s.deployment_profile
    app = FastAPI(title="Restaurant Costing, Purchasing & FIFO Inventory API", version="0.2.0",
                  docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
    app.state.settings = s
    app.state.profile = profile
    app.state.engine = make_engine(database_url or s.database_url)
    app.state.Session = make_sessionmaker(app.state.engine, capture=(profile == "branch"))
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(RequestLogMiddleware)
    if s.env != "production":
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"],
                           allow_headers=["*"], allow_credentials=False)

    api = APIRouter(prefix="/api/v1")
    if profile == "cloud":
        from .cloud import api as cloud_api
        api.include_router(cloud_api.people)
        api.include_router(cloud_api.machine)
    else:
        for r in (auth, devices, ingredients, recipes, suppliers, purchases, files, stock, reports, sync_admin):
            api.include_router(r.router)

    @api.get("/profile", tags=["ops"])
    def get_profile():
        return {"profile": profile}

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
        checks["mode"] = "cloud" if profile == "cloud" else "local_branch"
        if profile == "branch":
            row = db.execute(text("SELECT count(*) FILTER (WHERE status IN ('pending','retry','failed')), "
                                  "count(*) FILTER (WHERE status = 'failed') FROM outbox_event")).one()
            st = db.execute(text("SELECT registered_at IS NOT NULL, last_success_at, last_error, halted_reason "
                                 "FROM sync_state WHERE id = 1")).one()
            checks["sync_pending_events"], checks["sync_failed_events"] = row[0], row[1]
            checks["cloud_registered"], checks["cloud_last_success_at"] = st[0], st[1] and st[1].isoformat()
            checks["cloud_last_error"], checks["sync_halted"] = st[2], st[3]
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
