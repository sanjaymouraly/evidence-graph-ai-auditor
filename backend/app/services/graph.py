"""Persisted evidence graph projection, traversal and graph-native queries."""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Analysis, Approval, Claim, Contradiction, DecisionCase, DecisionDocument, Document,
    DocumentVersion, Edge, Entity, SourceSpan, User,
)
from app.parsers.base import find_phrase_bbox
from app.services.gate import decision_claims
from app.services.graph_store import NetworkXGraphStore
from app.services.predicates import P

log = logging.getLogger(__name__)
NODE_TYPES = {"Document", "DocumentVersion", "SourceSpan", "Claim", "Entity", "Requirement",
              "Risk", "Contradiction", "Decision", "Recommendation", "Reviewer"}
RELATIONS = {"SUPPORTS", "CONTRADICTS", "DERIVED_FROM", "MENTIONS", "SUPERSEDES", "REQUIRES",
             "DEPENDS_ON", "INVALIDATES", "REVIEWED_BY", "INFORMS_DECISION", "VERSION_OF", "PART_OF",
             "USES_EVIDENCE"}


def _truncate(text: str, n: int = 90) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _node(node_type: str, node_id: str, label: str, status: str = "", meta: dict | None = None) -> dict:
    return {"key": f"{node_type}:{node_id}", "id": node_id, "type": node_type,
            "label": _truncate(label), "status": status, "meta": meta or {}}


def add_edge_row(db: Session, workspace_id: str, source_type: str, source_id: str, target_type: str,
                 target_id: str, relation: str, justification: str = "", provenance: dict | None = None,
                 confidence: float = 1.0, created_by: str = "system") -> Edge:
    existing = db.execute(select(Edge).where(
        Edge.workspace_id == workspace_id, Edge.source_type == source_type, Edge.source_id == source_id,
        Edge.target_type == target_type, Edge.target_id == target_id, Edge.relation == relation,
    )).scalar_one_or_none()
    if existing:
        existing.status = "active"
        return existing
    edge = Edge(workspace_id=workspace_id, source_type=source_type, source_id=source_id,
                target_type=target_type, target_id=target_id, relation=relation,
                justification=justification, provenance=provenance or {}, confidence=confidence,
                created_by=created_by)
    db.add(edge)
    db.flush()
    return edge


def sync_decision_evidence_edges(db: Session, case: DecisionCase) -> None:
    """Maintain Claim INFORMS_DECISION edges for the current versions attached to a case."""
    claims, versions, _docs, _spans = decision_claims(db, case)
    current_ids = {c.id for c in claims}
    existing = db.execute(select(Edge).where(Edge.workspace_id == case.workspace_id,
                                             Edge.relation == "INFORMS_DECISION",
                                             Edge.target_type == "Decision", Edge.target_id == case.id)).scalars().all()
    for edge in existing:
        if edge.source_type == "Claim" and edge.source_id not in current_ids:
            edge.status = "invalidated"
    for claim in claims:
        add_edge_row(db, case.workspace_id, "Claim", claim.id, "Decision", case.id, "INFORMS_DECISION",
                     justification="Claim belongs to a current evidence document attached to this decision",
                     provenance={"decision_id": case.id, "version_id": claim.version_id},
                     created_by="decision_service")
    db.flush()


def _load_base(db: Session, workspace_id: str) -> tuple[dict[str, dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add(n: dict):
        nodes[n["key"]] = n

    docs = db.execute(select(Document).where(Document.workspace_id == workspace_id)).scalars().all()
    for d in docs:
        add(_node("Document", d.id, d.title, d.doc_type, {"doc_key": d.doc_key, "doc_type": d.doc_type}))
    versions = db.execute(select(DocumentVersion).where(DocumentVersion.workspace_id == workspace_id)).scalars().all()
    for v in versions:
        add(_node("DocumentVersion", v.id, f"{v.filename} · v{v.version_number}", v.status,
                  {"version_number": v.version_number, "is_current": v.is_current,
                   "content_hash": v.content_hash[:16], "effective_date": str(v.effective_date or ""),
                   "valid_until": str(v.valid_until or ""), "document_id": v.document_id}))
        edges.append({"source": f"DocumentVersion:{v.id}", "target": f"Document:{v.document_id}",
                      "relation": "VERSION_OF", "justification": "Document version instance",
                      "confidence": 1.0, "provenance": {}, "status": "active", "derived": True})
    claims = db.execute(select(Claim).where(Claim.workspace_id == workspace_id)).scalars().all()
    for c in claims:
        label = f"{c.subject_text or 'Unbound'} · {P[c.predicate].label if c.predicate in P else c.predicate}: {c.object_text}"
        add(_node("Claim", c.id, label, c.verification_status,
                  {"predicate": c.predicate, "value": c.object_text, "unit": c.unit, "currency": c.currency,
                   "confidence": c.extraction_confidence, "version_id": c.version_id, "span_id": c.span_id,
                   "claim_key": c.claim_key, "review_status": c.review_status,
                   "effective_date": str(c.effective_date or ""), "valid_until": str(c.valid_until or ""),
                   "verification_reason": (c.meta or {}).get("verification_reason", "")}))
        if c.span_id:
            edges.append({"source": f"Claim:{c.id}", "target": f"SourceSpan:{c.span_id}",
                          "relation": "DERIVED_FROM", "justification": "Extracted from source span",
                          "confidence": c.extraction_confidence, "provenance": {}, "status": "active",
                          "derived": True})
        if c.entity_id:
            edges.append({"source": f"Claim:{c.id}", "target": f"Entity:{c.entity_id}", "relation": "MENTIONS",
                          "justification": "Resolved subject entity", "confidence": 0.9,
                          "provenance": {}, "status": "active", "derived": True})
    span_ids = {c.span_id for c in claims if c.span_id}
    spans = db.execute(select(SourceSpan).where(SourceSpan.id.in_(span_ids))).scalars().all() if span_ids else []
    for s in spans:
        locator = (f"p.{s.page}" if s.page else (f"{s.sheet}!{s.cell_range}" if s.sheet else
                   (f"¶{s.paragraph_index}" if s.paragraph_index is not None else f"chars {s.char_start}-{s.char_end}")))
        add(_node("SourceSpan", s.id, f"{locator}: {s.text}", "injection" if (s.meta or {}).get("injection") else "",
                  {"locator": locator, "page": s.page, "sheet": s.sheet, "cell_range": s.cell_range,
                   "char_start": s.char_start, "char_end": s.char_end, "version_id": s.version_id,
                   "text": _truncate(s.text, 240)}))
        edges.append({"source": f"SourceSpan:{s.id}", "target": f"DocumentVersion:{s.version_id}",
                      "relation": "PART_OF", "justification": "Span belongs to document version",
                      "confidence": 1.0, "provenance": {}, "status": "active", "derived": True})
    for e in db.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars():
        add(_node("Entity", e.id, e.canonical_name, e.entity_type, {"aliases": e.aliases or []}))
    for c in db.execute(select(Contradiction).where(Contradiction.workspace_id == workspace_id)).scalars():
        add(_node("Contradiction", c.id, f"{c.kind.replace('_', ' ')} · {c.severity}", c.state,
                  {"claim_a_id": c.claim_a_id, "claim_b_id": c.claim_b_id, "blocking": c.blocking,
                   "reasoning": _truncate(c.reasoning, 300), "confidence": c.confidence}))
        for cid in (c.claim_a_id, c.claim_b_id):
            edges.append({"source": f"Contradiction:{c.id}", "target": f"Claim:{cid}", "relation": "CONTRADICTS",
                          "justification": c.kind, "confidence": c.confidence, "provenance": {},
                          "status": "active", "derived": True})
    cases = db.execute(select(DecisionCase).where(DecisionCase.workspace_id == workspace_id)).scalars().all()
    for case in cases:
        add(_node("Decision", case.id, case.title, case.status,
                  {"decision_type": case.decision_type, "gate_status": case.gate_status,
                   "gate_result_hash": (case.gate_result or {}).get("result_hash", ""),
                   "selected_option": case.selected_option, "gate_evaluated_at": str(case.gate_evaluated_at or "")}))
        for dd in db.execute(select(DecisionDocument).where(DecisionDocument.decision_id == case.id)).scalars():
            edges.append({"source": f"Decision:{case.id}", "target": f"Document:{dd.document_id}",
                          "relation": "USES_EVIDENCE", "justification": "Document attached to decision",
                          "confidence": 1.0, "provenance": {"decision_id": case.id}, "status": "active",
                          "derived": True})
        gate_requirements = (case.gate_result or {}).get("requirements") or []
        for req in gate_requirements:
            rid = f"{case.id}:{req.get('code')}"
            add(_node("Requirement", rid, req.get("label", req.get("code", "Requirement")),
                      "pass" if req.get("pass") else "fail",
                      {"critical": req.get("critical"), "missing_doc_types": req.get("missing_doc_types", []),
                       "min_claims": req.get("min_claims"), "decision_id": case.id}))
            edges.append({"source": f"Decision:{case.id}", "target": f"Requirement:{rid}", "relation": "REQUIRES",
                          "justification": f"Policy requirement {req.get('code')}", "confidence": 1.0,
                          "provenance": {"gate_version": (case.gate_result or {}).get("gate_version")},
                          "status": "active", "derived": True})
            for cid in req.get("matched_claim_ids", [])[:60]:
                if f"Claim:{cid}" in nodes:
                    edges.append({"source": f"Claim:{cid}", "target": f"Requirement:{rid}", "relation": "SUPPORTS",
                                  "justification": "Claim counted toward requirement coverage",
                                  "confidence": 1.0, "provenance": {"requirement": req.get("code")},
                                  "status": "active", "derived": True})
        reviews = db.execute(select(Approval, User).join(User, Approval.user_id == User.id)
                             .where(Approval.decision_id == case.id)).all()
        for approval, usr in reviews:
            add(_node("Reviewer", usr.id, usr.name or usr.email, usr.role,
                      {"role": usr.role, "email": usr.email}))
            edges.append({"source": f"Decision:{case.id}", "target": f"Reviewer:{usr.id}", "relation": "REVIEWED_BY",
                          "justification": f"{approval.action}: {approval.comment or 'no comment'}",
                          "confidence": 1.0, "provenance": {"approval_id": approval.id,
                                                            "action": approval.action},
                          "status": "active", "derived": True})
        latest_analysis = db.execute(select(Analysis).where(Analysis.decision_id == case.id)
                                     .order_by(Analysis.created_at.desc())).scalars().first()
        if latest_analysis:
            add(_node("Recommendation", latest_analysis.id,
                      f"AI analysis · {latest_analysis.provider_label}", latest_analysis.status,
                      {"model": latest_analysis.model, "created_at": str(latest_analysis.created_at),
                       "statement_count": len(latest_analysis.statements or []),
                       "unsupported": len([s for s in (latest_analysis.statements or [])
                                           if not s.get("supported") and not s.get("excluded")])}))
            edges.append({"source": f"Recommendation:{latest_analysis.id}", "target": f"Decision:{case.id}",
                          "relation": "INFORMS_DECISION", "justification": "Generated analysis (never an approval)",
                          "confidence": 1.0, "provenance": {"analysis_id": latest_analysis.id},
                          "status": "active", "derived": True})
            for st in latest_analysis.statements or []:
                for cid in st.get("claim_ids", []):
                    if f"Claim:{cid}" in nodes and st.get("supported"):
                        edges.append({"source": f"Claim:{cid}", "target": f"Recommendation:{latest_analysis.id}",
                                      "relation": "SUPPORTS", "justification": "Statement cites this claim",
                                      "confidence": 1.0, "provenance": {"statement_id": st.get("id")},
                                      "status": "active", "derived": True})
    for e in db.execute(select(Edge).where(Edge.workspace_id == workspace_id)).scalars():
        if e.source_type == "Claim" and e.target_type == "Claim":
            edges.append({"source": f"Claim:{e.source_id}", "target": f"Claim:{e.target_id}",
                          "relation": e.relation, "justification": e.justification,
                          "confidence": e.confidence, "provenance": e.provenance or {}, "status": e.status,
                          "derived": False, "edge_id": e.id})
        elif e.source_type == "DocumentVersion" and e.target_type == "DocumentVersion":
            edges.append({"source": f"DocumentVersion:{e.source_id}", "target": f"DocumentVersion:{e.target_id}",
                          "relation": e.relation, "justification": e.justification,
                          "confidence": e.confidence, "provenance": e.provenance or {}, "status": e.status,
                          "derived": False, "edge_id": e.id})
        elif e.source_type == "Claim" and e.target_type == "Decision":
            if f"Claim:{e.source_id}" in nodes and f"Decision:{e.target_id}" in nodes:
                edges.append({"source": f"Claim:{e.source_id}", "target": f"Decision:{e.target_id}",
                              "relation": "INFORMS_DECISION", "justification": e.justification,
                              "confidence": e.confidence, "provenance": e.provenance or {}, "status": e.status,
                              "derived": False, "edge_id": e.id})
    # Deduplicate edges on (source, target, relation)
    seen = set()
    unique_edges = []
    for ed in edges:
        key = (ed["source"], ed["target"], ed["relation"])
        if key in seen:
            continue
        seen.add(key)
        unique_edges.append(ed)
    for ed in unique_edges:
        if ed["source"] not in nodes or ed["target"] not in nodes:
            continue
        ed.setdefault("key", hashlib.sha256(f"{ed['source']}|{ed['target']}|{ed['relation']}".encode()).hexdigest()[:16])
    unique_edges = [ed for ed in unique_edges if ed["source"] in nodes and ed["target"] in nodes]
    return nodes, unique_edges


def build_graph(db: Session, workspace_id: str, *, node_types: set[str] | None = None,
                relations: set[str] | None = None, q: str | None = None,
                focus_key: str | None = None, depth: int = 2, limit: int = 800,
                decision_id: str | None = None) -> dict:
    nodes, edges = _load_base(db, workspace_id)
    store = NetworkXGraphStore()
    for key, node in nodes.items():
        store.add_node(key, **{k: v for k, v in node.items() if k != "key"})
    for ed in edges:
        store.add_edge(ed["source"], ed["target"], relation=ed["relation"],
                       status=ed["status"], key=ed["key"])
    allowed_types = node_types or (NODE_TYPES - {"SourceSpan"})
    allowed_relations = relations or RELATIONS
    if decision_id and f"Decision:{decision_id}" in nodes:
        focus_key = f"Decision:{decision_id}"
        depth = max(depth, 3)
    if focus_key and focus_key in nodes:
        keep = store.subgraph_within([focus_key], depth)
        edges = [ed for ed in edges if ed["source"] in keep and ed["target"] in keep]
    selected_edges = [ed for ed in edges if ed["relation"] in allowed_relations
                      and ed["status"] == "active"]
    connected = {ed["source"] for ed in selected_edges} | {ed["target"] for ed in selected_edges}
    if q:
        ql = q.lower()
        keep_nodes = {k for k, n in nodes.items() if ql in (n["label"] + " " + n["type"] + " " +
                                                             json.dumps(n["meta"], default=str)).lower()}
        selected_edges = [ed for ed in selected_edges if ed["source"] in keep_nodes or ed["target"] in keep_nodes]
        connected = {ed["source"] for ed in selected_edges} | {ed["target"] for ed in selected_edges}
        keep_nodes |= connected
    else:
        keep_nodes = connected if (focus_key or decision_id) else set(nodes.keys())
        if not (focus_key or decision_id) and not selected_edges:
            keep_nodes = set(nodes.keys())
    out_nodes = [n for k, n in nodes.items() if n["type"] in allowed_types and k in keep_nodes]
    truncated = len(out_nodes) > limit
    if truncated:
        out_nodes = out_nodes[:limit]
        keep = {n["key"] for n in out_nodes}
        selected_edges = [ed for ed in selected_edges if ed["source"] in keep and ed["target"] in keep]
    return {
        "nodes": out_nodes, "edges": selected_edges, "truncated": truncated,
        "counts": {"nodes": len(out_nodes), "edges": len(selected_edges)},
        "node_types": sorted(allowed_types), "relations": sorted(allowed_relations),
    }


def graph_stats(db: Session, workspace_id: str, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    claims = db.execute(select(Claim).where(Claim.workspace_id == workspace_id)).scalars().all()
    versions = db.execute(select(DocumentVersion).where(DocumentVersion.workspace_id == workspace_id)).scalars().all()
    docs = db.execute(select(Document).where(Document.workspace_id == workspace_id)).scalars().all()
    contradictions = db.execute(select(Contradiction).where(Contradiction.workspace_id == workspace_id)).scalars().all()
    cases = db.execute(select(DecisionCase).where(DecisionCase.workspace_id == workspace_id)).scalars().all()
    claim_status = Counter(c.verification_status for c in claims)
    contradictions_state = Counter(c.state for c in contradictions)
    decision_status = Counter(c.status for c in cases)
    stale_docs = [v for v in versions if v.is_current and v.valid_until and v.valid_until < as_of]
    superseded = [v for v in versions if not v.is_current]
    verified_required = sum(1 for c in claims if c.verification_status == "verified")
    with_citations = sum(1 for c in claims if c.span_id)
    return {
        "documents": len(docs), "versions": len(versions), "current_versions": sum(1 for v in versions if v.is_current),
        "claims": len(claims), "claim_status": dict(claim_status),
        "citation_coverage": round(with_citations / len(claims), 4) if claims else 0,
        "verified_rate": round(verified_required / len(claims), 4) if claims else 0,
        "contradictions": len(contradictions), "contradiction_state": dict(contradictions_state),
        "blocking_contradictions": sum(1 for c in contradictions if c.state == "confirmed" and c.blocking),
        "decisions": len(cases), "decision_status": dict(decision_status),
        "expired_documents": len(stale_docs), "superseded_versions": len(superseded),
        "documents_by_type": dict(Counter(d.doc_type for d in docs)),
        "as_of": as_of.isoformat(),
    }


def trace_claim(db: Session, workspace_id: str, claim_id: str) -> dict | None:
    claim = db.get(Claim, claim_id)
    if not claim or claim.workspace_id != workspace_id:
        return None
    span = db.get(SourceSpan, claim.span_id) if claim.span_id else None
    version = db.get(DocumentVersion, claim.version_id)
    document = db.get(Document, version.document_id) if version else None
    contradictions = db.execute(select(Contradiction).where(
        or_(Contradiction.claim_a_id == claim.id, Contradiction.claim_b_id == claim.id))).scalars().all()
    # DISTINCT over the whole entity is not portable: PostgreSQL cannot compare the JSON
    # columns for DISTINCT (SQLite silently permits it). Deduplicate on the id instead.
    decisions = db.execute(
        select(DecisionCase).where(DecisionCase.id.in_(
            select(DecisionDocument.decision_id)
            .where(DecisionDocument.document_id == document.id).distinct()))
    ).scalars().all() if document else []
    return {
        "claim": {"id": claim.id, "predicate": claim.predicate, "object": claim.object_text,
                  "verification_status": claim.verification_status, "confidence": claim.extraction_confidence,
                  "quoted_span": claim.quoted_span, "meta": claim.meta},
        "span": {"id": span.id, "locator_type": span.locator_type, "page": span.page,
                 "bbox": (find_phrase_bbox((span.meta or {}).get("line_boxes", []), claim.quoted_span)
                          or span.bbox) if span else None,
                 "sheet": span.sheet, "cell_range": span.cell_range, "char_start": span.char_start,
                 "char_end": span.char_end, "text": span.text} if span else None,
        "version": {"id": version.id, "number": version.version_number, "filename": version.filename,
                    "content_hash": version.content_hash, "is_current": version.is_current,
                    "effective_date": str(version.effective_date or ""),
                    "valid_until": str(version.valid_until or "")} if version else None,
        "document": {"id": document.id, "title": document.title, "doc_type": document.doc_type} if document else None,
        "contradictions": [{"id": c.id, "kind": c.kind, "state": c.state, "severity": c.severity}
                           for c in contradictions],
        "decisions": [{"id": d.id, "title": d.title, "status": d.status} for d in decisions],
    }


def impact_of_version(db: Session, workspace_id: str, version_id: str) -> dict | None:
    version = db.get(DocumentVersion, version_id)
    if not version or version.workspace_id != workspace_id:
        return None
    document = db.get(Document, version.document_id)
    claims = db.execute(select(Claim).where(Claim.version_id == version.id)).scalars().all()
    claim_ids = {c.id for c in claims}
    contradictions = db.execute(select(Contradiction).where(
        or_(Contradiction.claim_a_id.in_(claim_ids), Contradiction.claim_b_id.in_(claim_ids)))
    ).scalars().all() if claim_ids else []
    decisions = db.execute(
        select(DecisionCase).where(DecisionCase.id.in_(
            select(DecisionDocument.decision_id)
            .where(DecisionDocument.document_id == version.document_id).distinct()))
    ).scalars().all()
    later_versions = db.execute(select(DocumentVersion).where(
        DocumentVersion.document_id == version.document_id,
        DocumentVersion.version_number > version.version_number)).scalars().all()
    invalidations = db.execute(select(Edge).where(Edge.workspace_id == workspace_id,
                                                   Edge.relation == "INVALIDATES",
                                                   Edge.source_type == "Claim")).scalars().all()
    return {
        "version_id": version.id, "document_id": version.document_id,
        "document_title": document.title if document else "",
        "is_current": version.is_current, "claims": len(claims),
        "claim_ids": sorted(claim_ids)[:100],
        "contradictions": [{"id": c.id, "kind": c.kind, "state": c.state, "severity": c.severity,
                            "blocking": c.blocking} for c in contradictions],
        "decisions": [{"id": d.id, "title": d.title, "status": d.status, "gate_status": d.gate_status}
                      for d in decisions],
        "superseded_by": [{"id": v.id, "version": v.version_number} for v in later_versions],
        "invalidates_claims": [{"edge_id": e.id, "new_claim": e.source_id, "prior_claim": e.target_id,
                                "justification": e.justification}
                               for e in invalidations if e.source_id in claim_ids or e.target_id in claim_ids],
    }


def queries(db: Session, workspace_id: str, name: str, **params) -> dict:
    if name == "evidence_for_decision":
        case = db.get(DecisionCase, params.get("decision_id", ""))
        if not case or case.workspace_id != workspace_id:
            raise ValueError("Decision not found")
        claims, versions, documents, _ = decision_claims(db, case)
        return {"decision": {"id": case.id, "title": case.title, "status": case.status},
                "evidence": [{"claim_id": c.id, "predicate": c.predicate, "object": c.object_text,
                              "verification_status": c.verification_status, "version_id": c.version_id,
                              "document": documents[versions[c.version_id].document_id].title
                              if c.version_id in versions and versions[c.version_id].document_id in documents else ""}
                             for c in claims]}
    if name == "contradictory_claims_for_entity":
        entity = db.get(Entity, params.get("entity_id", ""))
        if not entity or entity.workspace_id != workspace_id:
            raise ValueError("Entity not found")
        claim_ids = {c.id for c in db.execute(select(Claim).where(Claim.entity_id == entity.id)).scalars()}
        contradictions = db.execute(select(Contradiction).where(Contradiction.workspace_id == workspace_id)).scalars().all()
        relevant = [c for c in contradictions if c.claim_a_id in claim_ids or c.claim_b_id in claim_ids]
        return {"entity": {"id": entity.id, "name": entity.canonical_name},
                "contradictions": [{"id": c.id, "kind": c.kind, "state": c.state, "severity": c.severity,
                                    "claim_a_id": c.claim_a_id, "claim_b_id": c.claim_b_id,
                                    "reasoning": c.reasoning} for c in relevant]}
    if name == "decisions_on_obsolete_records":
        cases = db.execute(select(DecisionCase).where(DecisionCase.workspace_id == workspace_id)).scalars().all()
        out = []
        for case in cases:
            result = case.gate_result or {}
            fresh = next((c for c in result.get("checks", []) if c.get("code") == "evidence_freshness"), None)
            if fresh and fresh.get("status") in {"fail", "warn"}:
                out.append({"decision_id": case.id, "title": case.title, "status": case.status,
                            "freshness_status": fresh.get("status"), "detail": fresh.get("detail")})
        return {"decisions": out}
    if name == "missing_requirement_coverage":
        case = db.get(DecisionCase, params.get("decision_id", ""))
        if not case or case.workspace_id != workspace_id:
            raise ValueError("Decision not found")
        reqs = (case.gate_result or {}).get("requirements", [])
        return {"decision_id": case.id, "missing": [
            {"code": r.get("code"), "label": r.get("label"), "critical": r.get("critical"),
             "missing_doc_types": r.get("missing_doc_types"),
             "failed_options": [o["option"] for o in r.get("options", []) if not o.get("pass")]}
            for r in reqs if not r.get("pass")]}
    if name == "trace_recommendation":
        analysis = db.execute(select(Analysis).where(Analysis.workspace_id == workspace_id)
                              .order_by(Analysis.created_at.desc())).scalars().first()
        if params.get("analysis_id"):
            analysis = db.get(Analysis, params["analysis_id"])
        if not analysis or analysis.workspace_id != workspace_id:
            raise ValueError("Analysis not found")
        traces = []
        for st in analysis.statements or []:
            for cid in st.get("claim_ids", []):
                t = trace_claim(db, workspace_id, cid)
                if t:
                    traces.append({"statement_id": st.get("id"), "text": st.get("text"), "trace": t})
        return {"analysis_id": analysis.id, "decision_id": analysis.decision_id, "traces": traces}
    raise ValueError("Unknown graph query")
