"""Decision cases, deterministic gate, reviews, analyses, reports and Q&A."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_decision_or_404, paged, pagination
from app.api.schemas import (AnalysisRequest, AskRequest, CommentRequest, DecisionCreate,
                             DecisionUpdate, DocumentAttach, EvaluateRequest, ExceptionRequest,
                             ReportRequest)
from app.core.security import require_permission
from app.db.models import (AIUsage, Analysis, Approval, AuditEvent, DecisionCase, GateCheck, ReportRecord, Snapshot, User)
from app.db.session import get_db
from app.services import decisions as decision_service
from app.services import gate as gate_service
from app.services.analysis import grounded_answer, run_analysis, serialize_analysis
from app.services.reports import create_snapshot, generate_report, verify_snapshot
from app.services.serializers import decision_detail, decision_summary

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.post("", status_code=201)
def create_decision(payload: DecisionCreate,
                    user: User = Depends(require_permission("decision:write")),
                    db: Session = Depends(get_db)) -> dict:
    try:
        case = decision_service.create_case(
            db, user, title=payload.title, decision_type=payload.decision_type,
            description=payload.description,
            options=[o.model_dump() if hasattr(o, "model_dump") else o for o in payload.options],
            criteria=[c.model_dump() for c in payload.criteria] if payload.criteria else None,
            as_of_date=payload.as_of_date, due_date=payload.due_date,
            requirements=payload.requirements, document_ids=payload.document_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return decision_detail(case, db)


@router.get("")
def list_decisions(status: str | None = None, decision_type: str | None = None,
                   q: str | None = None, pg=Depends(pagination),
                   user: User = Depends(require_permission("decision:read")),
                   db: Session = Depends(get_db)) -> dict:
    stmt = select(DecisionCase).where(DecisionCase.workspace_id == user.workspace_id)
    if status:
        stmt = stmt.where(DecisionCase.status == status)
    if decision_type:
        stmt = stmt.where(DecisionCase.decision_type == decision_type)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(DecisionCase.title).like(like)
                          | func.lower(DecisionCase.description).like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(DecisionCase.updated_at.desc())
                      .offset(pg.offset).limit(pg.page_size)).scalars().all()
    return paged([decision_summary(c, db) for c in rows], total, pg)


@router.get("/types")
def decision_types(user: User = Depends(require_permission("decision:read"))) -> dict:
    return {
        "types": [
            {"code": "procurement", "label": "Supplier selection / procurement"},
            {"code": "engineering_change", "label": "Engineering change approval"},
            {"code": "project_approval", "label": "Project risk & milestone approval"},
            {"code": "policy_compliance", "label": "Internal policy compliance review"},
        ],
        "states": ["DRAFT", "EVIDENCE_INCOMPLETE", "CONTRADICTED", "REVIEW_REQUIRED",
                   "SUFFICIENTLY_SUPPORTED", "APPROVED", "REJECTED"],
    }


@router.get("/{decision_id}")
def get_decision(decision_id: str, user: User = Depends(require_permission("decision:read")),
                 db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    payload = decision_detail(case, db)
    checks = db.execute(select(GateCheck).where(GateCheck.decision_id == case.id)
                        .order_by(GateCheck.evaluated_at)).scalars().all()
    payload["gate_checks_persisted"] = [{
        "code": c.code, "label": c.label, "status": c.status, "blocking": c.blocking,
        "detail": c.detail, "refs": c.refs, "gate_version": c.gate_version,
        "evaluated_at": c.evaluated_at.isoformat() if c.evaluated_at else None,
    } for c in checks]
    payload["approvals"] = _approvals(db, case.id, user)
    return payload


def _approvals(db: Session, decision_id: str, user: User) -> list[dict]:
    rows = db.execute(select(Approval, User).join(User, Approval.user_id == User.id)
                      .where(Approval.decision_id == decision_id)
                      .order_by(Approval.created_at)).all()
    return [{
        "id": a.id, "action": a.action, "comment": a.comment, "user": u.name, "email": u.email,
        "role": u.role, "gate_snapshot_hash": a.gate_snapshot_hash,
        "signature_hash": a.signature_hash,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a, u in rows]


@router.patch("/{decision_id}")
def update_decision(decision_id: str, payload: DecisionUpdate,
                    user: User = Depends(require_permission("decision:write")),
                    db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    changes = payload.model_dump(exclude_unset=True)
    changes.pop("status", None)
    changes.pop("subject_entity_id", None)
    if "options" in changes and changes["options"] is not None:
        changes["options"] = [o if isinstance(o, str) else o for o in changes["options"]]
    if "criteria" in changes and changes["criteria"] is not None:
        changes["criteria"] = [c if isinstance(c, dict) else c.model_dump() for c in changes["criteria"]]
    try:
        case = decision_service.update_case(db, user, case, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return decision_detail(case, db)


@router.post("/{decision_id}/documents")
def attach_documents(decision_id: str, payload: DocumentAttach,
                     user: User = Depends(require_permission("decision:write")),
                     db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    try:
        return decision_service.attach_documents(db, user, case, payload.document_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{decision_id}/documents/{document_id}")
def detach_document(decision_id: str, document_id: str,
                    user: User = Depends(require_permission("decision:write")),
                    db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    try:
        return decision_service.detach_document(db, user, case, document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{decision_id}/evaluate")
def evaluate(decision_id: str, payload: EvaluateRequest | None = None,
             user: User = Depends(require_permission("decision:submit")),
             db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    result = gate_service.evaluate_case(db, case, actor=user,
                                        reason=(payload.reason if payload else "manual evaluation"))
    return {"gate": result, "decision": decision_summary(case, db)}


@router.get("/{decision_id}/checklist")
def checklist(decision_id: str, user: User = Depends(require_permission("decision:read")),
              db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    gate_result = case.gate_result or {}
    if not gate_result:
        result = gate_service.evaluate_case(db, case, persist=True, reason="checklist initialised")
        gate_result = result
        db.refresh(case)
    return {
        "decision_id": case.id, "status": case.status, "gate_status": case.gate_status,
        "gate_version": gate_result.get("gate_version"), "policy_version": gate_result.get("policy_version"),
        "as_of": gate_result.get("as_of"), "evaluated_at": gate_result.get("evaluated_at"),
        "checks": gate_result.get("checks", []),
        "requirements": gate_result.get("requirements", []),
        "blocking_failures": gate_result.get("blocking_failures", []),
        "warnings": gate_result.get("warnings", []),
        "summary": gate_result.get("summary", {}),
        "exception": {"recorded": case.exception_recorded, "reason": case.exception_reason} if case.exception_recorded else None,
        "eligible_for_approval": gate_result.get("status") == "SUFFICIENTLY_SUPPORTED",
        "result_hash": gate_result.get("result_hash"),
    }


@router.get("/{decision_id}/mcda")
def mcda(decision_id: str, user: User = Depends(require_permission("decision:read")),
         db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    gate_result = case.gate_result or {}
    if gate_result.get("mcda"):
        return {"decision_id": case.id, "mcda": gate_result["mcda"], "as_of": gate_result.get("as_of")}
    result = gate_service.evaluate_case(db, case, persist=True, reason="mcda requested")
    return {"decision_id": case.id, "mcda": result["mcda"], "as_of": result["as_of"]}


@router.post("/{decision_id}/attest")
def attest(decision_id: str, payload: CommentRequest,
           user: User = Depends(require_permission("decision:approve")),
           db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    try:
        return gate_service.create_attestation(db, case, user, payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{decision_id}/approve")
def approve(decision_id: str, payload: CommentRequest,
            user: User = Depends(require_permission("decision:approve")),
            db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    try:
        result = gate_service.final_approval(db, case, user, payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**result, "decision": decision_summary(case, db)}


@router.post("/{decision_id}/reject")
def reject(decision_id: str, payload: CommentRequest,
           user: User = Depends(require_permission("decision:approve")),
           db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    if not payload.comment.strip():
        raise HTTPException(status_code=400, detail="A rejection comment is required")
    try:
        return gate_service.record_rejection(db, case, user, payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{decision_id}/request-changes")
def request_changes(decision_id: str, payload: CommentRequest,
                    user: User = Depends(require_permission("decision:approve")),
                    db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    try:
        return gate_service.request_changes(db, case, user, payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{decision_id}/exception")
def record_exception(decision_id: str, payload: ExceptionRequest,
                     user: User = Depends(require_permission("decision:approve")),
                     db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    try:
        return gate_service.record_exception(db, case, user, payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=403 if "administrators" in str(exc) else 400, detail=str(exc)) from exc


@router.post("/{decision_id}/reopen")
def reopen(decision_id: str, user: User = Depends(require_permission("decision:write")),
           db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    try:
        return gate_service.reopen_case(db, case, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{decision_id}/audit")
def decision_audit(decision_id: str, user: User = Depends(require_permission("audit:read")),
                   db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    rows = db.execute(select(AuditEvent).where(
        AuditEvent.workspace_id == user.workspace_id,
        (AuditEvent.object_id == case.id) | (AuditEvent.object_type == "DecisionCase")).order_by(
        AuditEvent.created_at.desc()).limit(500)).scalars().all()
    related = []
    for e in rows:
        if e.object_id == case.id or (e.payload or {}).get("decision_id") == case.id:
            related.append({"id": e.id, "action": e.action, "actor": e.actor_label,
                            "payload": e.payload, "event_hash": e.event_hash,
                            "prev_hash": e.prev_hash,
                            "created_at": e.created_at.isoformat() if e.created_at else None})
    payload = decision_detail(case, db)
    return {"decision": decision_summary(case, db), "events": related,
            "approvals": _approvals(db, case.id, user),
            "gate_evaluated_at": payload.get("gate_evaluated_at")}


@router.get("/{decision_id}/analysis")
def list_analysis(decision_id: str, user: User = Depends(require_permission("decision:read")),
                  db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    rows = db.execute(select(Analysis).where(Analysis.decision_id == case.id)
                      .order_by(Analysis.created_at.desc())).scalars().all()
    return {"items": [serialize_analysis(a) for a in rows], "total": len(rows)}


@router.post("/{decision_id}/analysis")
def run_decision_analysis(decision_id: str, payload: AnalysisRequest | None = None,
                          user: User = Depends(require_permission("analysis:run")),
                          db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    analysis = run_analysis(db, user, case, use_llm=(payload.use_llm if payload else True))
    gate_service.evaluate_case(db, case, actor=None, persist=True, reason="analysis generated")
    return serialize_analysis(analysis)


@router.post("/{decision_id}/ask")
def ask_about_decision(decision_id: str, payload: AskRequest,
                       user: User = Depends(require_permission("qa:ask")),
                       db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    return grounded_answer(db, user, payload.question, decision_id=case.id,
                           top_k=payload.top_k, use_llm=payload.use_llm)


@router.get("/{decision_id}/reports")
def decision_reports(decision_id: str, user: User = Depends(require_permission("report:read")),
                     db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    rows = db.execute(select(ReportRecord).where(ReportRecord.decision_id == case.id)
                      .order_by(ReportRecord.created_at.desc())).scalars().all()
    return {"items": [_report_dict(r) for r in rows], "total": len(rows)}


def _report_dict(r: ReportRecord) -> dict:
    return {"id": r.id, "decision_id": r.decision_id, "snapshot_id": r.snapshot_id, "kind": r.kind,
            "filename": r.filename, "sha256": r.sha256, "size_bytes": None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "download_url": f"/api/reports/{r.id}/download"}


@router.post("/{decision_id}/reports", status_code=201)
def create_report(decision_id: str, payload: ReportRequest,
                  user: User = Depends(require_permission("report:generate")),
                  db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    if payload.snapshot_id:
        snapshot = db.get(Snapshot, payload.snapshot_id)
        if not snapshot or snapshot.workspace_id != user.workspace_id or snapshot.decision_id != case.id:
            raise HTTPException(status_code=404, detail="Snapshot not found for this decision")
    try:
        record = generate_report(db, case, payload.kind, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _report_dict(record)


@router.get("/{decision_id}/snapshots")
def snapshots(decision_id: str, user: User = Depends(require_permission("report:read")),
              db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    rows = db.execute(select(Snapshot).where(Snapshot.decision_id == case.id)
                      .order_by(Snapshot.created_at.desc())).scalars().all()
    return {"items": [{"id": s.id, "snapshot_hash": s.snapshot_hash,
                       "report_spec_version": s.report_spec_version,
                       "created_at": s.created_at.isoformat() if s.created_at else None,
                       "verification": verify_snapshot(s)} for s in rows], "total": len(rows)}


@router.post("/{decision_id}/snapshots")
def make_snapshot(decision_id: str, user: User = Depends(require_permission("report:generate")),
                  db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    snapshot = create_snapshot(db, case, user)
    return {"id": snapshot.id, "snapshot_hash": snapshot.snapshot_hash,
            "verification": verify_snapshot(snapshot),
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None}


@router.get("/{decision_id}/usage")
def decision_usage(decision_id: str, user: User = Depends(require_permission("analysis:run")),
                   db: Session = Depends(get_db)) -> dict:
    case = get_decision_or_404(db, user, decision_id)
    ids = [a.usage_id for a in db.execute(select(Analysis).where(Analysis.decision_id == case.id)).scalars()
           if a.usage_id]
    rows = db.execute(select(AIUsage).where(AIUsage.id.in_(ids))).scalars().all() if ids else []
    return {"calls": len(rows),
            "prompt_tokens": sum(r.prompt_tokens or 0 for r in rows),
            "completion_tokens": sum(r.completion_tokens or 0 for r in rows),
            "estimated_cost_usd": round(sum(r.estimated_cost_usd or 0 for r in rows), 6),
            "items": [{"model": r.model, "operation": r.operation, "prompt_tokens": r.prompt_tokens,
                       "completion_tokens": r.completion_tokens,
                       "estimated_cost_usd": r.estimated_cost_usd, "success": r.success} for r in rows]}
