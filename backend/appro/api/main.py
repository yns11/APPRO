"""FastAPI application factory. Serves the API under ``/api`` and the built frontend at ``/``."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import get_settings
from ..engine.models import DatasetError
from .routers import entries, files, mrp, pdp, reference


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the context (database tables, grants, reference bootstrap) as soon as the process
    starts – not at the first request – so that a synchronisation job launched right after a deployment
    finds the ``erp_*`` tables.  A failure is logged and retried at the first request (``/api/health``
    then names the cause instead of the application refusing to start)."""
    try:
        from ..services.context import get_context
        get_context()
    except Exception:  # pragma: no cover - needs a broken database
        logging.getLogger("appro").exception("Initialisation différée : la base n'est pas joignable au démarrage")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title=settings.app_title, version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=lifespan)

    @app.get("/api/health", tags=["health"])
    def health():
        """Always 200 : names what is configured and what is missing (never any value)."""
        from ..data.store import lakebase_env_status
        from ..services.context import get_context
        out = {"status": "ok", "version": __version__, "data_source": settings.data_source,
               "database": "lakebase" if settings.uses_lakebase else ("url" if settings.db_url else "sqlite"),
               "schema": settings.db_schema,
               "lakebase_env": lakebase_env_status(), "frontend_built": (Path(settings.static_dir) / "index.html").exists()}
        try:
            ctx = get_context()
            with ctx.session() as session:
                from sqlalchemy import text
                session.execute(text("SELECT 1"))
            out["database_status"] = "ok"
            out["source"] = ctx.source.describe()
            out["reference_rows"] = int(len(ctx.table("ref_articles")))
        except Exception as exc:  # the diagnostic must answer even when the database is down
            out["status"] = "degraded"
            out["database_status"] = f"{type(exc).__name__}: {exc}"
        return out

    for r in (reference.router, mrp.router, entries.router, pdp.router, files.router):
        app.include_router(r)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        """API answers depend on the planner's latest writes: never cached by the browser or a proxy."""
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DatasetError)
    async def dataset_error(request: Request, exc: DatasetError):
        """Data refused by the engine (several stock initialisation dates…): a 422 with the reason."""
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # pragma: no cover - defensive
        logging.getLogger("appro").exception("Unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"Erreur interne : {type(exc).__name__}: {exc}"})

    static = Path(settings.static_dir)
    if static.exists() and (static / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            candidate = static / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def root():
            return {"message": "Ma Routine Appro – API : frontend non construit (npm run build dans client/)", "docs": "/api/docs"}
    return app


app = create_app()

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("appro.api.main:app", host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", 8000)))
