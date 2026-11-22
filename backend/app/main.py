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
from .hardening import RateLimiter, SecurityHeaders, install_error_handlers
from .logging_setup import RequestLogMiddleware, configure_logging

log = logging.getLogger("app")


def create_app(settings: Settings | None = None, database_url: str | None = None) -> FastAPI:
    """One codebase, two deployment profiles: 'branch' (restaurant server, system of record) and
    'cloud' (central replica, monitoring, remote management)."""
    s = settings or get_settings()
    s.validate_for_production()
    configure_logging()
    profile = s.deployment_profile
    public_docs = s.env != "production"  # never expose the API map in production
    app = FastAPI(title="Restaurant Costing, Purchasing & FIFO Inventory API", version=s.app_version,
                  docs_url="/api/docs" if public_docs else None, redoc_url="/api/redoc" if public_docs else None,
                  openapi_url="/api/openapi.json" if public_docs else None)
    app.state.settings = s
    app.state.profile = profile
    app.state.engine = make_engine(database_url or s.database_url)
    app.state.Session = make_sessionmaker(app.state.engine, capture=(profile == "branch"))
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    install_error_handlers(app)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(RateLimiter, rules=[
        ("/api/v1/auth/login", s.rate_limit_login_per_minute, 60),
        ("/api/v1/cloud/auth/", s.rate_limit_login_per_minute, 60),
        ("/api/v1/setup", s.rate_limit_login_per_minute, 60),
        ("/api/v1/cloud-sync/register", s.rate_limit_register_per_hour, 3600),
        ("/api/v1/", s.rate_limit_api_per_minute, 60),
    ])
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

    from .api import metrics
    api.include_router(metrics.router)

    @api.get("/profile", tags=["ops"])
    def get_profile():
        return {"profile": profile}

    @api.get("/health/live", tags=["ops"])
    def live():
        return {"status": "ok", "version": s.app_version}

    @api.get("/health/ready", tags=["ops"])
    def ready(db: Session = Depends(get_db)):
        checks = {}
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:  # pragma: no cover
            checks["database"] = f"error: {e.__class__.__name__}"
        from pathlib import Path
        data_dir = Path(s.files_dir).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(data_dir)  # the volume holding documents (and usually the DB)
        free_pct = round(usage.free / usage.total * 100, 1)
        checks["disk_free_percent"] = free_pct
        checks["disk"] = "ok" if free_pct > 10 else "low"
        checks["mode"] = "cloud" if profile == "cloud" else "local_branch"
        checks["version"] = s.app_version
        # sync details (errors, queue sizes) are NOT public: see the authenticated /sync/status
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
