"""Benchmark evaluation endpoints."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BenchmarkRunRequest
from app.core.security import require_permission
from app.db.models import BenchmarkRun, User
from app.db.session import get_db
from app.services.benchmark import TARGETS, load_ground_truth, load_manifest, run_benchmark

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
_RUN_LOCK = threading.Lock()


def _run_dict(r: BenchmarkRun, include_metrics: bool = True) -> dict:
    """Serialise a stored run.

    Full runs return the archived payload so historical results render exactly like a fresh
    run. The list view stays small.
    """
    base = {"id": r.id, "dataset": r.dataset, "status": r.status,
            "duration_seconds": r.duration_seconds,
            "created_at": r.created_at.isoformat() if r.created_at else None}
    if not include_metrics:
        return base
    payload = {**(r.payload or {})}
    payload.update(base)
    payload["metrics"] = payload.get("metrics") or r.metrics or {}
    payload["confusion"] = payload.get("confusion") or r.confusion or {}
    payload.setdefault("errors", [])
    payload.setdefault("targets", {})
    payload.setdefault("details", {})
    return payload


@router.get("")
def list_runs(user: User = Depends(require_permission("benchmark:read")),
              db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(BenchmarkRun).where(BenchmarkRun.workspace_id == user.workspace_id)
                      .order_by(BenchmarkRun.created_at.desc())).scalars().all()
    return {"items": [_run_dict(r, include_metrics=False) for r in rows], "total": len(rows),
            "targets": TARGETS}


@router.get("/{run_id}")
def get_run(run_id: str, user: User = Depends(require_permission("benchmark:read")),
            db: Session = Depends(get_db)) -> dict:
    run = db.get(BenchmarkRun, run_id)
    if not run or run.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return _run_dict(run)


@router.post("/run", status_code=201)
def start_run(payload: BenchmarkRunRequest,
              user: User = Depends(require_permission("benchmark:run")),
              db: Session = Depends(get_db)) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A benchmark run is already in progress")
    try:
        result = run_benchmark(db, user, payload.dataset, include_llm=payload.include_llm,
                               limit=payload.limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _RUN_LOCK.release()
    return result


@router.get("/ground-truth/{dataset}")
def ground_truth(dataset: str, user: User = Depends(require_permission("benchmark:read"))) -> dict:
    if dataset not in {"dev", "heldout"}:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    return {"manifest": {k: v for k, v in load_manifest(dataset).items() if k != "documents"},
            "ground_truth": load_ground_truth(dataset)}
