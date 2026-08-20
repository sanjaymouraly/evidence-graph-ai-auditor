"""Document ingestion, versioning and source viewer endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_document_or_404, paged, pagination
from app.api.schemas import DECISION_TYPES  # noqa: F401  (re-exported for OpenAPI ordering)
from app.config import get_settings
from app.core.security import require_permission
from app.db.models import (Claim, DecisionCase, DecisionDocument, Document, DocumentVersion,
                           IngestionJob, SourceSpan, User)
from app.db.session import get_db
from app.services.claims import citation_resolves
from app.services.ingestion import ingest_document, process_version, reprocess_version, soft_delete_version

router = APIRouter(prefix="/api/documents", tags=["documents"])

DOC_TYPES = ["quote", "contract", "certificate", "invoice", "spec", "requirements", "schedule",
             "technical_assessment", "policy", "risk_register", "email", "decision_record",
             "spreadsheet", "report", "generic"]


def _version_dict(v: DocumentVersion) -> dict:
    return {
        "id": v.id, "version_number": v.version_number, "filename": v.filename,
        "mime_type": v.mime_type, "content_hash": v.content_hash, "size_bytes": v.size_bytes,
        "status": v.status, "error": v.error, "warnings": v.warnings or [],
        "is_current": v.is_current, "superseded_by_version_id": v.superseded_by_version_id,
        "effective_date": v.effective_date.isoformat() if v.effective_date else None,
        "issued_date": v.issued_date.isoformat() if v.issued_date else None,
        "valid_until": v.valid_until.isoformat() if v.valid_until else None,
        "source_author": v.source_author, "source_org": v.source_org,
        "primary_entity": v.primary_entity, "trust_level": v.trust_level,
        "injection_flags": v.injection_flags or [],
        "uploaded_at": v.created_at.isoformat() if v.created_at else None,
        "processed_at": v.processed_at.isoformat() if v.processed_at else None,
        "meta": {k: val for k, val in (v.meta or {}).items() if k != "sheets"},
    }


def _doc_dict(d: Document, db: Session) -> dict:
    versions = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == d.id)
                          .order_by(DocumentVersion.version_number.desc())).scalars().all()
    current = next((v for v in versions if v.is_current), versions[0] if versions else None)
    claims = db.scalar(select(func.count()).select_from(Claim).where(Claim.version_id.in_(
        [v.id for v in versions]))) if versions else 0
    decisions = db.execute(select(DecisionCase.title).join(DecisionDocument,
                          DecisionDocument.decision_id == DecisionCase.id)
                          .where(DecisionDocument.document_id == d.id)).scalars().all()
    return {
        "id": d.id, "doc_key": d.doc_key, "title": d.title, "doc_type": d.doc_type,
        "description": d.description, "created_at": d.created_at.isoformat() if d.created_at else None,
        "version_count": len(versions), "claim_count": claims,
        "current_version": _version_dict(current) if current else None,
        "versions": [_version_dict(v) for v in versions],
        "decisions": list(decisions),
    }


@router.get("/meta/types")
def doc_types(user: User = Depends(require_permission("document:read"))) -> dict:
    return {"doc_types": DOC_TYPES}


@router.post("", status_code=201)
def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    doc_type: str = Form(default="auto"),
    doc_key: str | None = Form(default=None),
    effective_date: str | None = Form(default=None),
    valid_until: str | None = Form(default=None),
    primary_entity: str | None = Form(default=None),
    trust_level: float = Form(default=0.7),
    description: str = Form(default=""),
    allow_duplicate: bool = Form(default=False),
    process: bool = Form(default=True),
    user: User = Depends(require_permission("document:write")),
    db: Session = Depends(get_db),
) -> dict:
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    try:
        result = ingest_document(db, user, file.filename or "upload.bin", data, title=title,
                                 doc_type=doc_type or "auto", doc_key=doc_key,
                                 effective_date=effective_date or None, valid_until=valid_until or None,
                                 primary_entity=primary_entity or None, trust_level=trust_level,
                                 description=description, allow_duplicate=allow_duplicate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("duplicate"):
        return {**result, "status": "duplicate"}
    if process:
        try:
            processed = process_version(result["version_id"], actor_id=user.id)
        except Exception as exc:  # keep the uploaded evidence, report partial failure
            processed = {"status": "failed", "error": str(exc)[:500]}
        result.update({"processing": processed})
        doc = db.get(Document, result["document_id"])
        db.refresh(doc) if doc else None
    return result


@router.get("")
def list_documents(q: str | None = None, doc_type: str | None = None,
                   status: str | None = None,
                   pg=Depends(pagination),
                   user: User = Depends(require_permission("document:read")),
                   db: Session = Depends(get_db)) -> dict:
    stmt = select(Document).where(Document.workspace_id == user.workspace_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(Document.title).like(like) | func.lower(Document.doc_key).like(like))
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if status:
        stmt = stmt.where(Document.id.in_(
            select(DocumentVersion.document_id).where(DocumentVersion.status == status,
                                                      DocumentVersion.is_current.is_(True))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Document.updated_at.desc())
                      .offset(pg.offset).limit(pg.page_size)).scalars().all()
    return paged([_doc_dict(d, db) for d in rows], total, pg)


@router.get("/{document_id}")
def get_document(document_id: str, user: User = Depends(require_permission("document:read")),
                 db: Session = Depends(get_db)) -> dict:
    doc = get_document_or_404(db, user, document_id)
    payload = _doc_dict(doc, db)
    jobs = db.execute(select(IngestionJob).where(IngestionJob.version_id.in_(
        [v["id"] for v in payload["versions"]])).order_by(IngestionJob.created_at.desc())).scalars().all()
    payload["jobs"] = [{"id": j.id, "version_id": j.version_id, "status": j.status,
                        "stage": j.stage, "error": j.error, "attempts": j.attempts,
                        "created_at": j.created_at.isoformat() if j.created_at else None}
                       for j in jobs]
    return payload


@router.get("/{document_id}/versions/{version_id}/spans")
def version_spans(document_id: str, version_id: str,
                  user: User = Depends(require_permission("document:read")),
                  db: Session = Depends(get_db)) -> dict:
    doc = get_document_or_404(db, user, document_id)
    version = db.get(DocumentVersion, version_id)
    if not version or version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found for document")
    spans = db.execute(select(SourceSpan).where(SourceSpan.version_id == version.id)
                       .order_by(SourceSpan.ordinal)).scalars().all()
    claims = db.execute(select(Claim).where(Claim.version_id == version.id)).scalars().all()
    claims_by_span: dict[str, list] = {}
    for c in claims:
        claims_by_span.setdefault(c.span_id, []).append({
            "id": c.id, "predicate": c.predicate, "object": c.object_text,
            "verification_status": c.verification_status,
            "citation_ok": citation_resolves(c, next((s for s in spans if s.id == c.span_id), None))[0],
        })
    return {
        "document": {"id": doc.id, "title": doc.title, "doc_type": doc.doc_type},
        "version": _version_dict(version),
        "spans": [{
            "id": s.id, "ordinal": s.ordinal, "locator_type": s.locator_type, "page": s.page,
            "bbox": s.bbox, "paragraph_index": s.paragraph_index, "sheet": s.sheet,
            "cell_range": s.cell_range, "char_start": s.char_start, "char_end": s.char_end,
            "section_label": s.section_label, "text": s.text, "meta": s.meta or {},
            "claims": claims_by_span.get(s.id, []),
        } for s in spans],
        "sheets": (version.meta or {}).get("sheets", {}),
        "claim_count": len(claims),
    }


@router.get("/{document_id}/versions/{version_id}/file")
def download_version(document_id: str, version_id: str,
                     user: User = Depends(require_permission("document:read")),
                     db: Session = Depends(get_db)):
    doc = get_document_or_404(db, user, document_id)
    version = db.get(DocumentVersion, version_id)
    if not version or version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found")
    path = Path(version.storage_path or "")
    if not version.storage_path or not path.exists():
        raise HTTPException(status_code=410, detail="Stored file is unavailable")
    workspace_upload_root = (get_settings().upload_dir / user.workspace_id).resolve()
    try:
        path.resolve().relative_to(workspace_upload_root)
    except ValueError:
        # A substring check let ``/data/uploads/<ws>`` match ``/data/uploads/<ws>-other`` and
        # any path that merely mentioned the id. Compare resolved parents instead.
        raise HTTPException(status_code=403, detail="Cross-workspace file access denied")
    return FileResponse(path, media_type=version.mime_type or "application/octet-stream",
                        filename=version.filename)


@router.get("/{document_id}/versions/{version_id}/text", response_class=PlainTextResponse)
def version_text(document_id: str, version_id: str,
                 user: User = Depends(require_permission("document:read")),
                 db: Session = Depends(get_db)) -> PlainTextResponse:
    doc = get_document_or_404(db, user, document_id)
    version = db.get(DocumentVersion, version_id)
    if not version or version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found")
    spans = db.execute(select(SourceSpan).where(SourceSpan.version_id == version.id)
                       .order_by(SourceSpan.ordinal)).scalars().all()
    text = "\n\n".join(s.text for s in spans)
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@router.post("/{document_id}/versions/{version_id}/reprocess")
def reprocess(document_id: str, version_id: str,
              user: User = Depends(require_permission("document:write")),
              db: Session = Depends(get_db)) -> dict:
    doc = get_document_or_404(db, user, document_id)
    version = db.get(DocumentVersion, version_id)
    if not version or version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found")
    try:
        result = reprocess_version(db, user, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.delete("/{document_id}/versions/{version_id}")
def delete_version(document_id: str, version_id: str,
                   user: User = Depends(require_permission("document:write")),
                   db: Session = Depends(get_db)) -> dict:
    doc = get_document_or_404(db, user, document_id)
    version = db.get(DocumentVersion, version_id)
    if not version or version.document_id != doc.id:
        raise HTTPException(status_code=404, detail="Version not found")
    try:
        return soft_delete_version(db, user, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
