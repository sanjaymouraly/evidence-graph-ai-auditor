"""API/report serialization helpers."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Analysis, Approval, Claim, Contradiction, DecisionCase, DecisionDocument, Document, DocumentVersion,
    GateCheck, SourceSpan, User,
)
from app.services.predicates import P

LOCATOR_LABELS = {"page": "PDF page", "paragraph": "DOCX paragraph", "cell": "Spreadsheet cell",
                  "text": "Text span", "email_section": "Email section"}


def locator_dict(span: SourceSpan | None) -> dict:
    if span is None:
        return {}
    return {"type": span.locator_type, "label": LOCATOR_LABELS.get(span.locator_type, span.locator_type),
            "page": span.page, "bbox": span.bbox, "paragraph_index": span.paragraph_index,
            "sheet": span.sheet, "cell_range": span.cell_range, "char_start": span.char_start,
            "char_end": span.char_end, "section_label": span.section_label}


def span_locator_label(span: SourceSpan | None) -> str:
    """Human-readable locator for any span type.

    Every claim must be traceable to a place a reviewer can open. Falling back to an empty
    string is not acceptable: text, email and spreadsheet spans carry different fields, so
    each type gets an explicit rendering.
    """
    if span is None:
        return ""
    if span.page:
        return f"page {span.page}"
    if span.sheet:
        return f"{span.sheet}!{span.cell_range}" if span.cell_range else str(span.sheet)
    if span.paragraph_index is not None:
        return f"paragraph {span.paragraph_index}"
    if span.locator_type == "email_section" and span.section_label:
        return f"email {span.section_label}"
    if span.section_label:
        return str(span.section_label)
    if span.char_start is not None and span.char_end is not None:
        return f"chars {span.char_start}–{span.char_end}"
    return ""


def version_dict(v: DocumentVersion, include_meta: bool = False) -> dict:
    base = {
        "id": v.id, "document_id": v.document_id, "version_number": v.version_number,
        "filename": v.filename, "mime_type": v.mime_type, "content_hash": v.content_hash,
        "size_bytes": v.size_bytes, "status": v.status, "error": v.error, "warnings": v.warnings or [],
        "is_current": v.is_current, "superseded_by_version_id": v.superseded_by_version_id,
        "effective_date": v.effective_date.isoformat() if v.effective_date else None,
        "issued_date": v.issued_date.isoformat() if v.issued_date else None,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
        "source_author": v.source_author, "source_org": v.source_org, "primary_entity": v.primary_entity,
        "trust_level": v.trust_level, "injection_flags": v.injection_flags or [],
        "uploaded_by": v.uploaded_by, "processed_at": v.processed_at.isoformat() if v.processed_at else None,
        "created_at": v.created_at.isoformat(),
    }
    if include_meta:
        base["meta"] = v.meta or {}
    return base


def document_dict(d: Document, versions: list[DocumentVersion] | None = None) -> dict:
    versions = versions or []
    current = next((v for v in versions if v.is_current), None)
    return {
        "id": d.id, "doc_key": d.doc_key, "title": d.title, "doc_type": d.doc_type,
        "description": d.description, "created_at": d.created_at.isoformat(),
        "version_count": len(versions),
        "current_version": version_dict(current) if current else None,
        "versions": [version_dict(v) for v in versions],
    }


def claim_dict(claim: Claim, version: DocumentVersion | None, document: Document | None,
               span: SourceSpan | None) -> dict:
    return {
        "id": claim.id, "predicate": claim.predicate,
        "predicate_label": P[claim.predicate].label if claim.predicate in P else claim.predicate,
        "subject": claim.subject_text, "entity_id": claim.entity_id, "object": claim.object_text,
        "value_numeric": claim.value_numeric, "value_text": claim.value_text, "unit": claim.unit,
        "currency": claim.currency, "scope": claim.scope, "qualifiers": claim.qualifiers,
        "polarity": claim.polarity,
        "effective_date": claim.effective_date.isoformat() if claim.effective_date else None,
        "valid_until": claim.valid_until.isoformat() if claim.valid_until else None,
        "observed_date": claim.observed_date.isoformat() if claim.observed_date else None,
        "extraction_method": claim.extraction_method, "extraction_confidence": claim.extraction_confidence,
        "verification_status": claim.verification_status, "review_status": claim.review_status,
        "review_note": claim.review_note, "quoted_span": claim.quoted_span,
        "duplicate_of_id": claim.duplicate_of_id, "injection_flag": claim.injection_flag,
        "meta": claim.meta or {},
        "document": {"id": document.id, "title": document.title, "doc_type": document.doc_type} if document else None,
        "version": {"id": version.id, "version_number": version.version_number,
                    "filename": version.filename, "content_hash": version.content_hash,
                    "is_current": version.is_current} if version else None,
        "span": {"id": span.id, **locator_dict(span), "text": span.text,
                 "text_hash": span.text_hash} if span else None,
    }


def contradiction_dict(c: Contradiction, claims: dict[str, Claim],
                       versions: dict[str, DocumentVersion], documents: dict[str, Document],
                       spans: dict[str, SourceSpan]) -> dict:
    def side(cid: str):
        claim = claims.get(cid)
        if not claim:
            return {"claim_id": cid, "missing": True}
        v = versions.get(claim.version_id)
        d = documents.get(v.document_id) if v else None
        s = spans.get(claim.span_id) if claim.span_id else None
        return {"claim_id": claim.id, "subject": claim.subject_text, "predicate": claim.predicate,
                "value": claim.object_text, "status": claim.verification_status,
                "quoted_span": claim.quoted_span,
                "document": {"id": d.id, "title": d.title, "doc_type": d.doc_type} if d else None,
                "version": {"id": v.id, "version_number": v.version_number,
                            "is_current": v.is_current} if v else None,
                "locator": locator_dict(s), "effective_date": str(claim.effective_date or ""),
                "valid_until": str(claim.valid_until or "")}
    return {
        "id": c.id, "kind": c.kind, "state": c.state, "severity": c.severity,
        "confidence": c.confidence, "blocking": c.blocking, "reasoning": c.reasoning,
        "detected_by": c.detected_by, "review_note": c.review_note,
        "reviewed_by": c.reviewed_by, "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "created_at": c.created_at.isoformat(), "claim_a": side(c.claim_a_id), "claim_b": side(c.claim_b_id),
    }


def approval_dict(a: Approval, user: User | None) -> dict:
    return {"id": a.id, "action": a.action, "comment": a.comment,
            "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role} if user else None,
            "gate_snapshot_hash": a.gate_snapshot_hash, "signature_hash": a.signature_hash,
            "created_at": a.created_at.isoformat()}


def decision_summary(case: DecisionCase, db: Session) -> dict:
    docs = db.execute(select(DecisionDocument).where(DecisionDocument.decision_id == case.id)).scalars().all()
    approvals = db.execute(select(Approval).where(Approval.decision_id == case.id)
                           .order_by(Approval.created_at)).scalars().all()
    gate = case.gate_result or {}
    return {
        "id": case.id, "title": case.title, "decision_type": case.decision_type, "status": case.status,
        "gate_status": case.gate_status, "description": case.description,
        "as_of_date": case.as_of_date.isoformat() if case.as_of_date else None,
        "due_date": case.due_date.isoformat() if case.due_date else None,
        "options": case.options or [], "selected_option": case.selected_option,
        "documents_count": len(docs), "created_by": case.created_by,
        "created_at": case.created_at.isoformat(), "updated_at": case.updated_at.isoformat(),
        "gate_evaluated_at": case.gate_evaluated_at.isoformat() if case.gate_evaluated_at else None,
        "blocking_failures": gate.get("blocking_failures", []), "warnings": gate.get("warnings", []),
        "summary": gate.get("summary"), "exception_recorded": case.exception_recorded,
        "exception_reason": case.exception_reason,
        "reviews": [{"action": a.action, "comment": a.comment, "created_at": a.created_at.isoformat()}
                    for a in approvals],
    }


def decision_detail(case: DecisionCase, db: Session) -> dict:
    base = decision_summary(case, db)
    docs = []
    for dd in db.execute(select(DecisionDocument).where(DecisionDocument.decision_id == case.id)).scalars():
        doc = db.get(Document, dd.document_id)
        if doc:
            versions = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                                  .order_by(DocumentVersion.version_number)).scalars().all()
            current = next((v for v in versions if v.is_current), None)
            claim_count = len(db.execute(select(Claim.id).where(Claim.version_id == current.id)).scalars().all()) if current else 0
            docs.append({"added_at": dd.added_at.isoformat(), "claim_count": claim_count,
                         **document_dict(doc, list(versions))})
    approvals = []
    for a in db.execute(select(Approval).where(Approval.decision_id == case.id)
                        .order_by(Approval.created_at)).scalars():
        approvals.append(approval_dict(a, db.get(User, a.user_id)))
    analyses = [{"id": a.id, "status": a.status, "provider_label": a.provider_label, "model": a.model,
                 "created_at": a.created_at.isoformat(), "summary": a.summary,
                 "recommendation": a.recommendation, "statements": a.statements or [],
                 "validation": a.validation or {}, "snapshot_hash": a.snapshot_hash,
                 "unsupported": len([s for s in (a.statements or []) if not s.get("supported")])}
                for a in db.execute(select(Analysis).where(Analysis.decision_id == case.id)
                                    .order_by(Analysis.created_at.desc())).scalars()]
    gate_checks = db.execute(select(GateCheck).where(GateCheck.decision_id == case.id)
                             .order_by(GateCheck.id)).scalars().all()
    base.update({
        "documents": docs, "approvals": approvals, "analyses": analyses,
        "gate_result": case.gate_result or {},
        "gate_checks": [{"code": c.code, "label": c.label, "status": c.status, "blocking": c.blocking,
                         "detail": c.detail, "refs": c.refs, "evaluated_at": c.evaluated_at.isoformat()}
                        for c in gate_checks],
        "criteria": case.criteria or [], "requirements": case.requirements or [],
        "policy_snapshot": case.policy_snapshot or {},
        "exception_recorded": case.exception_recorded, "exception_reason": case.exception_reason,
    })
    return base
