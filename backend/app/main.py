"""FastAPI application entrypoint for the Evidence-Graph AI Auditor."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.api.health import router as health_router
from app.api.routers import (analysis, auth, benchmark, claims, contradictions, decisions, documents,
                             graph, providers, reports, workspace)
from app.config import get_settings
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db.models import Workspace
from app.db.session import SessionLocal, init_db
from app.services.providers import ProviderError

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("ega.startup")


@contextmanager
def _seed_lock():
    """Cross-process lock so a multi-worker start seeds exactly once.

    uvicorn runs every worker's lifespan, so two workers could both find an empty database
    and race to create the demo workspace; the loser hit a UNIQUE violation and logged a
    spurious failure. The lock is advisory and best-effort (flock is unavailable on
    Windows), with the unique constraint as the final guard.
    """
    handle = None
    try:
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        handle = open(Path(settings.data_dir) / ".seed.lock", "w")
        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        except (ImportError, OSError):  # pragma: no cover - platform dependent
            pass
    except OSError:  # pragma: no cover - read-only data dir
        handle = None
    try:
        yield
    finally:
        if handle is not None:
            handle.close()


def _workspace_count() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.count()).select_from(Workspace)) or 0
    finally:
        db.close()


def _ensure_seed() -> None:
    if not settings.seed_demo_data or settings.environment == "test":
        return
    with _seed_lock():
        if _workspace_count() > 0:
            return
        from sqlalchemy.exc import IntegrityError

        try:
            from app.seed import seed_demo
            result = seed_demo(force=False)
            log.info("auto-seeded demonstration workspace %s", result.get("workspace_slug"))
        except IntegrityError:
            # Another worker won the race between the count and the insert.
            log.info("workspace created by another process; skipping seed")
        except Exception:  # seeding must never prevent the API from starting
            log.exception("auto-seed failed; start with an empty workspace")


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = settings.startup_errors()
    if problems:
        for problem in problems:
            log.error("configuration error: %s", problem)
        raise RuntimeError("Invalid configuration: " + "; ".join(problems))
    for warning in settings.startup_warnings():
        log.warning("configuration: %s", warning)
    if settings.auto_create_schema:
        init_db()
    _ensure_seed()
    log.info("Evidence-Graph AI Auditor started (env=%s, db=%s)", settings.environment,
             settings.database_url.split("://")[0])
    yield
    log.info("shutdown complete")


app = FastAPI(
    title="Evidence-Graph AI Auditor",
    version="1.0.0",
    description=(
        "Deterministic, provenance-aware evidence auditing for high-stakes business decisions. "
        "LLMs may explain but never decide gate outcomes.\n\n"
        "Authenticate with `POST /api/auth/login`, then send `Authorization: Bearer <token>`."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)


class RateLimitMiddleware:
    """Small in-process sliding-window limiter (per client IP + auth/admin bucket)."""

    def __init__(self, app, limit_per_minute: int):
        self.app = app
        self.limit = limit_per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith("/api") or path.startswith("/api/health") or path.startswith("/api/docs"):
            await self.app(scope, receive, send)
            return
        client = (scope.get("client") or ("unknown",))[0]
        key = f"{client}:{path.split('/')[2] if len(path.split('/')) > 2 else 'root'}"
        now = time.monotonic()
        window = self.hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.limit:
            response = JSONResponse({"detail": "Rate limit exceeded. Retry shortly."}, status_code=429)
            await response(scope, receive, send)
            return
        window.append(now)
        await self.app(scope, receive, send)


app.add_middleware(RateLimitMiddleware, limit_per_minute=settings.api_rate_limit_per_minute)

for module_router in (health_router, auth.router, auth.users_router, workspace.router,
                      documents.router, claims.router, graph.router, contradictions.router,
                      decisions.router, analysis.router, providers.router, reports.router,
                      benchmark.router):
    app.include_router(module_router)


@app.exception_handler(ValueError)
async def _value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(ProviderError)
async def _provider_error_handler(_request: Request, exc: ProviderError):
    """Provider/SSRF validation failures are client errors, never 500s."""
    status_code = exc.status_code if exc.status_code and 400 <= exc.status_code < 500 else 400
    return JSONResponse({"detail": exc.message}, status_code=status_code)


@app.get("/api", include_in_schema=False)
def api_index() -> dict:
    return {"service": "evidence-graph-ai-auditor", "docs": "/api/docs",
            "health": "/api/health", "version": "/api/health/version"}


# Optional static frontend (Next.js export). Mounted only when a build exists.
frontend_dir = Path(settings.frontend_dir)
if settings.serve_frontend and frontend_dir.exists() and (frontend_dir / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets") \
        if (frontend_dir / "assets").exists() else None

    # Friendly aliases for the API documentation. Without these the SPA catch-all below
    # would answer /docs and /openapi.json with the UI shell, which looks like a broken API.
    @app.get("/docs", include_in_schema=False)
    def docs_alias():
        return RedirectResponse(url="/api/docs", status_code=307)

    @app.get("/redoc", include_in_schema=False)
    def redoc_alias():
        return RedirectResponse(url="/api/redoc", status_code=307)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_alias():
        return RedirectResponse(url="/api/openapi.json", status_code=307)

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = frontend_dir / full_path
        if full_path and candidate.is_dir() and (candidate / "index.html").exists():
            return FileResponse(candidate / "index.html")
        if full_path and candidate.is_file() and frontend_dir in candidate.resolve().parents:
            return FileResponse(candidate)
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        index = frontend_dir / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"detail": "Frontend build not found"}, status_code=404)
