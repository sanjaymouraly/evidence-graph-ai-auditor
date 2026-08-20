"""The ten mandatory end-to-end acceptance scenarios, exercised through the real API."""
from __future__ import annotations

import json


from app.config import BASE_DIR

FILES = BASE_DIR.parent / "datasets" / "synthetic" / "dev" / "files"
APEX = "Approve Apex Systems as compressor supplier — Chennai Phase 2"
BOREALIS = "Borealis Industrial spare-parts supply award"
M2 = "Chennai Phase 2 — milestone M2 release"
CASCADE = "Award pump maintenance contract to Cascade Pumps"


def test_scenario_1_supplier_contradiction(client, tokens, decision_index):
    decision = decision_index[APEX]
    listing = client.get(f"/api/contradictions?state=confirmed&decision_id={decision['id']}",
                         headers=tokens["admin"]).json()
    assert listing["total"] >= 2
    price = next(c for c in listing["items"] if c["kind"] == "price_conflict")
    assert price["blocking"] is True
    assert price["claim_a"]["document_title"] and price["claim_b"]["document_title"]
    assert "quoted_span" in price["claim_a"]
    # Both sides resolve to distinct stored documents.
    assert price["claim_a"]["document_id"] != price["claim_b"]["document_id"]
    # And the gate reflects the unresolved conflict for the decision.
    detail = client.get(f"/api/decisions/{decision['id']}", headers=tokens["admin"]).json()
    assert detail["status"] == "CONTRADICTED"
    assert "no_blocking_contradictions" in detail["blocking_failures"]


def test_scenario_2_outdated_evidence_flags_decision(client, tokens, decision_index):
    decision = decision_index[BOREALIS]
    detail = client.get(f"/api/decisions/{decision['id']}", headers=tokens["admin"]).json()
    assert detail["status"] == "EVIDENCE_INCOMPLETE"
    assert "evidence_freshness" in detail["blocking_failures"]
    claims = client.get(f"/api/claims?decision_id={decision['id']}&page_size=100",
                        headers=tokens["admin"]).json()["items"]
    expired = [c for c in claims if c["verification_status"] == "unsupported"]
    assert expired, "expired certificate claims must be marked unsupported"
    assert any("expired" in (c["meta"].get("verification_reason") or "").lower() for c in expired)


def test_scenario_3_missing_required_evidence_blocks(client, tokens, decision_index):
    decision = decision_index[M2]
    checklist = client.get(f"/api/decisions/{decision['id']}/checklist", headers=tokens["admin"]).json()
    assert checklist["eligible_for_approval"] is False
    assert "mandatory_sources" in checklist["blocking_failures"]
    assert "requirement_coverage" in checklist["blocking_failures"]
    gap = next(r for r in checklist["requirements"] if not r["pass"])
    assert "risk_register" in gap["missing_doc_types"]
    approval = client.post(f"/api/decisions/{decision['id']}/approve", headers=tokens["reviewer"],
                           json={"comment": "attempt"})
    assert approval.status_code == 409


def test_scenario_4_verified_decision_is_approved(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    checklist = client.get(f"/api/decisions/{decision['id']}/checklist", headers=tokens["admin"]).json()
    assert checklist["gate_status"] == "SUFFICIENTLY_SUPPORTED"
    assert checklist["eligible_for_approval"] is True
    detail = client.get(f"/api/decisions/{decision['id']}", headers=tokens["admin"]).json()
    assert detail["status"] == "APPROVED"
    actions = [a["action"] for a in detail["approvals"]]
    assert "attest" in actions and "approve" in actions
    assert detail["approvals"][-1]["gate_snapshot_hash"]


def test_scenario_5_unsupported_ai_statement_is_rejected():
    from app.services.analysis import validate_statements

    class StubClaim:
        id = "claim-real"
        predicate = "price"
        value_numeric = 245000
        value_text = "USD 245,000"
        object_text = "USD 245,000"
        quoted_span = "Total price: USD 245,000"
        subject_text = "Apex Systems Pvt Ltd"
        scope = "Supply of 25 compressors"
        qualifiers = {}
        effective_date = None
        valid_until = None

    statements = [
        {"id": "x1", "text": "Total price is USD 245,000.", "evidence_ids": ["claim-real"]},
        {"id": "x2", "text": "Total price is USD 999,000.", "evidence_ids": ["claim-real"]},
        {"id": "x3", "text": "The decision is approved by the gate.", "evidence_ids": ["claim-real"]},
        {"id": "x4", "text": "Delivery is fast.", "evidence_ids": ["invented-claim-id"]},
        {"id": "x5", "text": "Delivery is fast."},
    ]
    validated, validation = validate_statements(statements, {"claim-real": StubClaim()},
                                                {"result_hash": "abc"})
    supported = [s["supported"] for s in validated]
    assert supported == [True, False, False, False, False], supported
    notes = " ".join(n for s in validated for n in s.get("validation_notes", []))
    assert "numbers not grounded" in notes
    assert "gate/approval outcome" in notes
    assert "unknown evidence_id" in notes
    assert "no resolvable citation" in notes
    assert validation["unsupported"] == 4


def test_scenario_6_document_revision_invalidates_dependent_reviews(client, tokens):
    # Build an isolated supplier file so the revision cannot disturb other cases.
    base_quote = b"""SUPPLIER: Orion Bearings Ltd
Buyer: Northwind Manufacturing Pvt Ltd
Date: 2025-07-01
Valid until: 2025-12-31
Scope: Supply of 10 bearing assemblies for Chennai plant Phase 2
Unit price: USD 1,000 per assembly
Total price: USD 10,000 for 10 units
Delivery: within 20 days of purchase order
Payment terms: Net 30
Warranty: 12 months from commissioning
Certification: Orion Bearings Ltd holds ISO 9001:2015 certification.
"""
    upload = client.post("/api/documents", headers=tokens["auditor"],
                         files={"file": ("orion_quote_v1.txt", base_quote, "text/plain")},
                         data={"doc_type": "quote", "doc_key": "orion-quote",
                               "primary_entity": "Orion Bearings Ltd"})
    assert upload.status_code == 201 and upload.json()["duplicate"] is False
    document_id = upload.json()["document_id"]
    decision = client.post("/api/decisions", headers=tokens["auditor"], json={
        "title": "Orion Bearings trial award", "decision_type": "procurement",
        "options": ["Orion Bearings Ltd"], "as_of_date": "2025-09-01",
        "document_ids": [document_id]})
    assert decision.status_code == 201, decision.text
    decision_id = decision.json()["id"]
    evaluated = client.post(f"/api/decisions/{decision_id}/evaluate", headers=tokens["auditor"], json={})
    assert evaluated.status_code == 200
    revised = base_quote.replace(b"USD 10,000", b"USD 12,500").replace(
        b"orion_quote_v1.txt", b"orion_quote_v2.txt")
    revision = client.post("/api/documents", headers=tokens["auditor"],
                           files={"file": ("orion_quote_v2.txt", revised, "text/plain")},
                           data={"doc_type": "quote", "doc_key": "orion-quote",
                                 "primary_entity": "Orion Bearings Ltd"})
    assert revision.status_code == 201
    assert revision.json()["version_number"] == 2
    versions = client.get(f"/api/documents/{document_id}", headers=tokens["auditor"]).json()["versions"]
    assert versions[0]["is_current"] is True
    assert versions[1]["superseded_by_version_id"] == versions[0]["id"]
    impact = client.get(f"/api/graph/impact/{versions[0]['id']}", headers=tokens["auditor"])
    assert impact.status_code == 200
    refreshed = client.get(f"/api/decisions/{decision_id}", headers=tokens["auditor"]).json()
    # The dependent decision now evaluates the revised evidence (a price conflict appears).
    assert refreshed["status"] in {"CONTRADICTED", "REVIEW_REQUIRED", "EVIDENCE_INCOMPLETE"}
    assert refreshed["gate_evaluated_at"] != evaluated.json()["gate"]["evaluated_at"]


def test_scenario_7_traceability_to_original_span(client, tokens):
    listing = client.get("/api/claims?predicate=price&page_size=50", headers=tokens["admin"]).json()
    claim = next(c for c in listing["items"] if c["document"] and c["document"]["doc_type"] == "quote")
    trace = client.get(f"/api/claims/{claim['id']}/trace", headers=tokens["admin"]).json()
    assert trace["claim"]["id"] == claim["id"]
    assert trace["document"]["id"] == claim["document"]["id"]
    assert trace["span"]["id"] == claim["span"]["id"]
    assert trace["span"]["page"] is not None or trace["span"]["char_start"] is not None
    download = client.get(f"/api/documents/{claim['document']['id']}/versions/{claim['version']['id']}/file",
                          headers=tokens["admin"])
    assert download.status_code == 200


def test_scenario_8_offline_operation(client, tokens, decision_index):
    providers = client.get("/api/providers", headers=tokens["admin"]).json()
    enabled = [p for p in providers["items"] if p["enabled"]]
    assert enabled == [], "test workspace must start without paid providers"
    decision = decision_index[CASCADE]
    analysis = client.post(f"/api/decisions/{decision['id']}/analysis", headers=tokens["auditor"],
                           json={"use_llm": False})
    assert analysis.status_code == 200
    payload = analysis.json()
    assert payload["provider_label"] == "offline-deterministic"
    assert payload["statements"]
    assert all(s["supported"] for s in payload["statements"])
    answer = client.post("/api/analysis/ask", headers=tokens["auditor"],
                         json={"question": "What is the warranty for Cascade Pumps?", "use_llm": False})
    assert answer.status_code == 200
    assert answer.json()["insufficient_evidence"] is False
    nonsense = client.post("/api/analysis/ask", headers=tokens["auditor"],
                           json={"question": "zzzz qqqq unrelated tokens", "use_llm": False})
    assert nonsense.status_code == 200
    assert nonsense.json()["insufficient_evidence"] is True


def test_scenario_9_provider_configuration_is_masked_and_ssrf_safe(client, tokens):
    created = client.post("/api/providers", headers=tokens["admin"], json={
        "name": "Local test provider", "kind": "custom", "base_url": "http://127.0.0.1:9/v1",
        "api_key": "sk-very-secret-value-123456", "model": "test-model",
        "is_default_generation": False})
    assert created.status_code == 201, created.text
    body = created.json()
    provider_id = body["id"]
    assert body["api_key_set"] is True
    assert "sk-very-secret-value-123456" not in json.dumps(body)
    assert body["api_key_hint"].endswith("3456")
    tested = client.post(f"/api/providers/{provider_id}/test", headers=tokens["admin"])
    assert tested.status_code == 200
    payload = tested.json()
    assert payload["ok"] is False
    assert "sk-very-secret" not in json.dumps(payload)
    assert not client.post("/api/providers", headers=tokens["admin"], json={
        "name": "metadata", "kind": "custom", "base_url": "http://169.254.169.254/latest"}).status_code == 201
    assert client.post("/api/providers", headers=tokens["auditor"], json={
        "name": "not allowed", "kind": "custom", "base_url": "http://127.0.0.1:9"}).status_code == 403
    deleted = client.delete(f"/api/providers/{provider_id}", headers=tokens["admin"])
    assert deleted.status_code == 200


def test_scenario_10_reproducible_report_from_snapshot(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    report = client.post(f"/api/decisions/{decision['id']}/reports", headers=tokens["reviewer"],
                         json={"decision_id": decision["id"], "kind": "json"})
    assert report.status_code == 201, report.text
    record = report.json()
    snapshot = client.get(f"/api/snapshots/{record['snapshot_id']}/verify", headers=tokens["reviewer"]).json()
    assert snapshot["hash_match"] is True
    regenerated = client.post(f"/api/reports/{record['id']}/regenerate", headers=tokens["reviewer"])
    assert regenerated.status_code == 201
    assert regenerated.json()["snapshot_verification"]["hash_match"] is True
    assert regenerated.json()["snapshot_id"] == record["snapshot_id"]
    snapshot_payload = client.get(f"/api/snapshots/{record['snapshot_id']}/download",
                                  headers=tokens["reviewer"]).json()
    assert snapshot_payload["reproducibility"]["gate_result_hash"]
    assert snapshot_payload["integrity"]["claims_hash"]
    assert snapshot_payload["decision"]["id"] == decision["id"]
