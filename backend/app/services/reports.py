"""Evidence snapshots, professional PDF/JSON reports, manifests and CSV exports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Analysis, Approval, Claim, Contradiction, DecisionCase, Document, DocumentVersion,
    ReportRecord, Snapshot,
)
from app.services.audit import append_event, verify_chain
from app.services.gate import decision_claims, evaluate_case
from app.services.serializers import locator_dict
from app.services.predicates import P

log = logging.getLogger(__name__)
REPORT_VERSION = "report-1.0"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _file_sha256(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def create_snapshot(db: Session, case: DecisionCase, user=None) -> Snapshot:
    gate_result = case.gate_result or evaluate_case(db, case, actor=user, reason="snapshot created")
    claims, versions, documents, spans = decision_claims(db, case)
    audit = verify_chain(db, case.workspace_id)
    docs_payload = []
    integrity_files = []
    for doc in documents.values():
        doc_versions = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                                  .order_by(DocumentVersion.version_number)).scalars().all()
        current = next((v for v in doc_versions if v.is_current), None)
        docs_payload.append({
            "id": doc.id, "title": doc.title, "doc_type": doc.doc_type,
            "current_version": {"id": current.id, "version_number": current.version_number,
                                "filename": current.filename, "content_hash": current.content_hash,
                                "effective_date": str(current.effective_date or ""),
                                "valid_until": str(current.valid_until or "")} if current else None,
            "versions": [{"id": v.id, "version_number": v.version_number, "filename": v.filename,
                          "content_hash": v.content_hash, "is_current": v.is_current,
                          "status": v.status} for v in doc_versions],
        })
    for v in versions.values():
        actual = _file_sha256(v.storage_path)
        integrity_files.append({"version_id": v.id, "filename": v.filename,
                                "expected_sha256": v.content_hash, "actual_sha256": actual,
                                "match": actual == v.content_hash if actual else None})
    claims_payload = []
    for c in claims:
        v = versions.get(c.version_id)
        d = documents.get(v.document_id) if v else None
        s = spans.get(c.span_id) if c.span_id else None
        claims_payload.append({
            "id": c.id, "predicate": c.predicate,
            "label": P[c.predicate].label if c.predicate in P else c.predicate,
            "subject": c.subject_text, "value": c.object_text, "unit": c.unit, "currency": c.currency,
            "verification_status": c.verification_status, "extraction_confidence": c.extraction_confidence,
            "review_status": c.review_status, "quoted_span": c.quoted_span,
            "effective_date": str(c.effective_date or ""), "valid_until": str(c.valid_until or ""),
            "document": {"id": d.id, "title": d.title, "doc_type": d.doc_type} if d else None,
            "version": {"id": v.id, "version_number": v.version_number,
                        "content_hash": v.content_hash} if v else None,
            "locator": locator_dict(s),
            "meta": c.meta or {},
        })
    claim_ids = {c.id for c in claims}
    contradictions = []
    for c in db.execute(select(Contradiction).where(Contradiction.workspace_id == case.workspace_id)).scalars():
        if c.claim_a_id in claim_ids or c.claim_b_id in claim_ids:
            contradictions.append({"id": c.id, "kind": c.kind, "state": c.state, "severity": c.severity,
                                   "blocking": c.blocking, "reasoning": c.reasoning,
                                   "claim_a_id": c.claim_a_id, "claim_b_id": c.claim_b_id})
    analyses = [{"id": a.id, "provider_label": a.provider_label, "model": a.model, "status": a.status,
                 "summary": a.summary, "recommendation": a.recommendation, "statements": a.statements,
                 "validation": a.validation, "snapshot_hash": a.snapshot_hash,
                 "created_at": a.created_at.isoformat()}
                for a in db.execute(select(Analysis).where(Analysis.decision_id == case.id)
                                    .order_by(Analysis.created_at.desc())).scalars()]
    approvals = [{"id": a.id, "action": a.action, "comment": a.comment, "user_id": a.user_id,
                  "gate_snapshot_hash": a.gate_snapshot_hash, "signature_hash": a.signature_hash,
                  "created_at": a.created_at.isoformat()}
                 for a in db.execute(select(Approval).where(Approval.decision_id == case.id)
                                     .order_by(Approval.created_at)).scalars()]
    payload = {
        "report_spec_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision": {
            "id": case.id, "title": case.title, "decision_type": case.decision_type,
            "description": case.description, "status": case.status, "gate_status": case.gate_status,
            "as_of_date": str(case.as_of_date or ""), "due_date": str(case.due_date or ""),
            "options": case.options or [], "selected_option": case.selected_option,
            "criteria": case.criteria or [], "exception_recorded": case.exception_recorded,
            "exception_reason": case.exception_reason,
        },
        "gate": {
            "gate_version": gate_result.get("gate_version"), "policy_version": gate_result.get("policy_version"),
            "status": gate_result.get("status"), "as_of": gate_result.get("as_of"),
            "result_hash": gate_result.get("result_hash"), "checks": gate_result.get("checks", []),
            "requirements": gate_result.get("requirements", []), "blocking_failures": gate_result.get("blocking_failures", []),
            "warnings": gate_result.get("warnings", []), "mcda": gate_result.get("mcda", {}),
            "summary": gate_result.get("summary", {}),
        },
        "documents": docs_payload, "claims": claims_payload, "contradictions": contradictions,
        "analyses": analyses, "approvals": approvals,
        "integrity": {"source_files": integrity_files,
                      "claims_hash": _canonical_hash({"claims": claims_payload}),
                      "audit": {"events": audit["events"], "valid": audit["valid"], "head_hash": audit["head_hash"]},
                      "snapshot_payload_hash": ""},
        "reproducibility": {"decision_id": case.id,
                            "document_version_ids": sorted(v.id for v in versions.values()),
                            "claim_ids": sorted(c.id for c in claims),
                            "gate_result_hash": gate_result.get("result_hash"),
                            "policy_version": gate_result.get("policy_version")},
    }
    payload["integrity"]["snapshot_payload_hash"] = _canonical_hash({k: v for k, v in payload.items() if k != "integrity"})
    snapshot_hash = _canonical_hash(payload)
    snapshot = Snapshot(workspace_id=case.workspace_id, decision_id=case.id, payload=payload,
                        snapshot_hash=snapshot_hash, report_spec_version=REPORT_VERSION)
    db.add(snapshot)
    db.flush()
    append_event(db, case.workspace_id, "snapshot.created", "Snapshot", snapshot.id,
                 {"decision_id": case.id, "snapshot_hash": snapshot_hash,
                  "document_versions": payload["reproducibility"]["document_version_ids"]},
                 actor=user, commit=False)
    db.commit()
    return snapshot


def verify_snapshot(snapshot: Snapshot) -> dict:
    payload = json.loads(json.dumps(snapshot.payload, default=str))
    embedded = payload.get("integrity", {}).get("snapshot_payload_hash")
    recomputed_payload = _canonical_hash({k: v for k, v in payload.items() if k != "integrity"})
    recomputed_snapshot = _canonical_hash(payload)
    return {"snapshot_id": snapshot.id, "stored_hash": snapshot.snapshot_hash,
            "recomputed_hash": recomputed_snapshot, "hash_match": recomputed_snapshot == snapshot.snapshot_hash,
            "payload_hash_match": recomputed_payload == embedded,
            "created_at": snapshot.created_at.isoformat()}


# ---------------------------------------------------------------- rendering
STYLES = getSampleStyleSheet()
TITLE = ParagraphStyle("T", parent=STYLES["Title"], fontSize=19, leading=23, spaceAfter=4)
SUB = ParagraphStyle("S", parent=STYLES["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
H1 = ParagraphStyle("H1", parent=STYLES["Heading1"], fontSize=13, leading=16, spaceBefore=10, spaceAfter=5,
                    textColor=colors.HexColor("#0f2a44"))
H2 = ParagraphStyle("H2", parent=STYLES["Heading2"], fontSize=10, leading=13, spaceBefore=7, spaceAfter=3,
                    textColor=colors.HexColor("#123c5c"))
BODY = ParagraphStyle("B", parent=STYLES["BodyText"], fontSize=8.6, leading=11.4, spaceAfter=4)
SMALL = ParagraphStyle("SM", parent=BODY, fontSize=7.2, leading=9)
CELL = ParagraphStyle("C", parent=BODY, fontSize=6.9, leading=8.4, spaceAfter=0)
CELL_BOLD = ParagraphStyle("CB", parent=CELL, fontName="Helvetica-Bold")
MONO = ParagraphStyle("M", parent=SMALL, fontName="Courier", fontSize=6.6, leading=8)


def _p(text, style=BODY) -> Paragraph:
    return Paragraph(escape(str(text or "")), style)


def _table(rows, widths, header=True, small=True) -> Table:
    data = []
    for i, row in enumerate(rows):
        data.append([_p(cell, CELL_BOLD if (header and i == 0) else CELL) for cell in row])
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8c4cf")),
             ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7eef5"))]
    table.setStyle(TableStyle(style))
    return table


def _footer(canvas, doc, decision_id: str, snapshot_hash: str):
    canvas.saveState()
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(18 * mm, 10 * mm, f"Evidence-Graph AI Auditor · decision {decision_id[:8]} · "
                                        f"snapshot {snapshot_hash[:16]}")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _render_pdf(payload: dict, path: Path, snapshot_hash: str, verification: dict) -> None:
    d = payload["decision"]
    gate = payload["gate"]
    story = []
    story.append(_p("Evidence-Graph AI Auditor", SUB))
    story.append(_p("Decision Evidence & Verification Report", TITLE))
    story.append(_p(f"{d['title']}", H2))
    story.append(_p(f"Decision type: {d['decision_type']} · Status: {d['status']} · "
                    f"Gate: {gate['status']} · As of: {d['as_of_date'] or 'n/a'} · "
                    f"Generated: {payload['generated_at']}", SUB))
    story.append(Spacer(1, 6))
    gate_rows = [["Check", "Status", "Blocking", "Detail"]]
    for c in gate["checks"]:
        gate_rows.append([c["label"], c["status"].upper(), "yes" if c["blocking"] else "no", c["detail"]])
    story.append(_p("Evidence sufficiency gate", H1))
    story.append(_p(f"Deterministic gate version {gate['gate_version']} (policy {gate['policy_version']}) returned "
                    f"<b>{gate['status']}</b>. Blocking failures: "
                    f"{', '.join(gate['blocking_failures']) or 'none'}; warnings: {', '.join(gate['warnings']) or 'none'}. "
                    f"Result hash <font name='Courier' size='7'>{gate['result_hash'][:32]}…</font>."))
    story.append(_table(gate_rows, [88, 38, 34, 300]))
    story.append(_p("Decision context and alternatives", H1))
    story.append(_p(f"<b>Context.</b> {d['description'] or 'No description supplied.'}"))
    opts = ", ".join(o.get("name", "") for o in d.get("options", [])) or "Not specified"
    story.append(_p(f"<b>Alternatives considered:</b> {opts}."))
    story.append(_p(f"<b>Selected option:</b> {d.get('selected_option') or 'not recorded'}."))
    if d.get("exception_recorded"):
        story.append(_p(f"<b>Recorded policy exception:</b> {d.get('exception_reason')}. "
                        f"The evidence gate result above is unchanged by this exception."))
    story.append(_p("Requirement coverage", H1))
    req_rows = [["Requirement", "Critical", "Result", "Missing sources / failed options"]]
    for r in gate["requirements"]:
        failed = [o["option"] for o in r.get("options", []) if not o.get("pass")]
        req_rows.append([r["label"], "yes" if r.get("critical") else "no", "PASS" if r.get("pass") else "FAIL",
                         (", ".join(r.get("missing_doc_types") or []) or "—") +
                         (f"; failed options: {', '.join(failed)}" if failed else "")])
    story.append(_table(req_rows, [120, 40, 40, 260]))
    mcda = gate.get("mcda") or {}
    if mcda.get("applicable"):
        story.append(_p("Weighted decision support (informational)", H1))
        rows = [["Criterion", "Weight", "Direction"]] + [
            [c["label"], f"{c['weight']:.2f}", c["direction"]] for c in mcda.get("criteria", [])]
        story.append(_table(rows, [200, 70, 110]))
        rank_rows = [["Rank", "Option", "Weighted score", "Criteria covered"]] + [
            [str(i + 1), r["option"], f"{r['score']:.3f}", str(r["coverage"])]
            for i, r in enumerate(mcda.get("ranking", []))]
        story.append(_table(rank_rows, [40, 180, 90, 100]))
        story.append(_p("Weighted ranking is decision support only. Approval eligibility is determined by the gate.", SMALL))
    story.append(_p("Grounded findings and recommendation", H1))
    latest = payload["analyses"][0] if payload.get("analyses") else None
    if latest:
        story.append(_p(f"<b>Provider:</b> {latest['provider_label']} · model {latest.get('model') or 'n/a'} · "
                        f"status {latest['status']}."))
        for st in latest.get("statements", []):
            marker = "" if st.get("supported") else " [FLAGGED/EXCLUDED]"
            story.append(_p(f"• {st.get('text')} — evidence: {', '.join(c[:8] for c in st.get('claim_ids', [])) or 'structural'}"
                            f"{marker}"))
        story.append(_p(f"<b>Recommendation:</b> {latest.get('recommendation')}", BODY))
    else:
        story.append(_p("No AI or deterministic analysis has been generated for this decision. "
                        "Gate results and citations remain authoritative."))
    story.append(_p("Contradictions and uncertainty", H1))
    if payload["contradictions"]:
        rows = [["Kind", "Severity", "State", "Blocking", "Reasoning"]]
        for c in payload["contradictions"]:
            rows.append([c["kind"], c["severity"], c["state"], "yes" if c["blocking"] else "no",
                         c["reasoning"][:400]])
        story.append(_table(rows, [72, 42, 55, 40, 251]))
    else:
        story.append(_p("No contradictions recorded among the claims attached to this decision."))
    story.append(PageBreak())
    story.append(_p("Claim-to-source citation table", H1))
    rows = [["Claim", "Subject / predicate", "Value", "Document", "Locator", "Status"]]
    for c in payload["claims"][:120]:
        doc_title = (c.get("document") or {}).get("title", "")
        loc = c.get("locator") or {}
        locator = (f"p.{loc.get('page')}" if loc.get("page") else
                   (f"{loc.get('sheet')}!{loc.get('cell_range')}" if loc.get("sheet") else
                    (f"¶{loc.get('paragraph_index')}" if loc.get("paragraph_index") is not None else "span")))
        rows.append([c["id"][:8], f"{c['subject'] or '—'} / {c['label']}", c["value"][:60],
                     doc_title[:48], locator, c["verification_status"]])
    story.append(_table(rows, [38, 118, 110, 112, 48, 54]))
    story.append(_p("Evidence inventory", H1))
    doc_rows = [["Document", "Type", "Current version", "SHA-256 (current)", "File integrity"]]
    files_by_version = {f["version_id"]: f for f in payload["integrity"]["source_files"]}
    for doc in payload["documents"]:
        cv = doc.get("current_version") or {}
        integ = files_by_version.get(cv.get("id"), {})
        doc_rows.append([doc["title"], doc["doc_type"], str(cv.get("version_number", "—")),
                         (cv.get("content_hash") or "")[:20] + "…",
                         "verified" if integ.get("match") else ("unavailable" if integ.get("match") is None else "MISMATCH")])
    story.append(_table(doc_rows, [150, 62, 45, 140, 70]))
    story.append(_p("Reviewer actions", H1))
    if payload["approvals"]:
        rows = [["Action", "User", "Comment", "Timestamp"]]
        for a in payload["approvals"]:
            rows.append([a["action"], a["user_id"][:12], (a["comment"] or "")[:200], a["created_at"][:19]])
        story.append(_table(rows, [60, 90, 260, 70]))
    else:
        story.append(_p("No reviewer actions recorded at snapshot time."))
    story.append(_p("Methodology", H1))
    story.append(_p(
        "Evidence documents are parsed into typed source spans with stable locators (PDF page/bbox, DOCX paragraph, "
        "spreadsheet cell, email section, text offsets). Claims are extracted by deterministic rules, normalized "
        "(units, currencies, dates), and linked to their exact source span. Contradictions are detected by comparing "
        "claims on the same entity and predicate, accounting for scope, overlapping validity windows, units, currencies "
        "and supersession. The evidence sufficiency gate evaluates mandatory sources, requirement coverage, citation "
        "resolution, blocking contradictions, freshness, data quality, policy constraints and reviewer attestations. "
        "The gate is deterministic application logic; no language model can change its outcome."))
    story.append(_p("Limitations", H1))
    story.append(_p(
        "Rule-based extraction targets explicitly stated business facts and may miss implicitly stated or heavily "
        "paraphrased evidence. OCR is not enabled in this deployment, so scanned PDFs without a text layer cannot be "
        "audited. The default local embedder is a deterministic lexical hashing model rather than a neural embedding "
        "model; retrieval quality can be improved by configuring an embedding provider. Cross-currency comparison "
        "requires an approved FX basis and is intentionally not automated. The weighted ranking is decision support, "
        "not an approval. Synthetic data is fictional."))
    story.append(_p("Reproducibility and integrity", H1))
    story.append(_p(f"Snapshot ID: <font name='Courier'>{snapshot_hash[:32]}…</font>"))
    story.append(_p(f"Snapshot hash: <font name='Courier' size='6.5'>{snapshot_hash}</font>", SMALL))
    story.append(_p(f"Gate result hash: <font name='Courier'>{gate['result_hash']}</font>", SMALL))
    story.append(_p(f"Claims hash: <font name='Courier'>{payload['integrity']['claims_hash']}</font>", SMALL))
    story.append(_p(f"Audit chain events: {payload['integrity']['audit']['events']}; "
                    f"chain valid: {payload['integrity']['audit']['valid']}; head: "
                    f"<font name='Courier'>{payload['integrity']['audit']['head_hash'][:32]}…</font>", SMALL))
    story.append(_p(f"Document versions: {', '.join(v[:8] for v in payload['reproducibility']['document_version_ids'])}", SMALL))
    story.append(_p(f"Snapshot verification at render time: hash match = {verification.get('hash_match')}, "
                    f"payload hash match = {verification.get('payload_hash_match')}.", SMALL))
    story.append(_p("Source-file hash verification: " +
                    "; ".join(f"{f['filename']}: {'verified' if f['match'] else 'unavailable' if f['match'] is None else 'MISMATCH'}"
                              for f in payload["integrity"]["source_files"]), SMALL))
    path.parent.mkdir(parents=True, exist_ok=True)
    d_id = payload["decision"]["id"]
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm, title="Decision Evidence Report",
                            author="Evidence-Graph AI Auditor")
    doc.build(story, onFirstPage=lambda c, d: _footer(c, d, d_id, snapshot_hash),
              onLaterPages=lambda c, d: _footer(c, d, d_id, snapshot_hash))


def _render_json(payload: dict, snapshot_hash: str, verification: dict) -> bytes:
    body = {"report_spec_version": REPORT_VERSION, "snapshot": payload,
            "snapshot_verification": verification,
            "report_hash": _canonical_hash({"payload": payload, "verification": verification})}
    return json.dumps(body, indent=2, sort_keys=True).encode()


def _render_manifest(payload: dict) -> bytes:
    manifest = {
        "report_spec_version": REPORT_VERSION,
        "generated_at": payload["generated_at"],
        "decision": payload["decision"],
        "gate": {k: payload["gate"][k] for k in ("gate_version", "policy_version", "status", "result_hash")},
        "documents": [{"id": d["id"], "title": d["title"], "current_version": d["current_version"],
                       "versions": d["versions"]} for d in payload["documents"]],
        "claims": [{"id": c["id"], "predicate": c["predicate"], "value": c["value"],
                    "verification_status": c["verification_status"], "locator": c["locator"],
                    "version_id": (c.get("version") or {}).get("id")} for c in payload["claims"]],
        "integrity": payload["integrity"], "reproducibility": payload["reproducibility"],
    }
    manifest["manifest_hash"] = _canonical_hash(manifest)
    return json.dumps(manifest, indent=2, sort_keys=True).encode()


def _render_claims_csv(payload: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["claim_id", "predicate", "subject", "value", "unit", "currency", "verification_status",
                     "confidence", "document", "version_id", "locator_type", "page", "sheet", "cell",
                     "paragraph", "quoted_span"])
    for c in payload["claims"]:
        loc = c.get("locator") or {}
        writer.writerow([c["id"], c["predicate"], c["subject"], c["value"], c.get("unit") or "",
                         c.get("currency") or "", c["verification_status"], c["extraction_confidence"],
                         (c.get("document") or {}).get("title", ""), (c.get("version") or {}).get("id", ""),
                         loc.get("type", ""), loc.get("page", ""), loc.get("sheet", ""),
                         loc.get("cell_range", ""), loc.get("paragraph_index", ""), c["quoted_span"]])
    return buf.getvalue().encode()


def render_report_file(kind: str, payload: dict, snapshot_hash: str, verification: dict, path: Path) -> bytes:
    if kind == "pdf":
        _render_pdf(payload, path, snapshot_hash, verification)
        return path.read_bytes()
    if kind == "json":
        return _render_json(payload, snapshot_hash, verification)
    if kind == "manifest":
        return _render_manifest(payload)
    if kind == "csv":
        return _render_claims_csv(payload)
    raise ValueError(f"Unsupported report kind '{kind}'")


def generate_report(db: Session, case: DecisionCase, kind: str, user=None,
                    snapshot: Snapshot | None = None) -> ReportRecord:
    if kind not in {"pdf", "json", "manifest", "csv"}:
        raise ValueError("Report kind must be pdf, json, manifest or csv")
    snapshot = snapshot or create_snapshot(db, case, user)
    verification = verify_snapshot(snapshot)
    if not verification["hash_match"]:
        raise ValueError("Snapshot hash verification failed; refusing to export untrusted evidence")
    settings = get_settings()
    filename = f"decision-{case.id[:8]}-{kind}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.{kind}"
    path = settings.report_dir / case.workspace_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_report_file(kind, snapshot.payload, snapshot.snapshot_hash, verification, path)
    if kind != "pdf":
        path.write_bytes(content)
    sha = _sha256_bytes(content)
    record = ReportRecord(workspace_id=case.workspace_id, decision_id=case.id, snapshot_id=snapshot.id,
                          kind=kind, filename=filename, path=str(path), sha256=sha,
                          created_by=user.id if user else None)
    db.add(record)
    db.flush()  # assign the report id before sealing the audit event
    append_event(db, case.workspace_id, "report.generated", "Report", record.id,
                 {"decision_id": case.id, "kind": kind, "snapshot_hash": snapshot.snapshot_hash,
                  "sha256": sha}, actor=user, commit=False)
    db.commit()
    return record


def regenerate_report(db: Session, record: ReportRecord, user=None) -> tuple[ReportRecord, dict]:
    snapshot = db.get(Snapshot, record.snapshot_id)
    if not snapshot:
        raise ValueError("The evidence snapshot for this report is missing; cannot reproduce")
    verification = verify_snapshot(snapshot)
    if not verification["hash_match"]:
        raise ValueError("Snapshot integrity verification failed; refusing to regenerate")
    case = db.get(DecisionCase, record.decision_id)
    settings = get_settings()
    filename = f"decision-{case.id[:8]}-{record.kind}-regenerated-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.{record.kind}"
    path = settings.report_dir / case.workspace_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_report_file(record.kind, snapshot.payload, snapshot.snapshot_hash, verification, path)
    if record.kind != "pdf":
        path.write_bytes(content)
    sha = _sha256_bytes(content)
    new_record = ReportRecord(workspace_id=record.workspace_id, decision_id=record.decision_id,
                              snapshot_id=snapshot.id, kind=record.kind, filename=filename,
                              path=str(path), sha256=sha, created_by=user.id if user else None)
    db.add(new_record)
    db.flush()  # the audit event seals the row id; it must exist before hashing
    append_event(db, record.workspace_id, "report.regenerated", "Report", new_record.id,
                 {"original_report": record.id, "snapshot_id": snapshot.id, "sha256": sha}, actor=user,
                 commit=False)
    db.commit()
    return new_record, verification


def export_claims_csv(db: Session, workspace_id: str) -> bytes:
    claims = db.execute(select(Claim).where(Claim.workspace_id == workspace_id)).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["claim_id", "predicate", "subject", "object", "verification_status", "confidence",
                "document_id", "version_id", "document_title"])
    for c in claims:
        version = db.get(DocumentVersion, c.version_id)
        doc = db.get(Document, version.document_id) if version else None
        w.writerow([c.id, c.predicate, c.subject_text, c.object_text, c.verification_status,
                    c.extraction_confidence, doc.id if doc else "", c.version_id, doc.title if doc else ""])
    return buf.getvalue().encode()
