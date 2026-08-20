"""Workspace overview, dashboard metrics and search."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, workspace_audit_tail
from app.core.security import require_permission
from app.db.models import (AIUsage, AuditEvent, Claim, Contradiction, DecisionCase,
                           Document, DocumentVersion, ProviderConfig, ReportRecord,
                           Snapshot, User, Workspace)
from app.db.session import get_db
from app.services.gate import GATE_VERSION, policy_for
from app.services.graph import graph_stats
from app.services.retrieval import hybrid_search
from app.services.serializers import decision_summary

router = APIRouter(prefix="/api", tags=["workspace"])


@router.get("/workspace")
def workspace_info(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = db.get(Workspace, user.workspace_id)
    policy = policy_for(ws)
    return {
        "id": ws.id, "name": ws.name, "slug": ws.slug, "settings": ws.settings,
        "policy": {
            "version": policy["version"],
            "gate_version": GATE_VERSION,
            "freshness_days": policy["freshness_days"],
            "requirements": policy["requirements"],
            "min_options": policy["min_options"],
        },
    }


@router.get("/dashboard")
def dashboard(user: User = Depends(require_permission("claim:read")),
              db: Session = Depends(get_db)) -> dict:
    ws_id = user.workspace_id
    today = date.today()

    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    doc_total = count(Document, Document.workspace_id == ws_id)
    version_total = count(DocumentVersion, DocumentVersion.workspace_id == ws_id)
    processed = count(DocumentVersion, DocumentVersion.workspace_id == ws_id,
                      DocumentVersion.status == "processed")
    failed = count(DocumentVersion, DocumentVersion.workspace_id == ws_id,
                   DocumentVersion.status == "failed")
    claims = db.execute(select(Claim).where(Claim.workspace_id == ws_id)).scalars().all()
    claim_count = len(claims)
    by_verification = Counter(c.verification_status for c in claims)
    contradictions = db.execute(select(Contradiction).where(Contradiction.workspace_id == ws_id)).scalars().all()
    cases = db.execute(select(DecisionCase).where(DecisionCase.workspace_id == ws_id)
                       .order_by(DecisionCase.updated_at.desc())).scalars().all()
    status_counts = Counter(c.status for c in cases)
    gate_counts = Counter(c.gate_status for c in cases if c.gate_status)
    stale_docs = db.execute(
        select(DocumentVersion).where(DocumentVersion.workspace_id == ws_id,
                                      DocumentVersion.valid_until.is_not(None),
                                      DocumentVersion.valid_until < today)
    ).scalars().all()
    expiring = db.execute(
        select(DocumentVersion).where(DocumentVersion.workspace_id == ws_id,
                                      DocumentVersion.valid_until.is_not(None),
                                      DocumentVersion.valid_until >= today,
                                      DocumentVersion.valid_until <= today + timedelta(days=60))
    ).scalars().all()
    usage = db.execute(select(AIUsage).where(AIUsage.workspace_id == ws_id)).scalars().all()
    review_pending = [c for c in cases if c.status in {"REVIEW_REQUIRED", "SUFFICIENTLY_SUPPORTED"}]
    q = db.execute(
        select(Claim, DocumentVersion, Document)
        .join(DocumentVersion, Claim.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(Claim.workspace_id == ws_id, Claim.duplicate_of_id.is_(None))
        .order_by(Claim.created_at.desc()).limit(5)
    ).all()
    recent_claims = [{
        "id": c.id, "predicate": c.predicate, "subject": c.subject_text,
        "object": c.object_text, "document": d.title, "document_id": d.id,
        "verification_status": c.verification_status,
    } for c, _v, d in q]
    coverage = {
        "verified": by_verification.get("verified", 0),
        "uncertain": by_verification.get("uncertain", 0),
        "disputed": by_verification.get("disputed", 0),
        "unsupported": by_verification.get("unsupported", 0),
        "rejected": by_verification.get("rejected", 0),
    }
    health_denominator = max(1, claim_count)
    evidence_health = round(100 * (coverage["verified"] + 0.5 * coverage["uncertain"]) / health_denominator, 1)
    return {
        "documents": {"total": doc_total, "versions": version_total, "processed": processed,
                      "failed": failed, "expired": len(stale_docs), "expiring_60d": len(expiring)},
        "claims": {"total": claim_count, "by_status": coverage,
                   "duplicates": sum(1 for c in claims if c.duplicate_of_id)},
        "contradictions": {
            "total": len(contradictions),
            "confirmed": sum(1 for c in contradictions if c.state == "confirmed"),
            "possible": sum(1 for c in contradictions if c.state == "possible"),
            "blocking": sum(1 for c in contradictions if c.blocking and c.state == "confirmed"),
        },
        "decisions": {"total": len(cases), "by_status": dict(status_counts),
                      "by_gate": dict(gate_counts), "awaiting_review": len(review_pending),
                      "approved": status_counts.get("APPROVED", 0)},
        "evidence_health_score": evidence_health,
        "recent_decisions": [decision_summary(c, db) for c in cases[:6]],
        "recent_claims": recent_claims,
        "audit_tail": workspace_audit_tail(db, ws_id, 8),
        "graph": graph_stats(db, ws_id),
        "ai_usage": {
            "calls": len(usage), "estimated_cost_usd": round(sum(u.estimated_cost_usd or 0 for u in usage), 6),
            "providers_configured": db.scalar(select(func.count()).select_from(ProviderConfig)
                                              .where(ProviderConfig.workspace_id == ws_id,
                                                     ProviderConfig.enabled.is_(True))) or 0,
            "offline_mode": not any(p.enabled and p.kind != "offline" for p in db.execute(
                select(ProviderConfig).where(ProviderConfig.workspace_id == ws_id)).scalars()),
        },
        "reports": db.scalar(select(func.count()).select_from(ReportRecord)
                             .where(ReportRecord.workspace_id == ws_id)) or 0,
        "snapshots": db.scalar(select(func.count()).select_from(Snapshot)
                               .where(Snapshot.workspace_id == ws_id)) or 0,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=300),
           decision_id: str | None = None,
           top_k: int = Query(default=12, ge=1, le=50),
           user: User = Depends(require_permission("claim:read")),
           db: Session = Depends(get_db)) -> dict:
    filters = {"decision_id": decision_id} if decision_id else {}
    result = hybrid_search(db, user.workspace_id, q, top_k=top_k, filters=filters)
    return result


@router.get("/audit/verify")
def verify_audit(user: User = Depends(require_permission("audit:read")),
                 db: Session = Depends(get_db)) -> dict:
    from app.services.audit import verify_chain
    return verify_chain(db, user.workspace_id)


@router.get("/audit", response_model=None)
def audit_list(page: int = Query(1, ge=1, le=10000), page_size: int = Query(50, ge=1, le=200),
               action: str | None = None, object_type: str | None = None,
               object_id: str | None = None,
               user: User = Depends(require_permission("audit:read")),
               db: Session = Depends(get_db)):
    stmt = select(AuditEvent).where(AuditEvent.workspace_id == user.workspace_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if object_type:
        stmt = stmt.where(AuditEvent.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditEvent.object_id == object_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {
        "items": [{"id": e.id, "action": e.action, "object_type": e.object_type,
                   "object_id": e.object_id, "actor": e.actor_label, "payload": e.payload,
                   "prev_hash": e.prev_hash, "event_hash": e.event_hash,
                   "correlation_id": e.correlation_id,
                   "created_at": e.created_at.isoformat() if e.created_at else None} for e in rows],
        "total": total, "page": page, "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/audit/export.csv", response_class=PlainTextResponse)
def audit_export(user: User = Depends(require_permission("audit:read")),
                 db: Session = Depends(get_db)) -> PlainTextResponse:
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["created_at", "action", "object_type", "object_id", "actor", "event_hash", "prev_hash"])
    rows = db.execute(select(AuditEvent).where(AuditEvent.workspace_id == user.workspace_id)
                      .order_by(AuditEvent.created_at)).scalars().all()
    for e in rows:
        writer.writerow([e.created_at.isoformat() if e.created_at else "", e.action, e.object_type,
                         e.object_id, e.actor_label, e.event_hash, e.prev_hash])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'})
