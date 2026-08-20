"""HTTP API behaviour: authentication, RBAC, ingestion validation, pagination, exports."""
from __future__ import annotations



from app.config import BASE_DIR

DATASET = BASE_DIR.parent / "datasets" / "synthetic" / "dev" / "files"


def test_health_and_version(client):
    assert client.get("/api/health").json()["status"] == "ok"
    ready = client.get("/api/health/ready").json()
    assert ready["status"] == "ready"
    assert client.get("/api/health/version").json()["gate"] == "gate-1.2"


def test_login_rejects_bad_password(client, credentials):
    response = client.post("/api/auth/login", json={"email": credentials["admin"]["email"],
                                                    "password": "wrong-password"})
    assert response.status_code == 401
    assert "Invalid" in response.json()["detail"]


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_role_and_workspace(client, tokens):
    payload = client.get("/api/auth/me", headers=tokens["reviewer"]).json()
    assert payload["user"]["role"] == "reviewer"
    assert payload["workspace"]["slug"] == "test-dev"


def test_viewer_cannot_create_decisions(client, tokens):
    response = client.post("/api/decisions", headers=tokens["viewer"], json={
        "title": "Viewer attempt", "decision_type": "procurement", "options": []})
    assert response.status_code == 403


def test_viewer_cannot_run_analysis_or_upload(client, tokens):
    assert client.post("/api/claims/x/review", headers=tokens["viewer"],
                       json={"action": "verify"}).status_code == 403
    upload = client.post("/api/documents", headers=tokens["viewer"],
                         files={"file": ("note.txt", b"hello", "text/plain")})
    assert upload.status_code == 403


def test_workspace_endpoint_reports_policy_and_gate_version(client, tokens):
    payload = client.get("/api/workspace", headers=tokens["admin"]).json()
    assert payload["slug"] == "test-dev"
    assert payload["policy"]["version"] == "policy-2026.1"
    assert payload["policy"]["gate_version"] == "gate-1.2"
    assert payload["policy"]["requirements"]["procurement"]
    assert payload["policy"]["min_options"]["procurement"] == 2
    assert payload["policy"]["freshness_days"]["quote"] == 180


def test_dashboard_metrics_come_from_state(client, tokens):
    payload = client.get("/api/dashboard", headers=tokens["admin"]).json()
    assert payload["documents"]["total"] >= 20
    assert payload["claims"]["total"] > 50
    assert payload["contradictions"]["confirmed"] >= 2
    assert payload["evidence_health_score"] >= 0


def test_document_list_and_detail(client, tokens):
    listing = client.get("/api/documents?page_size=100", headers=tokens["admin"]).json()
    assert listing["total"] >= 20
    doc = next(d for d in listing["items"] if d["doc_key"] == "apex-quote")
    detail = client.get(f"/api/documents/{doc['id']}", headers=tokens["admin"]).json()
    assert detail["version_count"] == 2
    assert any(v["is_current"] for v in detail["versions"])
    assert detail["current_version"]["version_number"] == 2


def test_unsupported_upload_extension_is_rejected(client, tokens):
    response = client.post("/api/documents", headers=tokens["auditor"],
                           files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert response.status_code == 400
    assert "Unsupported" in response.json()["detail"]


def test_duplicate_upload_is_detected_not_overwritten(client, tokens):
    path = DATASET / "cascade_quote.pdf"
    first = client.post("/api/documents", headers=tokens["auditor"],
                        files={"file": (path.name, path.read_bytes(), "application/pdf")},
                        data={"doc_type": "quote"})
    assert first.status_code == 201
    second = client.post("/api/documents", headers=tokens["auditor"],
                         files={"file": (path.name, path.read_bytes(), "application/pdf")})
    assert second.status_code == 201
    assert second.json()["duplicate"] is True
    assert second.json()["version_id"] == first.json()["version_id"]


def test_cross_workspace_document_access_is_denied(client, tokens, db_session):
    from app.db.models import Document, Workspace
    other = Workspace(name="Other", slug="other-workspace", settings={})
    db_session.add(other)
    db_session.flush()
    doc = Document(workspace_id=other.id, doc_key="secret", title="Secret", doc_type="quote")
    db_session.add(doc)
    db_session.commit()
    response = client.get(f"/api/documents/{doc.id}", headers=tokens["admin"])
    assert response.status_code == 404


def test_version_file_outside_workspace_upload_root_is_denied(client, tokens, db_session, tmp_path):
    """A stored path must live under this workspace's upload directory, not merely contain its id."""
    from app.db.models import Document, DocumentVersion, Workspace

    workspace = db_session.query(Workspace).filter(Workspace.slug == "test-dev").one()
    document = Document(workspace_id=workspace.id, doc_key="path-escape", title="Path escape",
                        doc_type="generic")
    db_session.add(document)
    db_session.flush()
    outside = tmp_path / "outside.txt"
    outside.write_text("not this workspace's upload")
    version = DocumentVersion(document_id=document.id, workspace_id=workspace.id, version_number=1,
                              filename="outside.txt", content_hash="0" * 64, size_bytes=outside.stat().st_size,
                              storage_path=str(outside), status="processed", is_current=True)
    db_session.add(version)
    db_session.commit()
    response = client.get(f"/api/documents/{document.id}/versions/{version.id}/file",
                          headers=tokens["admin"])
    assert response.status_code == 403
    assert "Cross-workspace" in response.json()["detail"]


def test_claims_pagination_and_facets(client, tokens):
    page = client.get("/api/claims?page=1&page_size=10", headers=tokens["admin"]).json()
    assert page["page"] == 1 and len(page["items"]) <= 10
    assert page["pages"] >= 2
    facets = client.get("/api/claims/facets", headers=tokens["admin"]).json()
    assert "price" in facets["predicates"]
    assert sum(facets["statuses"].values()) >= page["total"]


def test_claim_detail_includes_span_locator_and_citation(client, tokens):
    listing = client.get("/api/claims?predicate=price&page_size=20", headers=tokens["admin"]).json()
    claim = listing["items"][0]
    detail = client.get(f"/api/claims/{claim['id']}", headers=tokens["admin"]).json()
    assert detail["span"] is not None
    assert detail["citation"]["resolves"] is True
    assert detail["version"]["content_hash"]


def test_claim_review_updates_status_and_audit(client, tokens):
    listing = client.get("/api/claims?predicate=risk_level&page_size=5", headers=tokens["admin"]).json()
    claim_id = listing["items"][0]["id"]
    reviewed = client.post(f"/api/claims/{claim_id}/review", headers=tokens["reviewer"],
                           json={"action": "flag", "note": "needs confirmation"})
    assert reviewed.status_code == 200
    assert reviewed.json()["verification_status"] == "disputed"
    restored = client.post(f"/api/claims/{claim_id}/review", headers=tokens["reviewer"],
                           json={"action": "reset", "note": "restored by test"})
    assert restored.status_code == 200


def test_graph_focus_and_stats(client, tokens, decision_index):
    decision = decision_index["Approve Apex Systems as compressor supplier — Chennai Phase 2"]
    response = client.get(f"/api/graph?decision_id={decision['id']}&limit=500", headers=tokens["admin"]).json()
    assert response["counts"]["nodes"] > 5
    types = {node["type"] for node in response["nodes"]}
    assert "Decision" in types
    assert "Claim" in types
    stats = client.get("/api/graph/stats", headers=tokens["admin"]).json()
    assert stats["documents"] >= 20


def test_graph_native_query_for_decision_evidence(client, tokens, decision_index):
    decision = decision_index["Award pump maintenance contract to Cascade Pumps"]
    response = client.get(f"/api/graph/queries/evidence_for_decision?decision_id={decision['id']}",
                          headers=tokens["admin"])
    assert response.status_code == 200
    assert response.json()["result"]["evidence"]


def test_contradictions_include_both_sources(client, tokens):
    payload = client.get("/api/contradictions?state=confirmed", headers=tokens["admin"]).json()
    assert payload["total"] >= 2
    item = payload["items"][0]
    assert item["claim_a"]["document_id"] and item["claim_b"]["document_id"]
    assert item["claim_a"]["locator"] is not None


def test_audit_chain_is_valid_and_exportable(client, tokens):
    verify = client.get("/api/audit/verify", headers=tokens["admin"]).json()
    assert verify["valid"] is True, verify["errors"][:3]
    listing = client.get("/api/audit?page_size=5", headers=tokens["admin"]).json()
    assert listing["total"] > 20
    export = client.get("/api/audit/export.csv", headers=tokens["admin"])
    assert export.status_code == 200 and export.text.startswith("created_at")


def test_reports_are_downloadable_and_verifiable(client, tokens, decision_index):
    reports = client.get("/api/reports?page_size=50", headers=tokens["admin"]).json()
    pdf = next((r for r in reports["items"] if r["kind"] == "pdf"), None)
    if pdf is None:
        cascade = decision_index["Award pump maintenance contract to Cascade Pumps"]
        created = client.post(f"/api/decisions/{cascade['id']}/reports", headers=tokens["reviewer"],
                              json={"decision_id": cascade["id"], "kind": "pdf"})
        assert created.status_code == 201, created.text
        pdf = created.json()
    download = client.get(f"/api/reports/{pdf['id']}/download", headers=tokens["admin"])
    assert download.status_code == 200
    assert download.content[:4] == b"%PDF"
    snapshot = client.get(f"/api/snapshots/{pdf['snapshot_id']}/verify", headers=tokens["admin"]).json()
    assert snapshot["hash_match"] is True


def test_rate_limit_header_and_request_id(client, tokens):
    response = client.get("/api/health", headers={"X-Correlation-ID": "test-correlation"})
    assert response.headers["X-Correlation-ID"] == "test-correlation"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
