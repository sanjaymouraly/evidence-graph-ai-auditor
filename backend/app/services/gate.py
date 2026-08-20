"""Deterministic evidence-sufficiency gate.

The LLM can never change gate outcomes. Every check is computed from persisted
claims, provenance spans, document versions, policy configuration and approvals.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Analysis, Approval, Claim, Contradiction, DecisionCase, DecisionDocument, Document,
    DocumentVersion, Entity, GateCheck, SourceSpan, User, Workspace,
)
from app.services.audit import append_event
from app.services.claims import citation_resolves
from app.services.entities import entity_key

log = logging.getLogger(__name__)
GATE_VERSION = "gate-1.2"

DEFAULT_POLICY: dict = {
    "version": "policy-2026.1",
    "freshness_days": {"quote": 180, "contract": 730, "certificate": 400, "risk_register": 365,
                       "spec": 365, "technical_assessment": 365, "schedule": 180, "policy": 1095,
                       "invoice": 365, "requirements": 365, "decision_record": 730,
                       "spreadsheet": 180, "email": 180, "generic": 365},
    "approvals_required": {"procurement": 1, "engineering_change": 1, "project_approval": 1,
                           "policy_compliance": 1},
    "min_options": {"procurement": 2, "engineering_change": 1, "project_approval": 1, "policy_compliance": 1},
    "requirements": {
        "procurement": [
            {"code": "price_evidence", "label": "Price evidence for each option", "predicates": ["unit_price", "price"],
             "per_option": True, "min_claims": 1, "critical": True, "required_doc_types": ["quote"], "max_age_days": 180},
            {"code": "delivery_commitment", "label": "Delivery commitment for each option",
             "predicates": ["delivery_days", "delivery_date"], "per_option": True, "min_claims": 1,
             "critical": True, "max_age_days": 180},
            {"code": "certification_current", "label": "Current certification for each option",
             "predicates": ["certification"], "source_doc_types": ["certificate"],
             "per_option": True, "min_claims": 1, "critical": True,
             "required_doc_types": ["certificate"], "max_age_days": 400, "require_current": True},
            {"code": "payment_terms", "label": "Payment terms documented", "predicates": ["payment_terms"],
             "per_option": False, "min_claims": 1, "critical": False, "max_age_days": 365},
            {"code": "risk_assessment", "label": "Risk assessment on file",
             "predicates": ["risk_level", "requirement_statement"], "per_option": False, "min_claims": 1,
             "critical": True, "required_doc_types": ["risk_register"], "max_age_days": 365},
            {"code": "signed_contract", "label": "Signed contract or purchase order",
             "predicates": [], "per_option": False, "min_claims": 0, "critical": True,
             "required_doc_types": ["contract"]},
        ],
        "engineering_change": [
            {"code": "change_scope", "label": "Defined change scope / specification",
             "predicates": ["spec_resolution", "spec_ip_rating", "spec_efficiency", "spec_accuracy",
                            "spec_load_capacity", "conformance"], "per_option": False, "min_claims": 1,
             "critical": True, "required_doc_types": ["spec"], "max_age_days": 365},
            {"code": "technical_assessment", "label": "Technical assessment on file",
             "predicates": ["requirement_statement", "conformance"], "per_option": False, "min_claims": 1,
             "critical": True, "required_doc_types": ["technical_assessment"], "max_age_days": 365},
            {"code": "change_risk", "label": "Change risk assessed", "predicates": ["risk_level"],
             "per_option": False, "min_claims": 1, "critical": True, "required_doc_types": ["risk_register"],
             "max_age_days": 365},
            {"code": "test_evidence", "label": "Validation / test evidence",
             "predicates": ["conformance", "requirement_statement"], "per_option": False, "min_claims": 1,
             "critical": False, "required_doc_types": ["technical_assessment"], "max_age_days": 365},
        ],
        "project_approval": [
            {"code": "milestones", "label": "Milestone schedule", "predicates": ["milestone_date", "delivery_date"],
             "per_option": False, "min_claims": 1, "critical": True, "required_doc_types": ["schedule"],
             "max_age_days": 180},
            {"code": "budget", "label": "Budgeted cost evidence", "predicates": ["price"],
             "per_option": False, "min_claims": 1, "critical": True, "max_age_days": 180},
            {"code": "project_risk", "label": "Project risk register", "predicates": ["risk_level"],
             "per_option": False, "min_claims": 1, "critical": True, "required_doc_types": ["risk_register"],
             "max_age_days": 365},
            {"code": "status_evidence", "label": "Current status / assessment",
             "predicates": ["requirement_statement", "conformance", "spec_throughput"], "per_option": False,
             "min_claims": 1, "critical": False, "required_doc_types": ["technical_assessment"],
             "max_age_days": 365},
        ],
        "policy_compliance": [
            {"code": "policy_source", "label": "Approved policy document",
             "predicates": ["policy_statement", "requirement_statement"], "per_option": False, "min_claims": 1,
             "critical": True, "required_doc_types": ["policy"], "max_age_days": 1095},
            {"code": "attestation", "label": "Compliance attestation",
             "predicates": ["compliance_declaration", "conformance", "requirement_statement"],
             "per_option": False, "min_claims": 1, "critical": True, "max_age_days": 365},
            {"code": "review_record", "label": "Review / decision record",
             "predicates": [], "per_option": False, "min_claims": 0, "critical": True,
             "required_doc_types": ["decision_record"]},
        ],
    },
    "blocking_contradiction_severities": ["high", "critical"],
}

TERMINAL_STATUSES = {"APPROVED", "REJECTED"}
CASE_STATUSES = {"DRAFT", "EVIDENCE_INCOMPLETE", "CONTRADICTED", "REVIEW_REQUIRED",
                 "SUFFICIENTLY_SUPPORTED", "APPROVED", "REJECTED"}


def policy_for(workspace: Workspace | None) -> dict:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    overrides = ((workspace.settings or {}).get("policy") if workspace else None) or {}
    for key in ("freshness_days", "approvals_required", "min_options"):
        if isinstance(overrides.get(key), dict):
            policy[key].update(overrides[key])
    if isinstance(overrides.get("requirements"), dict):
        for dtype, reqs in overrides["requirements"].items():
            if isinstance(reqs, list) and dtype in policy["requirements"]:
                policy["requirements"][dtype] = reqs
    if isinstance(overrides.get("blocking_contradiction_severities"), list):
        policy["blocking_contradiction_severities"] = overrides["blocking_contradiction_severities"]
    return policy


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def decision_claims(db: Session, case: DecisionCase) -> tuple[list[Claim], dict[str, DocumentVersion], dict[str, Document], dict[str, SourceSpan]]:
    doc_ids = [r.document_id for r in db.execute(
        select(DecisionDocument).where(DecisionDocument.decision_id == case.id)).scalars()]
    documents = {d.id: d for d in db.execute(
        select(Document).where(Document.id.in_(doc_ids))).scalars()} if doc_ids else {}
    versions = {v.id: v for v in db.execute(
        select(DocumentVersion).where(DocumentVersion.document_id.in_(doc_ids),
                                      DocumentVersion.is_current.is_(True),
                                      DocumentVersion.status == "processed")).scalars()} if doc_ids else {}
    claims = db.execute(select(Claim).where(Claim.version_id.in_(list(versions.keys())))).scalars().all() if versions else []
    spans = {s.id: s for s in db.execute(select(SourceSpan).where(SourceSpan.version_id.in_(list(versions.keys())))).scalars()} if versions else {}
    return claims, versions, documents, spans


def resolve_options(db: Session, case: DecisionCase) -> list[dict]:
    options = list(case.options or [])
    if not options and case.subject_entity_id:
        ent = db.get(Entity, case.subject_entity_id)
        if ent:
            options = [{"name": ent.canonical_name, "entity_id": ent.id}]
    resolved = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        ent_id = opt.get("entity_id")
        ent = db.get(Entity, ent_id) if ent_id else None
        if not ent and opt.get("name"):
            for candidate in db.execute(select(Entity).where(Entity.workspace_id == case.workspace_id)).scalars():
                if entity_key(candidate.canonical_name) == entity_key(opt["name"]):
                    ent = candidate
                    break
        resolved.append({"name": (ent.canonical_name if ent else opt.get("name", "Option")),
                         "entity_id": ent.id if ent else None,
                         "manual_scores": opt.get("manual_scores") or {}})
    return resolved


def claim_is_stale(claim: Claim, version: DocumentVersion | None, as_of: date, max_age_days: int | None) -> dict:
    if version is None:
        return {"stale": True, "reason": "source version unavailable", "days": None}
    if not version.is_current:
        return {"stale": True, "reason": "source version superseded", "days": None}
    if version.valid_until and version.valid_until < as_of:
        return {"stale": True, "reason": f"document expired {version.valid_until.isoformat()}",
                "days": (as_of - version.valid_until).days}
    if claim.valid_until and claim.valid_until < as_of:
        return {"stale": True, "reason": f"evidence expired {claim.valid_until.isoformat()}",
                "days": (as_of - claim.valid_until).days}
    if claim.effective_date and claim.effective_date > as_of:
        return {"stale": True, "reason": f"evidence not yet effective ({claim.effective_date.isoformat()})",
                "days": None}
    ref = version.issued_date or version.effective_date or version.created_at.date()
    age = (as_of - ref).days
    explicit_validity = (claim.valid_until and claim.valid_until >= as_of) or \
                        (version.valid_until and version.valid_until >= as_of)
    if not explicit_validity and max_age_days is not None and age > max_age_days:
        return {"stale": True, "reason": f"older than policy window ({age} days > {max_age_days})", "days": age}
    return {"stale": False, "reason": "", "days": age}


def _option_matches_claim(option: dict, claim: Claim) -> bool:
    if option.get("entity_id") and claim.entity_id:
        return option["entity_id"] == claim.entity_id
    if option.get("entity_id") and not claim.entity_id:
        return False
    name = (option.get("name") or "").lower()
    haystack = f"{claim.subject_text} {claim.scope} {claim.object_text}".lower()
    return bool(name) and name in haystack


def evaluate_requirements(db: Session, case: DecisionCase, claims: list[Claim],
                          versions: dict[str, DocumentVersion], documents: dict[str, Document],
                          policy: dict, as_of: date) -> list[dict]:
    reqs = policy["requirements"].get(case.decision_type, [])
    options = resolve_options(db, case)
    doc_types = {(documents[v.document_id].doc_type if v.document_id in documents else "") for v in versions.values()}
    results = []
    for req in reqs:
        per_option = []
        preds = set(req.get("predicates") or [])
        pool = [c for c in claims if (not preds or c.predicate in preds) and c.review_status != "rejected"]
        allowed_sources = set(req.get("source_doc_types") or [])
        if allowed_sources:
            pool = [c for c in pool
                    if c.version_id in versions
                    and versions[c.version_id].document_id in documents
                    and documents[versions[c.version_id].document_id].doc_type in allowed_sources]
        targets = options if req.get("per_option") and options else [{"name": "Case evidence", "entity_id": None}]
        all_pass = True
        for opt in targets:
            matched = [c for c in pool if _option_matches_claim(opt, c)] if req.get("per_option") else pool
            fresh = []
            for c in matched:
                f = claim_is_stale(c, versions.get(c.version_id), as_of, req.get("max_age_days"))
                fresh.append({"claim_id": c.id, "verification_status": c.verification_status,
                              "stale": f["stale"], "reason": f["reason"], "age_days": f["days"],
                              "predicate": c.predicate, "object": c.object_text,
                              "document_id": documents[versions[c.version_id].document_id].id if c.version_id in versions and versions[c.version_id].document_id in documents else None,
                              "version_id": c.version_id})
            fresh_count = sum(1 for f in fresh if not f["stale"])
            requirement_pass = (fresh_count if req.get("require_current") else len(matched)) >= int(req.get("min_claims", 1))
            all_pass = all_pass and requirement_pass
            per_option.append({"option": opt["name"], "entity_id": opt.get("entity_id"),
                               "count": len(matched), "fresh_count": fresh_count,
                               "pass": requirement_pass, "claims": fresh})
        doc_ok = all(dt in doc_types for dt in (req.get("required_doc_types") or []))
        results.append({
            **req, "pass": all_pass and doc_ok, "documents_present": doc_ok,
            "missing_doc_types": [dt for dt in (req.get("required_doc_types") or []) if dt not in doc_types],
            "options": per_option, "matched_claim_ids": sorted({c["claim_id"] for o in per_option for c in o["claims"]}),
        })
    return results


def _criteria_for(decision_type: str) -> list[dict]:
    base = [
        {"code": "price", "label": "Price", "weight": 0.35, "direction": "minimize", "kind": "derived"},
        {"code": "delivery", "label": "Delivery lead time", "weight": 0.20, "direction": "minimize", "kind": "derived"},
        {"code": "compliance", "label": "Certification & compliance", "weight": 0.25, "direction": "maximize", "kind": "derived"},
        {"code": "risk", "label": "Assessed risk", "weight": 0.10, "direction": "minimize", "kind": "derived"},
        {"code": "warranty", "label": "Warranty", "weight": 0.10, "direction": "maximize", "kind": "derived"},
    ]
    if decision_type == "engineering_change":
        base[0] = {"code": "price", "label": "Change cost", "weight": 0.25, "direction": "minimize", "kind": "derived"}
        base[1] = {"code": "delivery", "label": "Implementation lead time", "weight": 0.20, "direction": "minimize", "kind": "derived"}
        base.append({"code": "technical_fit", "label": "Technical fit (spec coverage)", "weight": 0.15,
                     "direction": "maximize", "kind": "derived"})
    return base


def compute_mcda(db: Session, case: DecisionCase, claims: list[Claim],
                 versions: dict[str, DocumentVersion] | None = None,
                 as_of: date | None = None) -> dict:
    """Weighted option scoring from current evidence, evaluated as of the decision date.

    Evidence validity is a function of the decision's own as-of date, not of "today": a
    quotation that has since expired is still valid evidence for a decision taken while it
    was in force. Claims flagged ``unsupported`` purely because the workspace-wide
    verification pass (which runs at "now") considered them expired therefore still count
    here, provided they are fresh at ``as_of``. Rejected claims and superseded versions are
    always excluded.
    """
    options = resolve_options(db, case)
    if len(options) < 2:
        return {"applicable": False, "reason": "comparative scoring requires at least two options",
                "options": options}
    criteria = list(case.criteria or []) or _criteria_for(case.decision_type)
    versions = versions or {}
    effective_as_of = as_of or case.as_of_date or date.today()

    def usable(claim: Claim) -> bool:
        if claim.review_status == "rejected" or claim.verification_status == "rejected":
            return False
        if versions:
            if claim_is_stale(claim, versions.get(claim.version_id), effective_as_of, None)["stale"]:
                return False
        elif claim.verification_status not in {"verified", "uncertain"}:
            return False
        return True

    metrics: list[dict] = []
    for opt in options:
        oc = [c for c in claims if _option_matches_claim(opt, c) and usable(c)]
        def best(preds, mode):
            vals = [c.value_numeric for c in oc if c.predicate in preds and c.value_numeric is not None]
            if not vals:
                return None
            return min(vals) if mode == "min" else max(vals)
        risk_vals = [c.value_numeric for c in oc if c.predicate == "risk_level" and c.value_numeric is not None]
        metrics.append({
            "option": opt["name"], "entity_id": opt.get("entity_id"),
            "price": (best({"price"}, "min") if any(c.predicate == "price" for c in oc)
                      else best({"unit_price"}, "min")),
            "delivery": best({"delivery_days"}, "min"),
            "warranty": best({"warranty_days"}, "max"),
            "risk": (min(risk_vals) if risk_vals else None),
            "compliance": len([c for c in oc if c.predicate in {"certification", "conformance"}]),
            "technical_fit": len([c for c in oc if c.predicate.startswith("spec_") or c.predicate == "conformance"]),
            "manual_scores": opt.get("manual_scores") or {},
        })
    for crit in criteria:
        vals = [(m.get(crit["code"]), m) for m in metrics]
        numeric = [(v, m) for v, m in vals if v is not None]

        if not numeric:
            for m in metrics:
                m.setdefault("scores", {})[crit["code"]] = None
            continue
        lo = min(v for v, _ in numeric)
        hi = max(v for v, _ in numeric)
        for v, m in vals:
            if v is None:
                m.setdefault("scores", {})[crit["code"]] = None
            elif hi == lo:
                m.setdefault("scores", {})[crit["code"]] = 1.0 if crit["direction"] == "maximize" else 0.5
            elif crit["direction"] == "minimize":
                m.setdefault("scores", {})[crit["code"]] = round((hi - v) / (hi - lo), 4)
            else:
                m.setdefault("scores", {})[crit["code"]] = round((v - lo) / (hi - lo), 4)

    def weighted(weights: dict[str, float]) -> list[dict]:
        total_weight = sum(weights.get(c["code"], 0) for c in criteria) or 1.0
        rows = []
        for m in metrics:
            s = sum((m.get("scores", {}).get(c["code"]) or 0) * weights.get(c["code"], 0)
                    for c in criteria if (m.get("scores", {}).get(c["code"]) is not None))
            rows.append({"option": m["option"], "score": round(s / total_weight, 4),
                         "coverage": sum(1 for c in criteria if m.get("scores", {}).get(c["code"]) is not None)})
        return sorted(rows, key=lambda r: -r["score"])

    base_weights = {c["code"]: float(c.get("weight", 0)) for c in criteria}
    results = weighted(base_weights)
    sensitivity = []
    for c in criteria:
        alt = dict(base_weights)
        alt[c["code"]] = max(0.0, alt[c["code"]] - 0.10)
        changed = weighted(alt)
        sensitivity.append({"criterion": c["label"], "perturbation": "-0.10 weight",
                            "ranking": [r["option"] for r in changed],
                            "top_option_changed": (changed[0]["option"] != results[0]["option"]) if changed and results else False})
    return {"applicable": True, "criteria": criteria, "metrics": metrics, "ranking": results,
            "weights_used": base_weights, "sensitivity": sensitivity,
            "method": "Weighted normalized scoring; weights are transparent and reviewer-editable per case. "
                      "Derived scores use current verified claims only; manual overrides are labeled.",
            "disclaimer": "Comparative ranking is decision support, not approval. The evidence gate determines eligibility."}


def _add(checks: list[dict], code: str, label: str, status: str, blocking: bool, detail: str, refs: list | None = None):
    checks.append({"code": code, "label": label, "status": status, "blocking": blocking and status == "fail",
                   "detail": detail, "refs": refs or []})


def evaluate_case(db: Session, case: DecisionCase, *, actor: User | None = None,
                  persist: bool = True, reason: str = "gate evaluated") -> dict:
    workspace = db.get(Workspace, case.workspace_id)
    policy = policy_for(workspace)
    as_of = case.as_of_date or date.today()
    claims, versions, documents, spans = decision_claims(db, case)
    requirements = evaluate_requirements(db, case, claims, versions, documents, policy, as_of)
    options = resolve_options(db, case)
    checks: list[dict] = []
    current_claims = [c for c in claims if versions.get(c.version_id) and versions[c.version_id].is_current]

    # 1. Evidence attached
    if not versions:
        _add(checks, "documents_attached", "Evidence documents attached", "fail", True,
             "No processed evidence documents are attached to this decision case.")
    else:
        _add(checks, "documents_attached", "Evidence documents attached", "pass", True,
             f"{len(documents)} document(s), {len(versions)} current version(s), {len(current_claims)} claim(s) in scope.",
             [{"type": "Document", "id": d} for d in documents])

    # 2. Mandatory sources
    doc_types = {(documents[v.document_id].doc_type if v.document_id in documents else "") for v in versions.values()}
    required_types = list(dict.fromkeys(
        [dt for req in requirements for dt in (req.get("required_doc_types") or [])] +
        [dt for dt in (case.requirements or []) if isinstance(dt, str)]
    ))
    missing = [dt for dt in required_types if dt not in doc_types]
    if not versions:
        _add(checks, "mandatory_sources", "Mandatory evidence sources present", "fail", True,
             "No evidence documents attached; mandatory sources cannot be satisfied.")
    elif missing:
        _add(checks, "mandatory_sources", "Mandatory evidence sources present", "fail", True,
             f"Missing mandatory document type(s): {', '.join(missing)}.")
    else:
        _add(checks, "mandatory_sources", "Mandatory evidence sources present", "pass", True,
             f"All required document types present: {', '.join(sorted(t for t in required_types if t)) or 'none specified'}.")

    # 3. Requirement coverage
    failed_reqs = [r for r in requirements if not r["pass"]]
    critical_failed = [r for r in failed_reqs if r.get("critical")]
    noncritical_failed = [r for r in failed_reqs if not r.get("critical")]
    detail_parts = []
    for r in failed_reqs:
        if r.get("missing_doc_types"):
            detail_parts.append(f"{r['label']}: missing {', '.join(r['missing_doc_types'])}")
        for opt in r.get("options", []):
            if not opt["pass"]:
                shown = opt.get("fresh_count", opt["count"]) if r.get("require_current") else opt["count"]
                detail_parts.append(f"{r['label']} — {opt['option']}: {shown}/{r.get('min_claims', 1)} required claims")
    if not requirements:
        _add(checks, "requirement_coverage", "Required claim coverage", "not_applicable", True,
             "No requirements configured for this decision type.")
    elif critical_failed:
        _add(checks, "requirement_coverage", "Required claim coverage", "fail", True,
             "Critical evidence gaps: " + ("; ".join(dict.fromkeys(detail_parts)) or "requirement not met"),
             [{"type": "Requirement", "id": r["code"]} for r in critical_failed])
    elif noncritical_failed:
        _add(checks, "requirement_coverage", "Required claim coverage", "warn", False,
             "Non-critical evidence gaps: " + "; ".join(dict.fromkeys(detail_parts)))
    else:
        _add(checks, "requirement_coverage", "Required claim coverage", "pass", True,
             f"All {len(requirements)} evidence requirement(s) satisfied across "
             f"{len(options) or 1} option(s).", [{"type": "Requirement", "id": r["code"]} for r in requirements])

    # 4. Citation resolvability
    cited = [c for c in current_claims if c.verification_status != "rejected"]
    unresolved = []
    for c in current_claims:
        span = spans.get(c.span_id)
        ok, why = citation_resolves(c, span)
        if not ok:
            unresolved.append({"claim_id": c.id, "reason": why})
    if not current_claims:
        _add(checks, "citations_resolve", "Citations resolve to source content", "fail", True,
             "No claims available to cite; attach and process evidence documents.")
    elif unresolved:
        _add(checks, "citations_resolve", "Citations resolve to source content", "fail", True,
             f"{len(unresolved)} of {len(current_claims)} claim citation(s) do not resolve to their cited span.",
             unresolved[:20])
    else:
        _add(checks, "citations_resolve", "Citations resolve to source content", "pass", True,
             f"All {len(cited)} claim citations resolve to exact source spans.")

    # 5. Contradictions
    contradictions = db.execute(
        select(Contradiction).where(Contradiction.workspace_id == case.workspace_id)
    ).scalars().all()
    claim_ids = {c.id for c in current_claims}
    # Only contradictions where *both* assertions are in this decision's evidence scope can
    # block it. A conflict between one attached source and an unattached document is a
    # workspace-level finding for the reviewer, not a gate failure for this case.
    relevant = [c for c in contradictions if c.claim_a_id in claim_ids and c.claim_b_id in claim_ids]
    blocking_conf = [c for c in relevant if c.state == "confirmed" and c.blocking]
    possible_high = [c for c in relevant if c.state == "possible" and c.severity in {"high", "critical"}]
    possible_other = [c for c in relevant if c.state == "possible" and c.severity not in {"high", "critical"}]
    if blocking_conf:
        _add(checks, "no_blocking_contradictions", "No unresolved blocking contradictions", "fail", True,
             f"{len(blocking_conf)} confirmed blocking contradiction(s): " +
             "; ".join(f"{c.kind} ({c.severity})" for c in blocking_conf[:5]),
             [{"type": "Contradiction", "id": c.id} for c in blocking_conf])
    elif possible_high:
        _add(checks, "no_blocking_contradictions", "No unresolved blocking contradictions", "warn", False,
             f"{len(possible_high)} high-severity possible contradiction(s) need reviewer confirmation.",
             [{"type": "Contradiction", "id": c.id} for c in possible_high])
    elif possible_other:
        _add(checks, "no_blocking_contradictions", "No unresolved blocking contradictions", "warn", False,
             f"{len(possible_other)} lower-severity possible contradiction(s) recorded for reviewer awareness.",
             [{"type": "Contradiction", "id": c.id} for c in possible_other])
    else:
        _add(checks, "no_blocking_contradictions", "No unresolved blocking contradictions", "pass", True,
             f"{len(relevant)} relevant contradiction record(s); none unresolved and blocking.")

    # 6. Freshness / temporal validity of required evidence
    required_claim_ids = {cid for r in requirements for cid in r.get("matched_claim_ids", [])}
    required_claims = [c for c in current_claims if c.id in required_claim_ids]
    expired, stale_noncritical, not_yet = [], [], []
    for c in required_claims:
        version = versions.get(c.version_id)
        req = next((r for r in requirements if c.id in r.get("matched_claim_ids", [])), None)
        f = claim_is_stale(c, version, as_of, (req or {}).get("max_age_days"))
        if f["stale"]:
            entry = {"claim_id": c.id, "predicate": c.predicate, "reason": f["reason"],
                     "critical": bool((req or {}).get("critical"))}
            if "not yet effective" in f["reason"]:
                not_yet.append(entry)
            elif "older than policy window" in f["reason"]:
                stale_noncritical.append(entry)
            else:
                expired.append(entry)
    if not required_claims and versions:
        _add(checks, "evidence_freshness", "Evidence is current and not superseded", "fail", True,
             "No required evidence could be located, so currency cannot be established.")
    elif expired or not_yet:
        _add(checks, "evidence_freshness", "Evidence is current and not superseded", "fail", True,
             "; ".join([f"{e['predicate']}: {e['reason']}" for e in (expired + not_yet)][:8]),
             expired + not_yet)
    elif stale_noncritical:
        critical_stale = [e for e in stale_noncritical if e["critical"]]
        status = "fail" if critical_stale else "warn"
        _add(checks, "evidence_freshness", "Evidence is current and not superseded", status, status == "fail",
             f"{len(stale_noncritical)} required claim(s) exceed their freshness window: " +
             "; ".join(f"{e['predicate']}: {e['reason']}" for e in stale_noncritical[:6]),
             stale_noncritical)
    elif required_claims:
        _add(checks, "evidence_freshness", "Evidence is current and not superseded", "pass", True,
             f"All {len(required_claims)} required claims are current as of {as_of.isoformat()}.")

    # 7. Data quality
    ambiguous = [c for c in required_claims if (c.qualifiers or {}).get("ambiguous") or (c.qualifiers or {}).get("date_ambiguous")]
    rejected_required = [c for c in required_claims if c.verification_status == "rejected"]
    missing_spans = [c for c in required_claims if c.span_id and c.span_id not in spans]
    duplicates = [c for c in current_claims if c.duplicate_of_id]
    if rejected_required or missing_spans:
        _add(checks, "data_quality", "Critical data-quality checks", "fail", True,
             f"{len(rejected_required) + len(missing_spans)} required claim(s) are rejected or lack a resolvable span.")
    elif ambiguous:
        _add(checks, "data_quality", "Critical data-quality checks", "warn", False,
             f"{len(ambiguous)} required claim(s) have ambiguous dates/values and need reviewer confirmation.",
             [{"type": "Claim", "id": c.id} for c in ambiguous])
    elif duplicates:
        _add(checks, "data_quality", "Critical data-quality checks", "warn", False,
             f"{len(duplicates)} duplicate claim(s) detected and de-emphasized in scoring.")
    else:
        _add(checks, "data_quality", "Critical data-quality checks", "pass", True,
             "No rejected, missing-span or ambiguous required claims.")

    # 8. AI analysis grounding
    analysis = db.execute(select(Analysis).where(Analysis.decision_id == case.id)
                          .order_by(Analysis.created_at.desc())).scalars().first()
    if analysis is None:
        _add(checks, "ai_analysis_grounded", "AI analysis statements are grounded", "not_applicable", False,
             "No AI analysis has been generated for this case.")
    else:
        unsupported = [s for s in (analysis.statements or []) if not s.get("supported") and not s.get("excluded", False)]
        if unsupported:
            _add(checks, "ai_analysis_grounded", "AI analysis statements are grounded", "fail", True,
                 f"{len(unsupported)} unsupported AI statement(s) are included in analysis {analysis.id}.",
                 [{"type": "Analysis", "id": analysis.id}])
        else:
            # Digest only content that a reviewer would care about. Statement ids are
            # regenerated per run, so including them would make every re-analysis look
            # like a new evidence state.
            digest_payload = [
                {"text": st.get("text"), "kind": st.get("kind"), "supported": bool(st.get("supported")),
                 "claim_ids": sorted(st.get("claim_ids") or []), "excluded": bool(st.get("excluded", False))}
                for st in (analysis.statements or [])
            ]
            statement_digest = hashlib.sha256(
                json.dumps(digest_payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:12]
            _add(checks, "ai_analysis_grounded", "AI analysis statements are grounded", "pass", True,
                 f"Analysis ({analysis.provider_label}, {len(analysis.statements or [])} statements, "
                 f"content {statement_digest}) has no unsupported statements.")

    # 9. Policy constraints
    policy_failures, policy_warnings = [], []
    min_options = int((policy.get("min_options") or {}).get(case.decision_type, 1))
    if len(options) < min_options:
        policy_failures.append(f"policy requires at least {min_options} compared option(s); {len(options)} provided")
    currencies = {c.currency for c in current_claims if c.predicate in {"unit_price", "price"} and c.currency}
    if len(currencies) > 1:
        policy_failures.append(f"price evidence uses multiple currencies without an approved FX basis ({', '.join(sorted(currencies))})")
    if case.decision_type == "engineering_change":
        rollback = [c for c in current_claims if "rollback" in (c.object_text + " " + (c.value_text or "")).lower()
                    or "contingency" in (c.object_text + " " + (c.value_text or "")).lower()]
        if not rollback:
            policy_warnings.append("no rollback/contingency statement found in attached change evidence")
    injection_docs = [v for v in versions.values() if v.injection_flags]
    if injection_docs:
        policy_failures.append(f"{len(injection_docs)} attached document version(s) contain prompt-injection-like content")
    if policy_failures:
        _add(checks, "policy_constraints", "Applicable policy constraints", "fail", True, "; ".join(policy_failures))
    elif policy_warnings:
        _add(checks, "policy_constraints", "Applicable policy constraints", "warn", False, "; ".join(policy_warnings))
    else:
        _add(checks, "policy_constraints", "Applicable policy constraints", "pass", True,
             f"Policy {policy.get('version')} constraints satisfied "
             f"({len(options)} option(s), currencies: {', '.join(sorted(currencies)) or 'n/a'}).")

    # Pre-attestation hash: what a reviewer is attesting to.
    evidence_manifest = sorted([c.id for c in current_claims])
    # The attestation binds the evidence state and the deterministic checks that decide the
    # gate outcome. The AI-grounding check is deliberately excluded: the advisory analysis is
    # regenerated routinely (and its text legitimately mentions the current gate status), and
    # the model is never allowed to decide a gate outcome. Regenerating a grounded analysis must
    # therefore not revoke a valid human attestation. If the regenerated analysis is *not*
    # grounded, the gate still fails on that check and the approval is explicitly invalidated.
    attestation_checks = [c for c in checks if c.get("code") != "ai_analysis_grounded"]
    pre_state = {
        "decision_id": case.id, "decision_type": case.decision_type, "as_of": as_of.isoformat(),
        "gate_version": GATE_VERSION, "policy": policy, "checks": attestation_checks,
        "requirements": [{k: r.get(k) for k in ("code", "pass", "missing_doc_types")} |
                         {"matched_claim_ids": r.get("matched_claim_ids", [])} for r in requirements],
        "versions": sorted(versions.keys()), "claims": evidence_manifest,
    }
    pre_hash = _hash(pre_state)

    # 10. Reviewer attestations
    approvals_required = int((policy.get("approvals_required") or {}).get(case.decision_type, 1))
    attestations = db.execute(select(Approval).where(Approval.decision_id == case.id,
                                                      Approval.action == "attest")).scalars().all()
    valid_attestations = [a for a in attestations if a.gate_snapshot_hash == pre_hash and a.user_id != case.created_by]
    if approvals_required == 0:
        _add(checks, "reviewer_attestations", "Required human review completed", "not_applicable", False,
             "Policy requires no reviewer attestation for this decision type.")
    elif len(valid_attestations) >= approvals_required:
        _add(checks, "reviewer_attestations", "Required human review completed", "pass", True,
             f"{len(valid_attestations)} valid reviewer attestation(s) for the current evidence snapshot.",
             [{"type": "Reviewer", "id": a.user_id} for a in valid_attestations])
    else:
        stale_attestations = len(attestations) - len(valid_attestations)
        _add(checks, "reviewer_attestations", "Required human review completed", "fail", True,
             f"{len(valid_attestations)}/{approvals_required} valid reviewer attestation(s). "
             f"{stale_attestations} attestation(s) are stale or self-issued and do not count.",
             [{"type": "Analysis", "id": pre_hash[:16]}])

    # 11. Recorded exception (never alters checks)
    if case.exception_recorded:
        _add(checks, "policy_exception", "Recorded policy exception", "warn", False,
             f"An exception was recorded by a human: {case.exception_reason}. Gate results remain unchanged.")

    blocking_failures = [c for c in checks if c["status"] == "fail" and c["blocking"]]
    warnings = [c for c in checks if c["status"] == "warn"]
    contradiction_fail = any(c["code"] == "no_blocking_contradictions" and c["status"] == "fail" for c in checks)
    if not versions:
        gate_status = "EVIDENCE_INCOMPLETE"
    elif contradiction_fail:
        gate_status = "CONTRADICTED"
    elif any(c["code"] != "reviewer_attestations" and c["status"] == "fail" for c in checks):
        gate_status = "EVIDENCE_INCOMPLETE"
    elif any(c["code"] == "reviewer_attestations" and c["status"] == "fail" for c in checks):
        gate_status = "REVIEW_REQUIRED"
    elif warnings:
        gate_status = "REVIEW_REQUIRED"
    else:
        gate_status = "SUFFICIENTLY_SUPPORTED"

    mcda = compute_mcda(db, case, current_claims, versions=versions, as_of=as_of)
    result = {
        "gate_version": GATE_VERSION, "policy_version": policy.get("version"),
        "as_of": as_of.isoformat(), "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "status": gate_status, "checks": checks, "requirements": requirements,
        "options": options, "blocking_failures": [c["code"] for c in blocking_failures],
        "warnings": [c["code"] for c in warnings],
        "evidence": {"documents": sorted(documents.keys()), "versions": sorted(versions.keys()),
                     "claims": len(current_claims), "required_claim_ids": sorted(required_claim_ids)},
        "attestation_pre_hash": pre_hash,
        "policy": policy, "mcda": mcda,
        "summary": {
            "passed": sum(1 for c in checks if c["status"] == "pass"),
            "failed": sum(1 for c in checks if c["status"] == "fail"),
            "warnings": len(warnings),
            "checks": len(checks),
        },
    }
    result["result_hash"] = _hash(result)
    if persist:
        previous_status = case.status
        db.execute(GateCheck.__table__.delete().where(GateCheck.decision_id == case.id))
        for check in checks:
            db.add(GateCheck(decision_id=case.id, workspace_id=case.workspace_id, code=check["code"],
                             label=check["label"], status=check["status"], blocking=check["blocking"],
                             detail=check["detail"], refs=check["refs"], gate_version=GATE_VERSION))
        case.gate_status = gate_status
        case.gate_result = result
        case.gate_evaluated_at = datetime.now(timezone.utc)
        approval_invalidated = previous_status == "APPROVED" and gate_status != "SUFFICIENTLY_SUPPORTED"
        if approval_invalidated:
            append_event(db, case.workspace_id, "decision.approval_invalidated", "DecisionCase", case.id, {
                "previous_status": previous_status, "new_status": gate_status, "reason": reason,
                "result_hash": result["result_hash"], "blocking_failures": result["blocking_failures"],
                "actor": actor.email if actor else "system",
            }, actor=actor, actor_label=actor.email if actor else "gate", commit=False)
        if case.status != "REJECTED":
            if gate_status == "SUFFICIENTLY_SUPPORTED":
                if case.status != "APPROVED":
                    case.status = "SUFFICIENTLY_SUPPORTED"
            elif case.status in {"DRAFT", "SUFFICIENTLY_SUPPORTED", "APPROVED", "REVIEW_REQUIRED",
                                 "EVIDENCE_INCOMPLETE", "CONTRADICTED"}:
                case.status = gate_status
        append_event(db, case.workspace_id, "decision.gate_evaluated", "DecisionCase", case.id, {
            "gate_status": gate_status, "previous_status": previous_status, "reason": reason,
            "result_hash": result["result_hash"], "blocking_failures": result["blocking_failures"],
            "warnings": result["warnings"], "actor": actor.email if actor else "system",
        }, actor=actor, actor_label=actor.email if actor else "gate", commit=False)
        db.commit()
    return result


def reevaluate_decisions_for_document(db: Session, workspace_id: str, document_id: str, reason: str) -> list[dict]:
    doc = db.get(Document, document_id)
    cases = db.execute(
        select(DecisionCase).join(DecisionDocument, DecisionDocument.decision_id == DecisionCase.id)
        .where(DecisionDocument.document_id == document_id, DecisionCase.workspace_id == workspace_id)
    ).scalars().all()
    out = []
    for case in cases:
        before = case.status
        evaluate_case(db, case, reason=reason)
        if before in {"APPROVED", "SUFFICIENTLY_SUPPORTED"} and case.status not in {"APPROVED", "SUFFICIENTLY_SUPPORTED"}:
            append_event(db, workspace_id, "decision.reopened_on_source_change", "DecisionCase", case.id, {
                "previous_status": before, "new_status": case.status, "document_id": document_id,
                "document": doc.title if doc else document_id, "reason": reason,
            }, actor_label="system")
            db.commit()
        out.append({"decision_id": case.id, "title": case.title, "before": before,
                    "after": case.status, "gate_status": case.gate_status})
    return out


def create_attestation(db: Session, case: DecisionCase, user: User, comment: str) -> dict:
    if case.status in {"REJECTED"}:
        raise ValueError("Rejected decisions cannot be attested")
    if user.id == case.created_by:
        raise ValueError("The case creator cannot attest their own evidence")
    result = evaluate_case(db, case, actor=None, persist=True, reason="pre-attestation gate snapshot")
    pre_hash = result["attestation_pre_hash"]
    signature = _hash({"decision_id": case.id, "user_id": user.id, "gate": pre_hash, "comment": comment,
                       "signed_at": datetime.now(timezone.utc).isoformat()})
    approval = Approval(decision_id=case.id, workspace_id=case.workspace_id, user_id=user.id,
                        action="attest", comment=comment, gate_snapshot_hash=pre_hash, signature_hash=signature)
    db.add(approval)
    append_event(db, case.workspace_id, "decision.reviewer_attested", "DecisionCase", case.id, {
        "comment": comment, "gate_hash": pre_hash, "signature": signature,
    }, actor=user, commit=False)
    db.commit()
    refreshed = evaluate_case(db, case, actor=user, persist=True, reason="post-attestation re-evaluation")
    return {"approval_id": approval.id, "gate_status": refreshed["status"],
            "case_status": case.status, "attestation_pre_hash": pre_hash}


def final_approval(db: Session, case: DecisionCase, user: User, comment: str) -> dict:
    if case.status == "REJECTED":
        raise ValueError("Rejected decisions cannot be approved; reopen the case first")
    if user.id == case.created_by:
        raise ValueError("The case creator cannot grant final approval")
    result = evaluate_case(db, case, actor=None, persist=True, reason="pre-approval verification")
    if result["status"] != "SUFFICIENTLY_SUPPORTED":
        raise ValueError(
            f"Decision is not eligible for approval: gate status is {result['status']} "
            f"(blocking failures: {', '.join(result['blocking_failures']) or 'none'}; warnings: "
            f"{', '.join(result['warnings']) or 'none'}). Record a policy exception to override (admin only)."
        )
    approval = Approval(decision_id=case.id, workspace_id=case.workspace_id, user_id=user.id,
                        action="approve", comment=comment, gate_snapshot_hash=result["result_hash"],
                        signature_hash=_hash({"decision_id": case.id, "user_id": user.id,
                                              "gate": result["result_hash"], "action": "approve"}))
    db.add(approval)
    case.status = "APPROVED"
    case.selected_option = case.selected_option or (result["options"][0]["name"] if result["options"] else None)
    append_event(db, case.workspace_id, "decision.approved", "DecisionCase", case.id, {
        "comment": comment, "gate_hash": result["result_hash"], "selected_option": case.selected_option,
    }, actor=user, commit=True)
    return {"status": case.status, "gate_status": result["status"], "result_hash": result["result_hash"]}


def record_rejection(db: Session, case: DecisionCase, user: User, comment: str) -> dict:
    if case.status == "REJECTED":
        raise ValueError("Decision already rejected")
    case.status = "REJECTED"
    db.add(Approval(decision_id=case.id, workspace_id=case.workspace_id, user_id=user.id,
                    action="reject", comment=comment))
    append_event(db, case.workspace_id, "decision.rejected", "DecisionCase", case.id,
                 {"comment": comment}, actor=user, commit=True)
    return {"status": case.status}


def request_changes(db: Session, case: DecisionCase, user: User, comment: str) -> dict:
    if case.status == "REJECTED":
        raise ValueError("Rejected decisions cannot request changes; reopen the case first")
    case.status = "REVIEW_REQUIRED"
    db.add(Approval(decision_id=case.id, workspace_id=case.workspace_id, user_id=user.id,
                    action="request_changes", comment=comment))
    append_event(db, case.workspace_id, "decision.changes_requested", "DecisionCase", case.id,
                 {"comment": comment}, actor=user, commit=True)
    return {"status": case.status}


def record_exception(db: Session, case: DecisionCase, user: User, reason: str) -> dict:
    if user.role != "admin":
        raise ValueError("Only administrators can record a policy exception")
    if len(reason.strip()) < 20:
        raise ValueError("Exception reason must be at least 20 characters and explain the override basis")
    result = evaluate_case(db, case, actor=None, persist=True, reason="gate snapshot at exception")
    case.exception_recorded = True
    case.exception_reason = reason.strip()
    case.exception_by = user.id
    case.status = "APPROVED"
    append_event(db, case.workspace_id, "decision.exception_approved", "DecisionCase", case.id, {
        "reason": reason, "gate_status_at_override": result["status"],
        "blocking_failures": result["blocking_failures"], "warnings": result["warnings"],
        "result_hash": result["result_hash"],
    }, actor=user, commit=True)
    return {"status": case.status, "gate_status_unchanged": result["status"],
            "blocking_failures": result["blocking_failures"]}


def reopen_case(db: Session, case: DecisionCase, user: User) -> dict:
    if user.role not in {"admin", "auditor"}:
        raise ValueError("Only auditor/admin can reopen a case")
    if case.status != "REJECTED":
        raise ValueError("Only rejected cases can be reopened")
    previous = case.status
    case.status = "DRAFT"
    append_event(db, case.workspace_id, "decision.reopened", "DecisionCase", case.id,
                 {"previous_status": previous}, actor=user, commit=True)
    return {"status": case.status}
