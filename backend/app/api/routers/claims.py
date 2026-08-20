"""Claim explorer, review actions and claim provenance trace."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import paged, pagination
from app.api.schemas import ClaimReview
from app.core.security import require_permission
from app.db.models import (Claim, Contradiction, DecisionDocument, Document,
                           DocumentVersion, Edge, SourceSpan, User)
from app.db.session import get_db
from app.services.audit import append_event
from app.services.claims import citation_resolves
from app.services.graph import trace_claim
from app.services.serializers import claim_dict

router = APIRouter(prefix="/api/claims", tags=["claims"])

VERIFICATION_STATES = ("verified", "uncertain", "disputed", "unsupported", "rejected")


@router.get("/facets")
def facets(user: User = Depends(require_permission("claim:read")),
           db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(Claim.predicate, Claim.verification_status, func.count(Claim.id))
        .where(Claim.workspace_id == user.workspace_id)
        .group_by(Claim.predicate, Claim.verification_status)
    ).all()
    predicates: dict[str, int] = {}
    statuses: dict[str, int] = {s: 0 for s in VERIFICATION_STATES}
    matrix: dict[str, dict[str, int]] = {}
    for predicate, status, count in rows:
        predicates[predicate] = predicates.get(predicate, 0) + count
        statuses[status] = statuses.get(status, 0) + count
        matrix.setdefault(predicate, {})[status] = count
    return {"predicates": predicates, "statuses": statuses, "matrix": matrix}


@router.get("")
def list_claims(q: str | None = None, predicate: str | None = None,
                verification_status: str | None = None, document_id: str | None = None,
                decision_id: str | None = None, has_contradiction: bool | None = None,
                include_duplicates: bool = False,
                pg=Depends(pagination),
                user: User = Depends(require_permission("claim:read")),
                db: Session = Depends(get_db)) -> dict:
    stmt = (select(Claim, DocumentVersion, Document, SourceSpan)
            .join(DocumentVersion, Claim.version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .outerjoin(SourceSpan, Claim.span_id == SourceSpan.id)
            .where(Claim.workspace_id == user.workspace_id))
    if not include_duplicates:
        stmt = stmt.where(Claim.duplicate_of_id.is_(None))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Claim.subject_text).like(like),
                              func.lower(Claim.object_text).like(like),
                              func.lower(Claim.quoted_span).like(like),
                              func.lower(Claim.predicate).like(like)))
    if predicate:
        stmt = stmt.where(Claim.predicate == predicate)
    if verification_status:
        stmt = stmt.where(Claim.verification_status == verification_status)
    if document_id:
        stmt = stmt.where(Document.id == document_id)
    if decision_id:
        doc_ids = select(DecisionDocument.document_id).where(DecisionDocument.decision_id == decision_id)
        stmt = stmt.where(Document.id.in_(doc_ids))
    if has_contradiction is not None:
        contra_ids = select(Contradiction.claim_a_id).where(
            Contradiction.workspace_id == user.workspace_id, Contradiction.state != "dismissed")
        contra_ids_b = select(Contradiction.claim_b_id).where(
            Contradiction.workspace_id == user.workspace_id, Contradiction.state != "dismissed")
        if has_contradiction:
            stmt = stmt.where(or_(Claim.id.in_(contra_ids), Claim.id.in_(contra_ids_b)))
        else:
            stmt = stmt.where(Claim.id.not_in(contra_ids), Claim.id.not_in(contra_ids_b))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Claim.created_at.desc(), Claim.id)
                      .offset(pg.offset).limit(pg.page_size)).all()
    items = []
    for claim, version, document, span in rows:
        item = claim_dict(claim, version, document, span)
        ok, reason = citation_resolves(claim, span)
        item["citation"] = {"resolves": ok, "reason": reason}
        items.append(item)
    return paged(items, total, pg)


@router.get("/export.csv", response_class=PlainTextResponse)
def export_claims(user: User = Depends(require_permission("claim:read")),
                  db: Session = Depends(get_db)) -> PlainTextResponse:
    from app.services.reports import export_claims_csv
    return PlainTextResponse(export_claims_csv(db, user.workspace_id).decode("utf-8"),
                             media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="claims.csv"'})


@router.get("/{claim_id}")
def get_claim(claim_id: str, user: User = Depends(require_permission("claim:read")),
              db: Session = Depends(get_db)) -> dict:
    claim = db.get(Claim, claim_id)
    if not claim or claim.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Claim not found")
    version = db.get(DocumentVersion, claim.version_id)
    document = db.get(Document, version.document_id) if version else None
    span = db.get(SourceSpan, claim.span_id) if claim.span_id else None
    payload = claim_dict(claim, version, document, span)
    ok, reason = citation_resolves(claim, span)
    payload["citation"] = {"resolves": ok, "reason": reason}
    contradicting = []
    for contra in db.execute(select(Contradiction).where(
            Contradiction.workspace_id == user.workspace_id,
            or_(Contradiction.claim_a_id == claim.id, Contradiction.claim_b_id == claim.id))).scalars():
        other_id = contra.claim_b_id if contra.claim_a_id == claim.id else contra.claim_a_id
        other = db.get(Claim, other_id)
        other_doc = None
        if other:
            ov = db.get(DocumentVersion, other.version_id)
            other_doc = db.get(Document, ov.document_id) if ov else None
        contradicting.append({
            "contradiction_id": contra.id, "state": contra.state, "kind": contra.kind,
            "severity": contra.severity, "blocking": contra.blocking, "reasoning": contra.reasoning,
            "other_claim": {"id": other.id, "predicate": other.predicate, "object": other.object_text,
                            "document": other_doc.title if other_doc else None,
                            "document_id": other_doc.id if other_doc else None,
                            "version_id": other.version_id} if other else None,
        })
    payload["contradictions"] = contradicting
    payload["edges"] = [{"relation": e.relation, "source_type": e.source_type, "source_id": e.source_id,
                         "target_type": e.target_type, "target_id": e.target_id,
                         "justification": e.justification}
                        for e in db.execute(select(Edge).where(
                            Edge.workspace_id == user.workspace_id,
                            or_((Edge.source_type == "Claim") & (Edge.source_id == claim.id),
                                (Edge.target_type == "Claim") & (Edge.target_id == claim.id)))).scalars()]
    return payload


@router.get("/{claim_id}/trace")
def claim_trace(claim_id: str, user: User = Depends(require_permission("claim:read")),
                db: Session = Depends(get_db)) -> dict:
    claim = db.get(Claim, claim_id)
    if not claim or claim.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Claim not found")
    trace = trace_claim(db, user.workspace_id, claim_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace unavailable")
    return trace


@router.post("/{claim_id}/review")
def review_claim(claim_id: str, payload: ClaimReview,
                 user: User = Depends(require_permission("claim:review")),
                 db: Session = Depends(get_db)) -> dict:
    claim = db.get(Claim, claim_id)
    if not claim or claim.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Claim not found")
    mapping = {
        "verify": ("verified", "confirmed"),
        "reject": ("rejected", "rejected"),
        "flag": ("disputed", "flagged"),
        "reset": ("uncertain", "pending"),
    }
    verification, review = mapping[payload.action]
    before = {"verification_status": claim.verification_status, "review_status": claim.review_status}
    claim.verification_status = verification
    claim.review_status = review
    claim.review_note = payload.note
    claim.reviewed_by = user.id
    from datetime import datetime, timezone
    claim.reviewed_at = datetime.now(timezone.utc)
    append_event(db, user.workspace_id, f"claim.review_{payload.action}", "Claim", claim.id,
                 {"before": before, "after": {"verification_status": verification, "review_status": review},
                  "note": payload.note}, actor=user, commit=False)
    db.commit()
    version = db.get(DocumentVersion, claim.version_id)
    document = db.get(Document, version.document_id) if version else None
    span = db.get(SourceSpan, claim.span_id) if claim.span_id else None
    return claim_dict(claim, version, document, span)
