"""Health, readiness and build metadata endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Claim, DecisionCase, Document, Workspace
from app.db.session import get_db

router = APIRouter(prefix="/api/health", tags=["health"])
STARTED_AT = datetime.now(timezone.utc)


@router.get("")
def liveness() -> dict:
    return {"status": "ok", "service": "evidence-graph-ai-auditor",
            "time": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": round((datetime.now(timezone.utc) - STARTED_AT).total_seconds(), 1)}


@router.get("/ready")
def readiness(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - infrastructure failure path
        checks["database"] = f"error: {exc}"
    try:
        checks["workspaces"] = db.scalar(select(func.count()).select_from(Workspace)) or 0
        checks["documents"] = db.scalar(select(func.count()).select_from(Document)) or 0
        checks["claims"] = db.scalar(select(func.count()).select_from(Claim)) or 0
        checks["decisions"] = db.scalar(select(func.count()).select_from(DecisionCase)) or 0
    except Exception as exc:  # pragma: no cover
        checks["error"] = str(exc)
    ok = checks.get("database") == "ok"
    return {"status": "ready" if ok else "degraded", "environment": settings.environment,
            "schema_mode": "auto-create" if settings.auto_create_schema else "migrations",
            "checks": checks, "time": datetime.now(timezone.utc).isoformat()}


@router.get("/version")
def version() -> dict:
    return {"name": "Evidence-Graph AI Auditor", "version": "1.0.0", "api": "v1",
            "gate": "gate-1.2", "policy": "policy-2026.1", "report_spec": "report-1.0"}
