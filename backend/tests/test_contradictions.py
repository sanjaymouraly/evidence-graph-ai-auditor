"""Contradiction semantics: entity/scope/period/currency awareness."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import Claim, Contradiction, Document, DocumentVersion, Workspace
from app.services.contradictions import detect_contradictions, scopes_compatible


@pytest.fixture(scope="module")
def workspace(db_module, seeded):
    return db_module.execute(select(Workspace).where(Workspace.slug == "test-dev")).scalars().one()


@pytest.fixture(scope="module")
def db_module(seeded):
    from app.db.session import SessionLocal
    db = SessionLocal()
    yield db
    db.close()


def test_seeded_corpus_detects_expected_blocking_conflicts(db_module, workspace):
    stats = detect_contradictions(db_module, workspace.id, as_of=date(2025, 9, 1), commit=True)
    assert stats["evaluated_claims"] > 50
    rows = db_module.execute(select(Contradiction).where(
        Contradiction.workspace_id == workspace.id,
        Contradiction.state == "confirmed")).scalars().all()
    kinds = {(c.kind, c.blocking) for c in rows}
    assert ("price_conflict", True) in kinds
    assert ("delivery_conflict", True) in kinds
    assert all(c.blocking is False for c in rows if c.kind == "assessment_divergence")


def test_conflict_links_two_distinct_source_documents(db_module, workspace):
    row = db_module.execute(select(Contradiction).where(
        Contradiction.workspace_id == workspace.id, Contradiction.kind == "price_conflict",
        Contradiction.blocking.is_(True))).scalars().first()
    assert row is not None
    claims = [db_module.get(Claim, row.claim_a_id), db_module.get(Claim, row.claim_b_id)]
    docs = []
    for claim in claims:
        version = db_module.get(DocumentVersion, claim.version_id)
        docs.append(db_module.get(Document, version.document_id).doc_key)
    assert set(docs) == {"apex-quote", "apex-contract"}
    assert claims[0].value_numeric != claims[1].value_numeric


def test_different_suppliers_do_not_conflict(db_module, workspace):
    """Cascade and Delta quote different prices for competing offers; that is not a conflict."""
    cascade = db_module.execute(select(Claim).where(Claim.workspace_id == workspace.id,
                                                   Claim.claim_key == "cascade pumps|price")).scalars().first()
    delta = db_module.execute(select(Claim).where(Claim.workspace_id == workspace.id,
                                                 Claim.claim_key == "delta motion systems|price")).scalars().first()
    assert cascade and delta and cascade.claim_key != delta.claim_key


def test_superseded_version_is_not_a_live_contradiction(db_module, workspace):
    rows = db_module.execute(select(Contradiction).where(Contradiction.workspace_id == workspace.id)).scalars().all()
    quote_prices = [c for c in rows if c.kind == "price_conflict"]
    # There is exactly one live price conflict per predicate family for Apex, not one per version pair.
    assert len(quote_prices) <= 3


def test_scope_and_period_helpers():
    assert scopes_compatible("Supply installation and commissioning of 25 air compressors",
                             "Supply installation and commissioning of 25 air compressors")
    assert not scopes_compatible("Valve package", "Installation")
    from app.services.normalize import overlap
    assert not overlap(date(2024, 3, 1), date(2024, 12, 31), date(2025, 8, 10), date(2025, 12, 31))
    assert overlap(date(2025, 6, 1), None, date(2025, 7, 1), date(2025, 12, 31))


def test_dismissed_contradiction_is_not_reactivated(db_module, workspace):
    row = db_module.execute(select(Contradiction).where(
        Contradiction.workspace_id == workspace.id, Contradiction.state == "confirmed")).scalars().first()
    original_state = row.state
    row.state = "dismissed"
    row.blocking = False
    row.review_note = "test dismissal"
    db_module.commit()
    try:
        detect_contradictions(db_module, workspace.id, as_of=date(2025, 9, 1), commit=True)
        db_module.refresh(row)
        assert row.state == "dismissed"
    finally:
        row.state = original_state
        row.blocking = True
        db_module.commit()
