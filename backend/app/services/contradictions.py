"""Hybrid contradiction detection: typed comparison + temporal logic + scope/unit/currency checks.

Different values are not automatically contradictions. The engine checks entity identity,
scope compatibility, overlapping validity windows, unit normalization and supersession.
"""
from __future__ import annotations

import itertools
import logging
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Claim, Contradiction, DecisionCase, DocumentVersion, Edge, SourceSpan
from app.services.claims import citation_resolves
from app.services.normalize import normalize_text_value, overlap
from app.services.predicates import CERTIFICATION_PREDICATES, P

log = logging.getLogger(__name__)

TOLERANCE = {"money": 0.005, "percent": 0.01, "number": 0.01, "duration": 1.0, "date": 0.0,
             "ordinal": 0.0, "range": 0.5, "text": 0.0, "standard": 0.0}
KIND_BY_PREDICATE = {
    "unit_price": "price_conflict", "price": "price_conflict", "discount_percent": "pricing_conflict",
    "delivery_days": "delivery_conflict", "delivery_date": "delivery_conflict",
    "warranty_days": "warranty_conflict", "payment_terms": "payment_terms_conflict",
    "certification": "certification_conflict", "conformance": "conformance_conflict",
    "valid_until": "validity_conflict", "effective_date": "validity_conflict",
    "liability_cap": "legal_terms_conflict", "penalty": "legal_terms_conflict",
    "incoterm": "delivery_terms_conflict", "sla_uptime": "sla_conflict",
    "risk_level": "assessment_divergence", "quantity": "quantity_conflict",
}
SEVERITY_BY_KIND = {
    "price_conflict": "high", "pricing_conflict": "medium", "delivery_conflict": "high",
    "warranty_conflict": "medium", "payment_terms_conflict": "medium",
    "certification_conflict": "critical", "conformance_conflict": "high",
    "validity_conflict": "medium", "legal_terms_conflict": "high", "delivery_terms_conflict": "medium",
    "sla_conflict": "high", "assessment_divergence": "low", "quantity_conflict": "high",
    "spec_conflict": "high", "currency_mismatch": "medium", "denomination_mismatch": "low",
    "attestation_conflict": "high", "value_conflict": "medium",
}
SCOPE_STOP = {"the", "and", "for", "of", "a", "an", "to", "with", "supply", "purchase", "procurement",
              "installation", "project", "phase", "contract", "agreement", "quote", "quotation"}
UNIT_DIMENSION = {"kg": "mass", "g": "mass", "lb": "mass", "lbs": "mass", "t": "mass", "day": "time",
                  "days": "time", "hour": "time", "hours": "time", "%": "percent", "c": "temp", "w": "power",
                  "kw": "power", "mw": "power", "hp": "power", "db": "sound"}


def _scope_tokens(scope: str) -> set[str]:
    raw = {t for t in re.findall(r"[a-z0-9]+", (scope or "").lower()) if len(t) > 1}
    filtered = {t for t in raw if t not in SCOPE_STOP}
    # If a scope label consists only of generic words ("Installation"), fall back to the raw
    # tokens so distinct labels are not mistaken for "no scope information".
    return filtered or raw


def scopes_compatible(a: str, b: str) -> bool:
    ta, tb = _scope_tokens(a), _scope_tokens(b)
    if not ta or not tb:
        return True
    if ta <= tb or tb <= ta:
        return True
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union) >= 0.5


def values_equal(a: Claim, b: Claim) -> bool:
    kind = P[a.predicate].kind
    tol = TOLERANCE.get(kind, 0.01)
    if kind in {"money", "percent", "number", "duration"}:
        if a.value_numeric is None or b.value_numeric is None:
            return False
        if (a.unit or "") != (b.unit or "") and kind not in {"money"}:
            dims = {UNIT_DIMENSION.get((a.unit or "").lower()), UNIT_DIMENSION.get((b.unit or "").lower())}
            if len(dims) > 1:
                return False
        if kind == "duration":
            # Duration tolerance is absolute (days); relative scaling would make 5 days look "equal"
            # for long lead times and silently hide delivery discrepancies.
            return abs(a.value_numeric - b.value_numeric) <= tol
        scale = max(abs(a.value_numeric), abs(b.value_numeric), 1e-9)
        return abs(a.value_numeric - b.value_numeric) <= max(tol, tol * scale)
    if kind == "date":
        return (a.value_text or "") == (b.value_text or "")
    if kind == "range":
        if a.value_numeric is None or b.value_numeric is None:
            return False
        high_a = (a.qualifiers or {}).get("high")
        high_b = (b.qualifiers or {}).get("high")
        return (abs(a.value_numeric - b.value_numeric) <= tol and
                high_a is not None and high_b is not None and abs(high_a - high_b) <= tol)
    if kind == "ordinal":
        return (a.value_text or "").lower() == (b.value_text or "").lower()
    return normalize_text_value(a.value_text or a.object_text) == normalize_text_value(b.value_text or b.object_text)


def _reason(a: Claim, b: Claim, verdict: str) -> str:
    return (f"{verdict}: claim {a.id[:8]} ({a.predicate} = {a.object_text!r}) vs "
            f"claim {b.id[:8]} ({b.predicate} = {b.object_text!r}); "
            f"documents {a.version_id[:8]} and {b.version_id[:8]}; "
            f"scope {a.scope or 'unspecified'!r} vs {b.scope or 'unspecified'!r}; "
            f"effective {a.effective_date or 'unspecified'} .. {a.valid_until or 'open'} vs "
            f"{b.effective_date or 'unspecified'} .. {b.valid_until or 'open'}")


def _claims_contradict(a: Claim, b: Claim, as_of: date) -> dict | None:
    if a.id == b.id or a.duplicate_of_id == b.id or b.duplicate_of_id == a.id:
        return None
    if a.predicate != b.predicate or a.claim_key != b.claim_key:
        return None
    if not P[a.predicate].comparable:
        return None
    a_entity, b_entity = a.entity_id, b.entity_id
    if not ((a_entity and a_entity == b_entity) or (not a_entity and not b_entity)):
        return None
    if a.version_id == b.version_id and (a.scope or "").strip() and (b.scope or "").strip() \
            and normalize_text_value(a.scope) != normalize_text_value(b.scope):
        # Two differently labelled rows inside one document (e.g. budget lines) are not a
        # cross-source contradiction. Row-level data quality is handled by extraction checks.
        return None
    if not scopes_compatible(a.scope, b.scope):
        return None
    a0, a1 = a.effective_date, a.valid_until
    b0, b1 = b.effective_date, b.valid_until
    if not overlap(a0, a1, b0, b1):
        return None
    # Both expired before as_of: obsolete evidence, handled by freshness checks, not a contradiction.
    if (a1 and a1 < as_of) and (b1 and b1 < as_of):
        return None
    if a.currency and b.currency and a.currency != b.currency and P[a.predicate].kind == "money":
        if values_equal(a, b):
            return {"kind": "denomination_mismatch", "state": "possible", "severity": "low",
                    "confidence": 0.7, "blocking": False,
                    "reasoning": _reason(a, b, "Same numeric amount is denominated in different currencies; "
                                                 "no FX rate is configured so equality cannot be asserted")}
        return {"kind": "currency_mismatch", "state": "possible", "severity": "medium",
                "confidence": 0.8, "blocking": False,
                "reasoning": _reason(a, b, "Monetary values differ and are denominated in different currencies; "
                                             "deterministic comparison is impossible without an approved FX rate")}
    if a.polarity != b.polarity and a.predicate in CERTIFICATION_PREDICATES | {"compliance_declaration"}:
        kind = "attestation_conflict"
        return {"kind": kind, "state": "confirmed", "severity": SEVERITY_BY_KIND[kind], "confidence": 0.85,
                "blocking": True, "reasoning": _reason(a, b, "Affirmative and negative attestations about the same subject")}
    if values_equal(a, b):
        return None
    kind = KIND_BY_PREDICATE.get(a.predicate, "spec_conflict" if a.predicate.startswith("spec_") else "value_conflict")
    severity = SEVERITY_BY_KIND.get(kind, "medium")
    if kind == "price_conflict" and a.value_numeric and b.value_numeric:
        diff = abs(a.value_numeric - b.value_numeric) / max(abs(a.value_numeric), abs(b.value_numeric))
        severity = "critical" if diff >= 0.25 else "high"
    confidence = round(min(a.extraction_confidence, b.extraction_confidence), 2)
    both_strong = (a.extraction_confidence >= 0.8 and b.extraction_confidence >= 0.8
                   and not (a.qualifiers or {}).get("date_ambiguous") and not (b.qualifiers or {}).get("date_ambiguous")
                   and not (a.qualifiers or {}).get("ambiguous") and not (b.qualifiers or {}).get("ambiguous"))
    state = "confirmed" if both_strong else "possible"
    # Risk-rating divergence between assessors is a review signal, never auto-blocking.
    blocking = state == "confirmed" and severity in {"high", "critical"} and kind != "assessment_divergence"
    return {"kind": kind, "state": state, "severity": severity, "confidence": max(0.5, confidence),
            "blocking": blocking,
            "reasoning": _reason(a, b, "Values differ after entity/scope/unit/period compatibility checks")}


def detect_contradictions(db: Session, workspace_id: str, as_of: date | None = None,
                          commit: bool = True) -> dict:
    if as_of is None:
        latest_case = db.execute(select(DecisionCase).where(DecisionCase.workspace_id == workspace_id)
                                 .order_by(DecisionCase.as_of_date.desc())).scalars().first()
        as_of = (latest_case.as_of_date if latest_case and latest_case.as_of_date else date.today())
    rows = db.execute(
        select(Claim, DocumentVersion, SourceSpan)
        .join(DocumentVersion, Claim.version_id == DocumentVersion.id)
        .outerjoin(SourceSpan, Claim.span_id == SourceSpan.id)
        .where(Claim.workspace_id == workspace_id, Claim.review_status != "rejected",
               DocumentVersion.is_current.is_(True), DocumentVersion.status == "processed")
    ).all()
    claims = [r[0] for r in rows]
    groups: dict[str, list[Claim]] = {}
    for c in claims:
        if not c.claim_key or c.claim_key.startswith("unbound|"):
            groups.setdefault(f"{c.version_id}|{c.claim_key}", []).append(c)
        else:
            groups.setdefault(c.claim_key, []).append(c)
    invalidated_pairs = {
        frozenset((e.source_id, e.target_id)) for e in db.execute(
            select(Edge).where(Edge.workspace_id == workspace_id, Edge.relation == "INVALIDATES",
                               Edge.source_type == "Claim", Edge.target_type == "Claim")
        ).scalars()
    }
    detected: dict[tuple[str, str, str], dict] = {}
    for key, group in groups.items():
        if len(group) < 2:
            continue
        group = group[:60]
        for a, b in itertools.combinations(group, 2):
            if frozenset((a.id, b.id)) in invalidated_pairs:
                continue
            result = _claims_contradict(a, b, as_of)
            if not result:
                continue
            a_id, b_id = sorted([a.id, b.id])
            ca, cb = (a, b) if a.id == a_id else (b, a)
            pair_key = (a_id, b_id, result["kind"])
            if pair_key in detected:
                continue
            result.update({"claim_a": ca, "claim_b": cb})
            detected[pair_key] = result
    existing = db.execute(select(Contradiction).where(Contradiction.workspace_id == workspace_id)).scalars().all()
    existing_map = {(c.claim_a_id, c.claim_b_id, c.kind): c for c in existing}
    created = updated = resolved = 0
    for pair_key, result in detected.items():
        a, b = result["claim_a"], result["claim_b"]
        if pair_key in existing_map:
            c = existing_map[pair_key]
            changed = False
            for attr in ("state", "severity", "confidence", "reasoning", "blocking"):
                new_val = result[attr]
                if attr == "state" and c.state in {"confirmed", "dismissed"}:
                    continue
                if attr == "blocking" and c.state == "dismissed":
                    continue
                if getattr(c, attr) != new_val:
                    setattr(c, attr, new_val)
                    changed = True
            if changed:
                updated += 1
            result["entity"] = c
        else:
            c = Contradiction(
                workspace_id=workspace_id, claim_a_id=a.id, claim_b_id=b.id, kind=result["kind"],
                state=result["state"], severity=result["severity"], confidence=result["confidence"],
                reasoning=result["reasoning"], detected_by="rule_engine", blocking=result["blocking"],
            )
            db.add(c)
            db.flush()
            for claim in (a, b):
                db.add(Edge(workspace_id=workspace_id, source_type="Contradiction", source_id=c.id,
                            target_type="Claim", target_id=claim.id, relation="CONTRADICTS",
                            justification=result["kind"], confidence=result["confidence"],
                            created_by="rule_engine"))
            created += 1
            result["entity"] = c
    for pair_key, c in existing_map.items():
        if pair_key not in detected and c.state != "resolved":
            c.state = "resolved"
            c.review_note = (c.review_note or "") + " [auto] No longer detected after evidence update."
            c.blocking = False
            resolved += 1
    # Refresh verification status on claims from deterministic signals.
    contradictions = db.execute(select(Contradiction).where(Contradiction.workspace_id == workspace_id)).scalars().all()
    confirmed_ids: set[str] = set()
    possible_ids: set[str] = set()
    for c in contradictions:
        if c.state == "confirmed":
            confirmed_ids.update([c.claim_a_id, c.claim_b_id])
        elif c.state == "possible":
            possible_ids.update([c.claim_a_id, c.claim_b_id])
    versions = {v.id: v for v in db.execute(
        select(DocumentVersion).where(DocumentVersion.workspace_id == workspace_id)).scalars()}
    spans = {s.id: s for s in db.execute(
        select(SourceSpan).where(SourceSpan.workspace_id == workspace_id)).scalars()}
    for claim in claims:
        version = versions.get(claim.version_id)
        span = spans.get(claim.span_id)
        reason = ""
        status = "verified"
        if claim.review_status == "rejected":
            status, reason = "rejected", "reviewer rejected claim"
        elif claim.injection_flag:
            status, reason = "rejected", "content flagged as prompt injection"
        elif claim.duplicate_of_id:
            status, reason = "verified", "duplicate of another claim"
        else:
            resolves, why = citation_resolves(claim, span)
            if not resolves:
                status, reason = "rejected", why
            elif version and not version.is_current:
                status, reason = "unsupported", "source version superseded"
            elif claim.valid_until and claim.valid_until < as_of:
                status, reason = "unsupported", f"evidence expired on {claim.valid_until.isoformat()}"
            elif version and version.valid_until and version.valid_until < as_of:
                status, reason = "unsupported", f"source document expired on {version.valid_until.isoformat()}"
            elif claim.id in confirmed_ids:
                status, reason = "disputed", "involved in a confirmed contradiction"
            elif claim.id in possible_ids:
                status, reason = "uncertain", "possible contradiction pending reviewer confirmation"
            elif claim.extraction_confidence < 0.55 or (claim.qualifiers or {}).get("ambiguous"):
                status, reason = "uncertain", "low-confidence or ambiguous extraction"
        claim.verification_status = status
        meta = dict(claim.meta or {})
        if reason:
            meta["verification_reason"] = reason
        else:
            meta.pop("verification_reason", None)
        claim.meta = meta
    if commit:
        db.commit()
    return {"created": created, "updated": updated, "resolved": resolved,
            "candidates": len(detected), "evaluated_claims": len(claims), "as_of": as_of.isoformat()}
