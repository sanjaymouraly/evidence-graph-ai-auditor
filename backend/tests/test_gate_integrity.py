"""Regression tests for approval integrity.

Two properties are covered here:

1. Re-running the deterministic analysis on an already-approved case must not silently
   invalidate the reviewer attestation when the evidence state (and therefore the gate
   result) is unchanged. The pre-attestation hash now binds the *content* of the analysis
   rather than its volatile row id.
2. When an approved case *does* regress (its evidence changed), the demotion must be
   explicitly recorded as ``decision.approval_invalidated``. A silent status change would
   make the audit trail misleading.
"""
from __future__ import annotations

CASCADE = "Award pump maintenance contract to Cascade Pumps"


def test_reanalysis_keeps_approval_when_gate_result_is_unchanged(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    before = client.get(f"/api/decisions/{decision['id']}", headers=tokens["admin"]).json()
    assert before["status"] == "APPROVED"

    response = client.post(f"/api/decisions/{decision['id']}/analysis", headers=tokens["admin"],
                           json={"use_llm": False})
    assert response.status_code == 200, response.text

    after = client.get(f"/api/decisions/{decision['id']}", headers=tokens["admin"]).json()
    assert after["status"] == "APPROVED"
    assert after["gate_status"] == "SUFFICIENTLY_SUPPORTED"

    events = client.get(f"/api/decisions/{decision['id']}/audit", headers=tokens["admin"]).json()["events"]
    assert not [e for e in events if e["action"] == "decision.approval_invalidated"]


def test_evidence_change_demotes_approved_case_with_explicit_event(client, tokens, decision_index):
    source = decision_index[CASCADE]
    detail = client.get(f"/api/decisions/{source['id']}", headers=tokens["admin"]).json()
    document_ids = [doc["id"] for doc in detail["documents"]]

    created = client.post("/api/decisions", headers=tokens["admin"], json={
        "title": "Integrity regression — Cascade clone",
        "decision_type": "procurement",
        "description": "Clone used to verify approval invalidation semantics.",
        "options": [option["name"] for option in detail["options"]],
        "as_of_date": detail["as_of_date"],
        "document_ids": document_ids,
    })
    assert created.status_code == 201, created.text
    clone = created.json()["id"]

    evaluated = client.post(f"/api/decisions/{clone}/evaluate", headers=tokens["admin"], json={})
    assert evaluated.status_code == 200, evaluated.text
    # Without a reviewer attestation the gate must not claim sufficiency.
    assert evaluated.json()["gate"]["status"] == "REVIEW_REQUIRED"

    attested = client.post(f"/api/decisions/{clone}/attest", headers=tokens["reviewer"],
                           json={"comment": "Reviewed the cloned evidence pack."})
    assert attested.status_code == 200, attested.text
    assert attested.json()["gate_status"] == "SUFFICIENTLY_SUPPORTED"
    approved = client.post(f"/api/decisions/{clone}/approve", headers=tokens["reviewer"],
                           json={"comment": "Approved after gate verification."})
    assert approved.status_code == 200, approved.text

    # Approved decisions are immutable through the API, so the evidence change is applied
    # out of band (as a data migration or ops action would). The gate must still notice it.
    from app.db.models import DecisionDocument
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.query(DecisionDocument).filter(DecisionDocument.decision_id == clone,
                                          DecisionDocument.document_id == document_ids[0]).delete()
        db.commit()
    finally:
        db.close()
    reevaluated = client.post(f"/api/decisions/{clone}/evaluate", headers=tokens["admin"], json={})
    assert reevaluated.status_code == 200
    assert reevaluated.json()["gate"]["status"] != "SUFFICIENTLY_SUPPORTED"

    after = client.get(f"/api/decisions/{clone}", headers=tokens["admin"]).json()
    assert after["status"] == "REVIEW_REQUIRED"
    assert after["status"] != "APPROVED"
    events = client.get(f"/api/decisions/{clone}/audit", headers=tokens["admin"]).json()["events"]
    invalidation = [e for e in events if e["action"] == "decision.approval_invalidated"]
    assert len(invalidation) == 1
    assert invalidation[0]["payload"]["previous_status"] == "APPROVED"
