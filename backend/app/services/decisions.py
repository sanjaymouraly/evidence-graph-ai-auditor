"""Decision case lifecycle: creation, evidence attachment, evaluation orchestration."""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import DecisionCase, DecisionDocument, Document, Entity, User, Workspace
from app.services.audit import append_event
from app.services.entities import entity_key
from app.services.gate import evaluate_case, policy_for
from app.services.graph import sync_decision_evidence_edges
from app.services.normalize import normalize_date

log = logging.getLogger(__name__)
DECISION_TYPES = {"procurement", "engineering_change", "project_approval", "policy_compliance"}
STATUS_LABELS = {
    "DRAFT": "Draft", "EVIDENCE_INCOMPLETE": "Evidence incomplete", "CONTRADICTED": "Contradicted",
    "REVIEW_REQUIRED": "Review required", "SUFFICIENTLY_SUPPORTED": "Sufficiently supported",
    "APPROVED": "Approved", "REJECTED": "Rejected",
}


def default_criteria(decision_type: str) -> list[dict]:
    base = [
        {"code": "price", "label": "Price", "weight": 0.35, "direction": "minimize", "kind": "derived"},
        {"code": "delivery", "label": "Delivery lead time", "weight": 0.20, "direction": "minimize", "kind": "derived"},
        {"code": "compliance", "label": "Certification & compliance", "weight": 0.25, "direction": "maximize", "kind": "derived"},
        {"code": "risk", "label": "Assessed risk", "weight": 0.10, "direction": "minimize", "kind": "derived"},
        {"code": "warranty", "label": "Warranty", "weight": 0.10, "direction": "maximize", "kind": "derived"},
    ]
    if decision_type == "engineering_change":
        base[0] = {"code": "price", "label": "Change cost", "weight": 0.25, "direction": "minimize", "kind": "derived"}
        base[1] = {"code": "delivery", "label": "Implementation lead time", "weight": 0.20, "direction": "minimize", "kind": "derived"}
        base.append({"code": "technical_fit", "label": "Technical fit (spec coverage)", "weight": 0.15,
                     "direction": "maximize", "kind": "derived"})
    return base


def _validate_type(decision_type: str) -> None:
    if decision_type not in DECISION_TYPES:
        raise ValueError(f"Unsupported decision type '{decision_type}'. Choose one of {sorted(DECISION_TYPES)}")


def _resolve_options(db: Session, workspace_id: str, options: list) -> list[dict]:
    out = []
    entities = {e.id: e for e in db.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars()}
    for opt in options or []:
        if isinstance(opt, str):
            opt = {"name": opt}
        if not isinstance(opt, dict) or not (opt.get("name") or opt.get("entity_id")):
            continue
        entity = entities.get(opt.get("entity_id"))
        if entity is None and opt.get("name"):
            key = entity_key(opt["name"])
            entity = next((e for e in entities.values() if entity_key(e.canonical_name) == key), None)
        out.append({
            "name": entity.canonical_name if entity else str(opt.get("name", "")).strip()[:200],
            "entity_id": entity.id if entity else None,
            "manual_scores": opt.get("manual_scores") or {},
        })
    return out


def create_case(db: Session, user: User, *, title: str, decision_type: str, description: str = "",
                options: list | None = None, criteria: list | None = None, as_of_date: str | None = None,
                due_date: str | None = None, requirements: list | None = None,
                document_ids: list[str] | None = None) -> DecisionCase:
    _validate_type(decision_type)
    if not title or len(title.strip()) < 3:
        raise ValueError("Decision title is required (minimum 3 characters)")
    workspace = db.get(Workspace, user.workspace_id)
    case = DecisionCase(
        workspace_id=user.workspace_id, title=title.strip()[:400], decision_type=decision_type,
        description=description or "", status="DRAFT", options=_resolve_options(db, user.workspace_id, options or []),
        criteria=criteria or default_criteria(decision_type),
        requirements=requirements or [],
        as_of_date=normalize_date(as_of_date)[0] if as_of_date else date.today(),
        due_date=normalize_date(due_date)[0] if due_date else None,
        policy_snapshot=policy_for(workspace), created_by=user.id, documents=[],
    )
    db.add(case)
    db.flush()
    attached = []
    for doc_id in document_ids or []:
        doc = db.get(Document, doc_id)
        if not doc or doc.workspace_id != user.workspace_id:
            raise ValueError(f"Document {doc_id} not found in workspace")
        db.add(DecisionDocument(decision_id=case.id, document_id=doc.id))
        attached.append(doc.id)
    case.documents = attached
    db.flush()
    sync_decision_evidence_edges(db, case)
    append_event(db, user.workspace_id, "decision.created", "DecisionCase", case.id, {
        "title": case.title, "decision_type": decision_type, "documents": attached,
        "options": case.options, "as_of_date": case.as_of_date.isoformat() if case.as_of_date else None,
    }, actor=user, commit=False)
    db.commit()
    return case


def update_case(db: Session, user: User, case: DecisionCase, changes: dict) -> DecisionCase:
    if case.workspace_id != user.workspace_id:
        raise ValueError("Decision not found")
    if case.status == "APPROVED" and user.role not in {"admin"}:
        editable = {"description"}
    else:
        editable = {"title", "description", "options", "criteria", "as_of_date", "due_date", "requirements",
                    "selected_option"}
    changed = {}
    for field, value in changes.items():
        if field not in editable or value is None:
            continue
        if field == "options":
            value = _resolve_options(db, user.workspace_id, value)
        elif field == "as_of_date":
            d = normalize_date(value)[0] if isinstance(value, str) else value
            value = d
        elif field == "due_date":
            value = normalize_date(value)[0] if isinstance(value, str) else value
        if getattr(case, field) != value:
            setattr(case, field, value)
            changed[field] = value
    if changed:
        append_event(db, user.workspace_id, "decision.updated", "DecisionCase", case.id,
                     {"changed_fields": sorted(changed.keys())}, actor=user, commit=False)
        db.commit()
        if case.gate_evaluated_at:
            evaluate_case(db, case, actor=user, reason="decision updated")
    return case


def attach_documents(db: Session, user: User, case: DecisionCase, document_ids: list[str]) -> dict:
    if case.status in {"APPROVED", "REJECTED"}:
        raise ValueError("Approved or rejected decisions are immutable; create a new review or reopen the case")
    for doc_id in document_ids:
        doc = db.get(Document, doc_id)
        if not doc or doc.workspace_id != user.workspace_id:
            raise ValueError(f"Document {doc_id} not found")
        exists = db.execute(select(DecisionDocument).where(
            DecisionDocument.decision_id == case.id, DecisionDocument.document_id == doc.id)).scalar_one_or_none()
        if not exists:
            db.add(DecisionDocument(decision_id=case.id, document_id=doc.id))
    db.flush()
    case.documents = [r.document_id for r in db.execute(
        select(DecisionDocument).where(DecisionDocument.decision_id == case.id)).scalars()]
    sync_decision_evidence_edges(db, case)
    append_event(db, user.workspace_id, "decision.evidence_attached", "DecisionCase", case.id,
                 {"document_ids": document_ids}, actor=user, commit=False)
    db.commit()
    result = evaluate_case(db, case, actor=user, reason="evidence attached") if case.gate_evaluated_at else None
    return {"documents": case.documents, "gate_status": result["status"] if result else case.gate_status}


def detach_document(db: Session, user: User, case: DecisionCase, document_id: str) -> dict:
    if case.status in {"APPROVED", "REJECTED"}:
        raise ValueError("Approved or rejected decisions are immutable; reopen or create a new review")
    db.execute(delete(DecisionDocument).where(DecisionDocument.decision_id == case.id,
                                              DecisionDocument.document_id == document_id))
    case.documents = [r.document_id for r in db.execute(
        select(DecisionDocument).where(DecisionDocument.decision_id == case.id)).scalars()]
    sync_decision_evidence_edges(db, case)
    append_event(db, user.workspace_id, "decision.evidence_detached", "DecisionCase", case.id,
                 {"document_id": document_id}, actor=user, commit=False)
    db.commit()
    result = evaluate_case(db, case, actor=user, reason="evidence detached") if case.gate_evaluated_at else None
    return {"documents": case.documents, "gate_status": result["status"] if result else case.gate_status}
