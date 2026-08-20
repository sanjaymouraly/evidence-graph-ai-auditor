"""Report center, downloads and snapshot verification."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import paged, pagination
from app.core.security import require_permission
from app.db.models import DecisionCase, ReportRecord, Snapshot, User
from app.db.session import get_db
from app.services.reports import regenerate_report, verify_snapshot

router = APIRouter(prefix="/api", tags=["reports"])


def _report_dict(r: ReportRecord) -> dict:
    return {"id": r.id, "decision_id": r.decision_id, "snapshot_id": r.snapshot_id, "kind": r.kind,
            "filename": r.filename, "sha256": r.sha256,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "download_url": f"/api/reports/{r.id}/download"}


@router.get("/reports")
def list_reports(kind: str | None = None, pg=Depends(pagination),
                 user: User = Depends(require_permission("report:read")),
                 db: Session = Depends(get_db)) -> dict:
    stmt = select(ReportRecord).where(ReportRecord.workspace_id == user.workspace_id)
    if kind:
        stmt = stmt.where(ReportRecord.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(ReportRecord.created_at.desc())
                      .offset(pg.offset).limit(pg.page_size)).scalars().all()
    titles = {c.id: c.title for c in db.execute(
        select(DecisionCase).where(DecisionCase.id.in_([r.decision_id for r in rows]))).scalars()}
    items = [{**_report_dict(r), "decision_title": titles.get(r.decision_id, "")} for r in rows]
    return paged(items, total, pg)


@router.get("/reports/{report_id}")
def get_report(report_id: str, user: User = Depends(require_permission("report:read")),
               db: Session = Depends(get_db)) -> dict:
    record = db.get(ReportRecord, report_id)
    if not record or record.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Report not found")
    snapshot = db.get(Snapshot, record.snapshot_id)
    payload = _report_dict(record)
    payload["snapshot_verification"] = verify_snapshot(snapshot) if snapshot else {"hash_match": False}
    case = db.get(DecisionCase, record.decision_id)
    payload["decision"] = {"id": case.id, "title": case.title, "status": case.status,
                           "gate_status": case.gate_status} if case else None
    return payload


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, user: User = Depends(require_permission("report:read")),
                    db: Session = Depends(get_db)):
    record = db.get(ReportRecord, report_id)
    if not record or record.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Report not found")
    path = Path(record.path or "")
    if not record.path or not path.exists():
        raise HTTPException(status_code=410, detail="Report file is missing from storage")
    media = {"pdf": "application/pdf", "json": "application/json", "manifest": "application/json",
             "csv": "text/csv"}.get(record.kind, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=record.filename)


@router.post("/reports/{report_id}/regenerate", status_code=201)
def regenerate(report_id: str, user: User = Depends(require_permission("report:generate")),
               db: Session = Depends(get_db)) -> dict:
    record = db.get(ReportRecord, report_id)
    if not record or record.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        new_record, verification = regenerate_report(db, record, user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**_report_dict(new_record), "snapshot_verification": verification}


@router.get("/snapshots/{snapshot_id}/verify")
def verify(snapshot_id: str, user: User = Depends(require_permission("report:read")),
           db: Session = Depends(get_db)) -> dict:
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot or snapshot.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    result = verify_snapshot(snapshot)
    return {"snapshot_id": snapshot.id, "decision_id": snapshot.decision_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "report_spec_version": snapshot.report_spec_version,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            **result}


@router.get("/snapshots/{snapshot_id}/download", response_class=PlainTextResponse)
def snapshot_json(snapshot_id: str, user: User = Depends(require_permission("report:read")),
                  db: Session = Depends(get_db)) -> PlainTextResponse:
    import json
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot or snapshot.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return PlainTextResponse(json.dumps(snapshot.payload, indent=2, default=str),
                             media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename="snapshot-{snapshot.id}.json"'})
