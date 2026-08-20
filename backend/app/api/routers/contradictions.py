"""Contradiction review endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import paged, pagination
from app.api.schemas import ContradictionAction
from app.core.security import require_permission
from app.db.models import Claim, Contradiction, Document, DocumentVersion, SourceSpan, User
from app.db.session import get_db
from app.services.audit import append_event
from app.services.contradictions import detect_contradictions
from app.services.gate import reevaluate_decisions_for_document

router = APIRouter(prefix="/api/contradictions", tags=["contradictions"])

STATES = ("possible", "confirmed", "dismissed", "resolved")


def _claim_side(db: Session, claim_id: str) -> dict | None:
    claim = db.get(Claim, claim_id)
    if not claim:
        return None
    version = db.get(DocumentVersion, claim.version_id)
    document = db.get(Document, version.document_id) if version else None
    span = db.get(SourceSpan, claim.span_id) if claim.span_id else None
    return {
        "claim_id": claim.id, "predicate": claim.predicate, "subject": claim.subject_text,
        "object": claim.object_text, "quoted_span": claim.quoted_span,
        "verification_status": claim.verification_status,
        "document_id": document.id if document else None,
        "document_title": document.title if document else None,
        "doc_type": document.doc_type if document else None,
        "version_id": version.id if version else None,
        "version_number": version.version_number if version else None,
        "filename": version.filename if version else None,
        "locator": {"page": span.page, "sheet": span.sheet, "cell_range": span.cell_range,
                    "paragraph_index": span.paragraph_index, "bbox": span.bbox,
                    "char_start": span.char_start, "char_end": span.char_end} if span else None,
    }


def _contra_dict(c: Contradiction, db: Session) -> dict:
    return {
        "id": c.id, "kind": c.kind, "state": c.state, "severity": c.severity,
        "confidence": c.confidence, "blocking": c.blocking, "reasoning": c.reasoning,
        "detected_by": c.detected_by,
        "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "review_note": c.review_note,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "claim_a": _claim_side(db, c.claim_a_id), "claim_b": _claim_side(db, c.claim_b_id),
    }


@router.get("")
def list_contradictions(state: str | None = None, kind: str | None = None,
                        blocking: bool | None = None, decision_id: str | None = None,
                        pg=Depends(pagination),
                        user: User = Depends(require_permission("contradiction:read")),
                        db: Session = Depends(get_db)) -> dict:
    stmt = select(Contradiction).where(Contradiction.workspace_id == user.workspace_id)
    if state:
        stmt = stmt.where(Contradiction.state == state)
    if kind:
        stmt = stmt.where(Contradiction.kind == kind)
    if blocking is not None:
        stmt = stmt.where(Contradiction.blocking.is_(blocking))
    if decision_id:
        from app.db.models import DecisionDocument
        doc_ids = select(DecisionDocument.document_id).where(DecisionDocument.decision_id == decision_id)
        claim_ids = select(Claim.id).where(Claim.version_id.in_(
            select(DocumentVersion.id).where(DocumentVersion.document_id.in_(doc_ids))))
        stmt = stmt.where(or_(Contradiction.claim_a_id.in_(claim_ids),
                              Contradiction.claim_b_id.in_(claim_ids)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Contradiction.blocking.desc(), Contradiction.created_at.desc())
                      .offset(pg.offset).limit(pg.page_size)).scalars().all()
    return paged([_contra_dict(c, db) for c in rows], total, pg)


@router.get("/summary")
def summary(user: User = Depends(require_permission("contradiction:read")),
            db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Contradiction).where(Contradiction.workspace_id == user.workspace_id)).scalars().all()
    by_state: dict[str, int] = {s: 0 for s in STATES}
    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for c in rows:
        by_state[c.state] = by_state.get(c.state, 0) + 1
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
        by_severity[c.severity or "unknown"] = by_severity.get(c.severity or "unknown", 0) + 1
    return {"total": len(rows), "by_state": by_state, "by_kind": by_kind,
            "by_severity": by_severity,
            "blocking_open": sum(1 for c in rows if c.blocking and c.state in ("possible", "confirmed"))}


@router.post("/detect")
def detect(user: User = Depends(require_permission("contradiction:confirm")),
           db: Session = Depends(get_db)) -> dict:
    stats = detect_contradictions(db, user.workspace_id, commit=True)
    append_event(db, user.workspace_id, "contradiction.detected", "Workspace", user.workspace_id,
                 stats, actor=user, commit=True)
    return stats


@router.get("/{contradiction_id}")
def get_contradiction(contradiction_id: str,
                      user: User = Depends(require_permission("contradiction:read")),
                      db: Session = Depends(get_db)) -> dict:
    c = db.get(Contradiction, contradiction_id)
    if not c or c.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    return _contra_dict(c, db)


def _transition(db: Session, c: Contradiction, user: User, new_state: str, note: str,
                blocking: bool | None = None) -> dict:
    before = {"state": c.state, "blocking": c.blocking}
    c.state = new_state
    c.review_note = note
    c.reviewed_by = user.id
    c.reviewed_at = datetime.now(timezone.utc)
    if blocking is not None:
        c.blocking = blocking
    append_event(db, user.workspace_id, f"contradiction.{new_state}", "Contradiction", c.id,
                 {"before": before, "after": {"state": new_state, "blocking": c.blocking},
                  "note": note}, actor=user, commit=False)
    db.commit()
    # Recompute dependent decision gates so confirmed/dismissed changes take effect.
    affected = {db.get(DocumentVersion, db.get(Claim, cid).version_id).document_id
                for cid in (c.claim_a_id, c.claim_b_id) if db.get(Claim, cid)}
    for document_id in affected:
        reevaluate_decisions_for_document(db, user.workspace_id, document_id,
                                          reason=f"contradiction {c.id} set to {new_state}")
    return _contra_dict(c, db)


@router.post("/{contradiction_id}/confirm")
def confirm(contradiction_id: str, payload: ContradictionAction,
            user: User = Depends(require_permission("contradiction:confirm")),
            db: Session = Depends(get_db)) -> dict:
    c = db.get(Contradiction, contradiction_id)
    if not c or c.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    if c.state in {"confirmed", "dismissed"}:
        raise HTTPException(status_code=409, detail=f"Contradiction already {c.state}")
    return _transition(db, c, user, "confirmed", payload.note or "Reviewer confirmed contradiction",
                       blocking=True)


@router.post("/{contradiction_id}/dismiss")
def dismiss(contradiction_id: str, payload: ContradictionAction,
            user: User = Depends(require_permission("contradiction:confirm")),
            db: Session = Depends(get_db)) -> dict:
    c = db.get(Contradiction, contradiction_id)
    if not c or c.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    if not payload.note.strip():
        raise HTTPException(status_code=400, detail="Dismissal requires a reviewer justification")
    return _transition(db, c, user, "dismissed", payload.note, blocking=False)


@router.post("/{contradiction_id}/resolve")
def resolve(contradiction_id: str, payload: ContradictionAction,
            user: User = Depends(require_permission("contradiction:confirm")),
            db: Session = Depends(get_db)) -> dict:
    c = db.get(Contradiction, contradiction_id)
    if not c or c.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    return _transition(db, c, user, "resolved", payload.note or "Resolved", blocking=False)
