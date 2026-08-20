"""Regression tests for decision-date-aware evidence evaluation.

Claim verification status is a workspace-level property computed "now": a quotation that
expired last year is legitimately flagged unsupported today. A decision taken *while that
quotation was in force* must still be scored on it. These tests pin that behaviour so a
future refactor cannot silently drop evidence from the option comparison.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.db.models import Claim, DecisionCase, Document, DocumentVersion, Workspace, utcnow  # noqa: F401


def _claim(value: float, predicate: str, *, valid_until: date | None, effective: date) -> Claim:
    return Claim(
        id=f"c-{predicate}-{value}", workspace_id="ws", version_id="v1", span_id="s1",
        entity_id="e1", subject_text="Vendor A", predicate=predicate, claim_key="k",
        object_text=str(value), quoted_span="quote", value_numeric=value,
        verification_status="unsupported", review_status="pending",
        effective_date=effective, valid_until=valid_until,
    )


def test_mcda_counts_evidence_valid_at_decision_date():
    from app.services.gate import compute_mcda

    case = DecisionCase(id="d1", workspace_id="ws", title="t", decision_type="procurement",
                        status="DRAFT", options=[{"name": "Vendor A", "entity_id": "e1"},
                                                 {"name": "Vendor B", "entity_id": "e2"}],
                        as_of_date=date(2025, 9, 1))
    expired_after_decision = _claim(100.0, "price", valid_until=date(2025, 12, 31), effective=date(2025, 8, 1))
    expired_before_decision = _claim(50.0, "delivery_days", valid_until=date(2025, 8, 1), effective=date(2025, 1, 1))
    active = _claim(200.0, "price", valid_until=None, effective=date(2025, 8, 1))

    version = DocumentVersion(id="v1", workspace_id="ws", document_id="doc1", version_number=1,
                              is_current=True, status="processed", content_hash="x",
                              created_at=datetime(2025, 8, 1, tzinfo=timezone.utc))

    class _StubRepo:
        """Minimal session double: entity lookups resolve by id and no documents are needed."""

        def __init__(self):
            self.entities = {"e1", "e2"}

        def get(self, model, pk):  # noqa: ANN001
            if model.__name__ == "Entity" and pk in self.entities:
                class _E:
                    id = pk
                    canonical_name = "Vendor A" if pk == "e1" else "Vendor B"
                return _E()
            return None

        def execute(self, stmt):  # noqa: ANN001
            class _Result:
                def scalars(self_inner):
                    return self_inner

                def __iter__(self_inner):
                    return iter([])
            return _Result()

        def get_bind(self):  # pragma: no cover - compatibility only
            return None

    claims = [expired_after_decision, expired_before_decision, active]
    result = compute_mcda(_StubRepo(), case, claims, versions={"v1": version}, as_of=date(2025, 9, 1))
    row_a = next(row for row in result["metrics"] if row["option"] == "Vendor A")

    # Price is the cheaper of the two, and only the price claim valid on 2025-09-01 counts.
    assert row_a["price"] == 100.0
    # Delivery evidence expired before the decision date is excluded, not scored.
    assert row_a["delivery"] is None


def test_mcda_excludes_expired_and_rejected_claims():
    from app.services.gate import compute_mcda

    case = DecisionCase(id="d1", workspace_id="ws", title="t", decision_type="procurement",
                        status="DRAFT", options=[{"name": "Vendor A", "entity_id": "e1"},
                                                 {"name": "Vendor B", "entity_id": "e2"}],
                        as_of_date=date(2026, 1, 1))
    rejected = _claim(10.0, "price", valid_until=None, effective=date(2025, 1, 1))
    rejected.review_status = "rejected"
    verification_rejected = _claim(20.0, "price", valid_until=None, effective=date(2025, 1, 1))
    verification_rejected.verification_status = "rejected"
    expired = _claim(30.0, "price", valid_until=date(2025, 6, 1), effective=date(2025, 1, 1))

    version = DocumentVersion(id="v1", workspace_id="ws", document_id="doc1", version_number=1,
                              is_current=True, status="processed", content_hash="x",
                              created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))

    class _StubRepo:
        def get(self, model, pk):  # noqa: ANN001
            if model.__name__ == "Entity" and pk in {"e1", "e2"}:
                class _E:
                    id = pk
                    canonical_name = "Vendor A" if pk == "e1" else "Vendor B"
                return _E()
            return None

        def execute(self, stmt):  # noqa: ANN001
            class _Result:
                def scalars(self_inner):
                    return self_inner

                def __iter__(self_inner):
                    return iter([])
            return _Result()

    result = compute_mcda(_StubRepo(), case, [rejected, verification_rejected, expired],
                          versions={"v1": version}, as_of=date(2026, 1, 1))
    row_a = next(row for row in result["metrics"] if row["option"] == "Vendor A")
    assert row_a["price"] is None


def test_utcnow_is_timezone_aware():
    # Sanity check used by the audit chain and report snapshots.
    now = utcnow()
    assert now.tzinfo is not None
    assert now > now - timedelta(minutes=1)
