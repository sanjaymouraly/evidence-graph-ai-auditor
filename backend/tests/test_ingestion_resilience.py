"""Reprocessing must never erase evidence when a rebuild fails halfway."""
from __future__ import annotations

from sqlalchemy import delete, or_, select

from app.db.models import (
    Claim, Document, DocumentVersion, Edge, IngestionJob, SourceSpan, User, Workspace,
)
from app.services.ingestion import ingest_document, process_version


def test_failed_reprocess_keeps_previous_spans_and_claims(client, seeded, db_session, monkeypatch):
    workspace = db_session.execute(select(Workspace).where(Workspace.slug == "test-dev")).scalars().one()
    admin = db_session.execute(
        select(User).where(User.workspace_id == workspace.id, User.role == "admin")
    ).scalars().first()
    created = ingest_document(
        db_session, admin, "resilience-note.txt",
        b"Supplier: Cascade Pumps\nWarranty: 36 months\nTotal price: USD 120000\n",
        title="Resilience note", doc_type="quote", doc_key="resilience-test-note",
    )
    version_id = created["version_id"]
    document_id = created["document_id"]
    first = process_version(version_id, admin.id)
    assert first["status"] == "processed", first

    def current_derived() -> tuple[set[str], set[str]]:
        db_session.expire_all()
        spans = set(db_session.execute(
            select(SourceSpan.id).where(SourceSpan.version_id == version_id)
        ).scalars())
        claims = set(db_session.execute(
            select(Claim.id).where(Claim.version_id == version_id)
        ).scalars())
        return spans, claims

    span_ids, claim_ids = current_derived()
    assert span_ids and claim_ids

    try:
        version = db_session.get(DocumentVersion, version_id)
        version.status = "uploaded"
        version.processed_at = None
        db_session.commit()

        def explode(*args, **kwargs):
            raise ValueError("forced extraction failure")

        monkeypatch.setattr("app.services.ingestion.extract_and_store_claims", explode)
        failed = process_version(version_id, admin.id)
        assert failed["status"] == "failed"

        after_spans, after_claims = current_derived()
        assert after_spans == span_ids
        assert after_claims == claim_ids
        version = db_session.get(DocumentVersion, version_id)
        assert version.status == "failed"
    finally:
        # Keep the shared session-scoped test database clean for later assertions.
        db_session.rollback()
        claim_id_select = select(Claim.id).where(Claim.version_id == version_id)
        span_id_select = select(SourceSpan.id).where(SourceSpan.version_id == version_id)
        db_session.execute(delete(Edge).where(Edge.workspace_id == workspace.id, or_(
            Edge.source_id.in_(claim_id_select), Edge.target_id.in_(claim_id_select),
            Edge.source_id.in_(span_id_select), Edge.target_id.in_(span_id_select),
        )))
        db_session.execute(delete(Claim).where(Claim.version_id == version_id))
        db_session.execute(delete(SourceSpan).where(SourceSpan.version_id == version_id))
        db_session.execute(delete(IngestionJob).where(IngestionJob.version_id == version_id))
        db_session.execute(delete(DocumentVersion).where(DocumentVersion.id == version_id))
        db_session.execute(delete(Document).where(Document.id == document_id))
        db_session.commit()
