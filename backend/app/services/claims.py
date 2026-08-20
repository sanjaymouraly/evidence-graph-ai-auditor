"""Deterministic, schema-validated claim extraction with immutable provenance spans.

Extraction confidence is a *parse* signal only. It is never treated as factual
correctness; verification status is derived separately from citation resolution,
document currency and contradiction review.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Claim, DocumentVersion, Edge, Entity, SourceSpan
from app.services.entities import ORG_PATTERN, entity_key
from app.services.normalize import (
    AMOUNT_RE, AMOUNT_SRC, DATE_SRC, convert_mass, convert_power, normalize_date,
    normalize_duration, normalize_money, normalize_percent, normalize_standard,
    normalize_temperature, normalize_text_value, parse_number, sentence_split,
)
from app.services.predicates import P, RISK_ORDINAL

log = logging.getLogger(__name__)

AMT = AMOUNT_SRC
DATE = DATE_SRC
MODALITY_RE = re.compile(r"\b(must|shall|shall not|is required to|are required to|prohibits?|required)\b", re.I)
CERT_STD_RE = re.compile(
    r"\b(ISO\s?\d{4,5}(?::\d{4})?|IEC\s?\d{4,5}(?:[-:]\d+)*(?:\s*SIL\s*\d)?|SOC\s?2|RoHS|REACH|CE|UL\s?\d+|ATEX|API\s?610)\b",
    re.I,
)
NEGATION_RE = re.compile(r"\b(not|no longer|never|without|fails? to|cannot|unable to|lapsed|suspended|revoked)\b", re.I)

RE_UNIT_PRICE = re.compile(
    r"(?:unit\s*price|price\s*per\s*(?:unit|piece|pcs|set|kg|tonne|hour)|rate\s*per\s*(?:unit|piece|hour)|unit\s*rate|rate)\s*"
    r"(?:of|is|are|was|will be|shall be|=|:)?\s*(" + AMT + r")", re.I)
RE_PRICE_TOTAL = re.compile(
    r"(?:total\s*(?:price|contract\s*value|order\s*value|cost|amount)|contract\s*value|order\s*value|"
    r"grand\s*total|quoted\s*(?:price|amount)|budget)\s*"
    r"(?:of|is|are|was|will be|shall be|=|:)?\s*(" + AMT + r")", re.I)
RE_PRICE_GENERIC = re.compile(
    r"(?<!unit )(?<!unit_)\b(?:price|cost|amount|fee)\s*(?:of|is|are|was|will be|shall be|=|:)?\s*(" + AMT + r")", re.I)
RE_PRICE_PER = re.compile(
    r"(" + AMT + r")\s*(?:per|/)\s*(?:unit|piece|pcs|set|kg|tonne|hour)\b", re.I)
RE_DELIVERY_DAYS = re.compile(
    r"(?:deliver(?:y|ed)?(?:\s+(?:time|period|schedule))?|lead[\s-]*time|shipment|supply\s+period)"
    r"(?:\s*(?:within|in|of|:|by|=|shall\s+be|will\s+be|is)){0,3}\s*(\d+(?:\.\d+)?)\s*"
    r"(business\s+|calendar\s+|working\s+)?(day|week|month)s?\b", re.I)
RE_DELIVERY_DATE = re.compile(
    r"(?:deliver(?:y|ed)?\s*(?:date)?\s*(?:by|on|:)?|shall\s+be\s+delivered\s+(?:by|on)|delivery\s+by)\s*"
    r"(?:of|:)?\s*(" + DATE + r")", re.I)
RE_PAYMENT = re.compile(
    r"(?:payment\s*terms?|payable\s*(?:within)?|net)\s*(?:of|is|are|:)?\s*"
    r"(net\s*[-\s]?\d+|\d+\s*(?:calendar\s+|business\s+)?days?)", re.I)
RE_WARRANTY = re.compile(
    r"warrant(?:y|ies)\s*(?:period)?\s*(?:of|:)?\s*(\d+(?:\.\d+)?)\s*(month|year|week|day)s?", re.I)
RE_VALID_UNTIL = re.compile(
    r"(?:valid\s*(?:until|till|through|up\s+to)|expir(?:y|es|ation)(?:\s+date)?|validity)\s*"
    r"(?:on|:)?\s*(" + DATE + r")", re.I)
RE_MILESTONE = re.compile(
    r"(?:milestone|go[\s-]?live|completion|deadline)\s*(?:date)?\s*[:\-]\s*(" + DATE + r")", re.I)
RE_MITIGATION = re.compile(r"\b(rollback|contingenc\w*|mitigation)\b.{0,30}\b(plan|procedure|measure)\b", re.I)
RE_EFFECTIVE = re.compile(
    r"(?:effective\s*(?:date|from)?|valid\s+from|commenc(?:es|ement)\s+(?:on|date)?)\s*"
    r"(?:of|:|on)?\s*(" + DATE + r")", re.I)
RE_CONFORMANCE = re.compile(
    r"(?:complies|conforms|compliant)\s+(?:with|to)\s+([A-Za-z0-9][\w\s\-:./&]{1,70}?)(?:[,.;)]|$)", re.I)
RE_UPTIME = re.compile(r"(?:uptime|availability)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*%", re.I)
RE_EFFICIENCY = re.compile(r"efficiency\s*(?:of|is|=|:)?\s*(?:up to\s*)?(\d+(?:\.\d+)?)\s*%", re.I)
RE_ACCURACY = re.compile(r"accuracy\s*(?:of|is|=|:)?\s*(?:±|within\s*)?\s*(\d+(?:\.\d+)?)\s*(%|mm|m|cm)?", re.I)
RE_TEMP = re.compile(
    r"(?:operating\s+)?temperature\s*(?:range)?\s*(?:of|is|=|:)?\s*"
    r"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?([CF])?\s*(?:to|–|-|and)\s*(-?\d+(?:\.\d+)?)\s*(?:°\s*)?([CF])?", re.I)
RE_WEIGHT = re.compile(r"(?:net\s+)?weight\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*(kg|g|lb|lbs|t|tonnes?)\b", re.I)
RE_POWER = re.compile(
    r"(?:power\s*(?:rating|consumption)?|rated\s+power)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*(kW|MW|W|hp)\b", re.I)
RE_CAPACITY = re.compile(
    r"(?:load\s+capacity|payload|capacity)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*"
    r"(kg|g|lb|lbs|t|tonnes?|units?)\b", re.I)
RE_THROUGHPUT = re.compile(r"throughput\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*([A-Za-z/ ]{2,25})?", re.I)
RE_RESOLUTION = re.compile(r"resolution\s*(?:of|is|=|:)?\s*(\d{3,5}\s*[x×]\s*\d{3,5})", re.I)
RE_IP = re.compile(r"\bIP\s?(\d{2}[A-Z]?)\b", re.I)
RE_NOISE = re.compile(r"(?:noise|sound)\s*(?:level|pressure)?\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*dB", re.I)
RE_QUANTITY = re.compile(
    r"(?:quantity|qty|order(?:ed)?\s+quantity|supply\s+of)\s*(?:of|is|=|:)?\s*(\d[\d,]*)\s*"
    r"(units?|pcs|pieces?|sets?|licen[cs]es?|seats?|kg|tonnes?)?", re.I)
RE_DISCOUNT = re.compile(r"discount\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)\s*%", re.I)
RE_LIABILITY = re.compile(
    r"(?:liability(?:\s+cap)?|aggregate\s+liability|indemnity)\D{0,50}?"
    r"(?:limited\s+to|capped\s+at|not\s+exceed|=|:|of)\s*(" + AMT + r")", re.I)
RE_PENALTY = re.compile(r"(?:penalt(?:y|ies)|liquidated\s+damages)\D{0,50}?(" + AMT + r")", re.I)
RE_INCOTERM = re.compile(r"(?:incoterms?|delivery\s+terms?)\s*(?:of|:)?\s*(DDP|DAP|CIF|FOB|EXW|CPT|CIP|CFR)\b", re.I)
RE_RISK = re.compile(
    r"(?:(?:risk|severity)\s*(?:rating|level)\s*(?:is|of|:)?\s*(low|medium|high|critical))|"
    r"(?:rated|classified)\s+(?:as\s+)?(low|medium|high|critical)\s+risk", re.I)
RE_RECOMMEND = re.compile(r"\brecommend(?:ed|s|ation)?\b", re.I)
RE_REQUIREMENT = re.compile(r"\b(must|shall|shall not|is required to|are required to|prohibits?)\b", re.I)
SCOPE_RE = re.compile(
    r"\bfor\s+the\s+(?:supply|purchase|procurement|installation|upgrade)\s+of\s+([A-Za-z0-9][^,.;]{2,70})", re.I)


@dataclass
class DocContext:
    workspace_id: str
    version_id: str
    version_number: int = 1
    effective_date: date | None = None
    valid_until: date | None = None
    observed_date: date | None = None
    scope_hint: str = ""
    primary_entity: str | None = None
    currency: str | None = None
    is_current: bool = True
    entities: list[tuple[str, str, str]] = field(default_factory=list)  # (alias_lower, canonical, entity_id)


@dataclass
class ClaimDraft:
    predicate: str
    raw_value: str
    object_text: str
    confidence: float = 0.8
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    currency: str | None = None
    qualifiers: dict = field(default_factory=dict)
    polarity: int = 1
    scope: str = ""
    effective_date: date | None = None
    valid_until: date | None = None
    entity_name: str | None = None
    entity_id: str | None = None
    value_display: str | None = None


def _clean(text: str, limit: int = 500) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    return s[:limit]


def _money_draft(predicate: str, raw: str, ctx: DocContext, confidence: float,
                 qualifiers: dict | None = None, polarity: int = 1) -> ClaimDraft | None:
    value, currency, display = normalize_money(raw)
    if value is None:
        return None
    currency = currency or ctx.currency
    return ClaimDraft(
        predicate=predicate, raw_value=display, object_text=display, confidence=confidence,
        value_numeric=value, value_text=display, unit=None, currency=currency,
        qualifiers={"raw_value": display, "numeric_scope": "monetary", **(qualifiers or {})},
        polarity=polarity, value_display=display, effective_date=ctx.effective_date,
        valid_until=ctx.valid_until,
    )


def _select_entity(sentence: str, ctx: DocContext) -> tuple[str | None, str | None]:
    probe = sentence.lower()
    best: tuple[str, str, str] | None = None
    for alias_lower, canonical, eid in ctx.entities:
        if len(alias_lower) >= 3 and alias_lower in probe:
            if best is None or len(alias_lower) > len(best[0]):
                best = (alias_lower, canonical, eid)
    if best:
        return best[1], best[2]
    m = ORG_PATTERN.search(sentence)
    if m:
        return _clean(m.group(1), 200), None
    if ctx.primary_entity:
        return ctx.primary_entity, None
    return None, None


def _extract_sentence(sentence: str, ctx: DocContext) -> list[ClaimDraft]:
    probe = sentence.replace("_", " ")
    drafts: list[ClaimDraft] = []
    fired: set[str] = set()
    polarity = -1 if NEGATION_RE.search(probe) else 1
    scope = ctx.scope_hint
    m_scope = SCOPE_RE.search(sentence)
    if m_scope:
        scope = _clean(m_scope.group(1), 120)

    def add(draft: ClaimDraft | None, pred: str | None = None):
        if draft is None:
            return
        if pred:
            if pred in fired:
                return
            fired.add(pred)
        if not draft.scope:
            draft.scope = scope
        if draft.effective_date is None:
            draft.effective_date = ctx.effective_date
        if draft.valid_until is None:
            draft.valid_until = ctx.valid_until
        if draft.entity_name is None and draft.entity_id is None:
            draft.entity_name, draft.entity_id = _select_entity(sentence, ctx)
        drafts.append(draft)

    m = RE_UNIT_PRICE.search(probe)
    if m:
        add(_money_draft("unit_price", m.group(1), ctx, 0.93), "unit_price")
        fired.add("price")
    if not m:
        m = RE_PRICE_PER.search(probe)
        if m:
            add(_money_draft("unit_price", m.group(1), ctx, 0.9), "unit_price")
            fired.add("price")
    m = RE_PRICE_TOTAL.search(probe)
    if m:
        add(_money_draft("price", m.group(1), ctx, 0.9), "price")
    elif "unit_price" not in fired:
        m = RE_PRICE_GENERIC.search(probe)
        if m:
            add(_money_draft("price", m.group(1), ctx, 0.85), "price")
    if not {"price", "unit_price"} & fired:
        amounts = list(AMOUNT_RE.finditer(probe))
        if amounts and re.search(r"\b(price|cost|amount|quote[sd]?|fee|budget)\b", probe, re.I):
            add(_money_draft("price", amounts[0].group(0), ctx, 0.7), "price")

    m = RE_DISCOUNT.search(probe)
    if m:
        add(ClaimDraft("discount_percent", m.group(1) + "%", m.group(1) + "%",
                       value_numeric=normalize_percent(m.group(1)), unit="%",
                       confidence=0.9, effective_date=ctx.effective_date, valid_until=ctx.valid_until))

    m = RE_PAYMENT.search(probe)
    if m:
        raw = _clean(m.group(1), 40)
        days_m = re.search(r"(\d+)", raw)
        days = float(days_m.group(1)) if days_m else None
        add(ClaimDraft("payment_terms", raw, raw, value_numeric=days, unit="days",
                       value_text=normalize_text_value(raw), confidence=0.9,
                       qualifiers={"raw_value": raw}))

    m = RE_DELIVERY_DAYS.search(probe)
    if m:
        days, unit = normalize_duration(m.group(1), m.group(3))
        raw = _clean(m.group(0), 80)
        add(ClaimDraft("delivery_days", raw, raw, value_numeric=days, unit=unit, value_text=raw,
                       confidence=0.9, qualifiers={"raw_value": raw,
                                                    "raw_duration": f"{m.group(2) or ''}{m.group(3)}"}))

    m = RE_DELIVERY_DATE.search(probe)
    if m:
        d, ambiguous = normalize_date(m.group(1))
        if d:
            add(ClaimDraft("delivery_date", d.isoformat(), d.isoformat(), value_text=d.isoformat(),
                           confidence=0.9 if not ambiguous else 0.65,
                           qualifiers={"date_ambiguous": ambiguous}))

    m = RE_WARRANTY.search(probe)
    if m:
        days, unit = normalize_duration(m.group(1), m.group(2))
        raw = _clean(m.group(0), 60)
        add(ClaimDraft("warranty_days", raw, raw, value_numeric=days, unit="days", value_text=raw,
                       confidence=0.9, qualifiers={"raw_value": raw}))

    m = RE_VALID_UNTIL.search(probe)
    if m:
        d, ambiguous = normalize_date(m.group(1))
        if d:
            std = CERT_STD_RE.search(probe)
            add(ClaimDraft("valid_until", d.isoformat(), f"valid until {d.isoformat()}",
                           value_text=d.isoformat(), confidence=0.88 if not ambiguous else 0.6,
                           qualifiers={"date_ambiguous": ambiguous,
                                       "standard": normalize_standard(std.group(0)) if std else None}))

    m = RE_MILESTONE.search(probe)
    if m:
        d, ambiguous = normalize_date(m.group(1))
        if d:
            add(ClaimDraft("milestone_date", d.isoformat(), d.isoformat(), value_text=d.isoformat(),
                           confidence=0.88, qualifiers={"date_ambiguous": ambiguous}))

    m = RE_EFFECTIVE.search(probe)
    if m:
        d, ambiguous = normalize_date(m.group(1))
        if d:
            add(ClaimDraft("effective_date", d.isoformat(), d.isoformat(), value_text=d.isoformat(),
                           confidence=0.85, qualifiers={"date_ambiguous": ambiguous}))

    std = CERT_STD_RE.search(probe)
    if std:
        canonical = normalize_standard(std.group(0))
        has_cert_context = bool(re.search(r"certif|accredit|holds?|has been|maintains?", probe, re.I))
        is_requirement = bool(MODALITY_RE.search(probe)) and not has_cert_context
        if not is_requirement:
            add(ClaimDraft("certification", canonical, canonical, value_text=canonical,
                           confidence=0.85 if has_cert_context else 0.72,
                           qualifiers={"standard": canonical},
                           polarity=polarity), "certification")
    m = RE_CONFORMANCE.search(probe)
    if m:
        obj = _clean(m.group(1), 120)
        canonical = normalize_standard(obj)
        add(ClaimDraft("conformance", obj, obj, value_text=canonical, confidence=0.82,
                       qualifiers={"standard": canonical}, polarity=polarity))

    m = RE_UPTIME.search(probe)
    if m:
        add(ClaimDraft("sla_uptime", m.group(1) + "%", m.group(1) + "%",
                       value_numeric=normalize_percent(m.group(1)), unit="%", confidence=0.9))
    m = RE_EFFICIENCY.search(probe)
    if m:
        add(ClaimDraft("spec_efficiency", m.group(1) + "%", m.group(1) + "%",
                       value_numeric=normalize_percent(m.group(1)), unit="%", confidence=0.85))
    m = RE_ACCURACY.search(probe)
    if m:
        unit = (m.group(2) or "%").lower()
        add(ClaimDraft("spec_accuracy", m.group(0), m.group(0), value_numeric=parse_number(m.group(1)),
                       unit=unit, confidence=0.85))
    m = RE_TEMP.search(probe)
    if m:
        low = parse_number(m.group(1))
        high = parse_number(m.group(3))
        unit = m.group(2) or m.group(4) or "C"
        low_c, _ = normalize_temperature(low, unit)
        high_c, _ = normalize_temperature(high, unit)
        add(ClaimDraft("operating_temperature", _clean(m.group(0), 60), _clean(m.group(0), 60),
                       value_numeric=low_c, unit="C", confidence=0.85,
                       qualifiers={"high": high_c, "raw": m.group(0), "kind": "range"}))
    m = RE_WEIGHT.search(probe)
    if m:
        val, unit = convert_mass(parse_number(m.group(1)), m.group(2))
        add(ClaimDraft("spec_weight", m.group(0), m.group(0), value_numeric=val, unit=unit, confidence=0.85,
                       qualifiers={"raw_value": m.group(0)}))
    m = RE_POWER.search(probe)
    if m:
        val, unit = convert_power(parse_number(m.group(1)), m.group(2))
        add(ClaimDraft("spec_power", m.group(0), m.group(0), value_numeric=val, unit=unit, confidence=0.85,
                       qualifiers={"raw_value": m.group(0)}))
    m = RE_CAPACITY.search(probe)
    if m:
        unit_raw = m.group(2).lower()
        if unit_raw.startswith("unit"):
            add(ClaimDraft("spec_load_capacity", m.group(0), m.group(0),
                           value_numeric=parse_number(m.group(1)), unit="units", confidence=0.82,
                           qualifiers={"raw_value": m.group(0)}))
        else:
            val, unit = convert_mass(parse_number(m.group(1)), unit_raw)
            add(ClaimDraft("spec_load_capacity", m.group(0), m.group(0), value_numeric=val, unit=unit,
                           confidence=0.85, qualifiers={"raw_value": m.group(0)}))
    m = RE_THROUGHPUT.search(probe)
    if m:
        unit = _clean(m.group(2) or "units/day", 25)
        add(ClaimDraft("spec_throughput", m.group(0), m.group(0), value_numeric=parse_number(m.group(1)),
                       unit=unit, confidence=0.78))
    m = RE_RESOLUTION.search(probe)
    if m:
        add(ClaimDraft("spec_resolution", m.group(1), m.group(1),
                       value_text=normalize_text_value(m.group(1).replace("×", "x")), confidence=0.85))
    m = RE_IP.search(probe)
    if m:
        obj = "IP" + m.group(1)
        add(ClaimDraft("spec_ip_rating", obj, obj, value_text=obj.upper(), confidence=0.8))
    m = RE_NOISE.search(probe)
    if m:
        add(ClaimDraft("noise_level", m.group(0), m.group(0), value_numeric=parse_number(m.group(1)),
                       unit="dB", confidence=0.82))
    m = RE_QUANTITY.search(probe)
    if m:
        qty = parse_number(m.group(1))
        unit = _clean(m.group(2) or "units", 30)
        add(ClaimDraft("quantity", m.group(0), m.group(0), value_numeric=qty, unit=unit, confidence=0.85))
    m = RE_LIABILITY.search(probe)
    if m:
        add(_money_draft("liability_cap", m.group(1), ctx, 0.85), "liability_cap")
    m = RE_PENALTY.search(probe)
    if m:
        add(_money_draft("penalty", m.group(1), ctx, 0.8), "penalty")
    m = RE_INCOTERM.search(probe)
    if m:
        add(ClaimDraft("incoterm", m.group(1).upper(), m.group(1).upper(), value_text=m.group(1).upper(),
                       confidence=0.9))
    m = RE_RISK.search(probe)
    if m:
        level = (m.group(1) or m.group(2)).lower()
        add(ClaimDraft("risk_level", level, level, value_numeric=float(RISK_ORDINAL[level]),
                       value_text=level, unit="ordinal", confidence=0.82,
                       qualifiers={"ordinal": RISK_ORDINAL[level]}))
    if RE_RECOMMEND.search(probe):
        subject, eid = _select_entity(sentence, ctx)
        phrase = _clean(sentence, 240)
        add(ClaimDraft("recommendation", subject or phrase, phrase,
                       value_text=normalize_text_value(subject or phrase), confidence=0.6,
                       entity_name=subject, entity_id=eid))
    if RE_MITIGATION.search(probe) and "requirement_statement" not in fired:
        add(ClaimDraft("requirement_statement", _clean(sentence, 300), _clean(sentence, 300),
                       value_text=normalize_text_value(sentence), confidence=0.7,
                       qualifiers={"kind": "mitigation_plan"}), "requirement_statement")
    if RE_REQUIREMENT.search(probe) and not (fired & {"price", "unit_price", "delivery_days", "delivery_date", "certification"}):
        modality = RE_REQUIREMENT.search(probe).group(1).lower()
        add(ClaimDraft("requirement_statement", _clean(sentence, 240), _clean(sentence, 240),
                       value_text=normalize_text_value(sentence), confidence=0.72,
                       qualifiers={"modality": modality}))
    if re.search(r"\bpolicy\b", probe, re.I) and re.search(r"\b(must|shall|prohibits?)\b", probe, re.I):
        add(ClaimDraft("policy_statement", _clean(sentence, 240), _clean(sentence, 240),
                       value_text=normalize_text_value(sentence), confidence=0.72))
    return drafts


def validate_draft(d: ClaimDraft) -> list[str]:
    problems: list[str] = []
    if d.predicate not in P:
        problems.append(f"unknown predicate {d.predicate}")
    if not d.object_text or len(d.object_text) > 500:
        problems.append("object_text missing or too long")
    spec = P.get(d.predicate)
    if spec and spec.kind in {"money", "percent", "number", "duration", "ordinal"} and d.value_numeric is None:
        problems.append(f"numeric value required for {d.predicate}")
    if spec and spec.kind == "date" and not d.value_text:
        problems.append(f"date value required for {d.predicate}")
    return problems


def extract_claims_from_spans(spans: list[SourceSpan], ctx: DocContext) -> list[tuple[ClaimDraft, SourceSpan]]:
    out: list[tuple[ClaimDraft, SourceSpan]] = []
    for span in spans:
        if (span.meta or {}).get("injection"):
            continue
        text = span.text or ""
        meta = span.meta or {}
        atomic = (span.locator_type == "cell" or bool(meta.get("row_values"))
                  or bool(meta.get("values")) or bool(meta.get("row_label")))
        sentences = [(0, len(text), text)] if atomic else sentence_split(text)
        row_label = (span.meta or {}).get("row_label")
        span_ctx = ctx
        if atomic:
            row_entity, row_entity_id = _select_entity(text, ctx)
            if row_entity:
                span_ctx = replace(ctx, primary_entity=row_entity)
        for _, _, sentence in sentences:
            if len(sentence) < 5 or len(sentence) > 900:
                continue
            for draft in _extract_sentence(sentence, span_ctx):
                if validate_draft(draft):
                    continue
                if row_label:
                    draft.scope = str(row_label)[:200]
                draft.qualifiers = {**(draft.qualifiers or {}),
                                    "sentence": sentence[:900],
                                    "sentence_len": len(sentence)}
                out.append((draft, span))
                if len(out) >= 700:
                    return out
    return out


def claim_key_for(entity: Entity | None, entity_name: str | None, predicate: str) -> str:
    base = entity_key(entity.canonical_name) if entity else (entity_key(entity_name) if entity_name else "unbound")
    return f"{base}|{predicate}"


def extract_and_store_claims(db: Session, version: DocumentVersion, spans: list[SourceSpan],
                             ctx: DocContext) -> list[Claim]:
    """Idempotently (re)build claims for a version. Removes prior derived data first."""
    old_claim_ids = [c.id for c in db.execute(select(Claim).where(Claim.version_id == version.id)).scalars()]
    if old_claim_ids:
        db.execute(delete(Edge).where(Edge.workspace_id == version.workspace_id,
                                      ((Edge.source_type == "Claim") & Edge.source_id.in_(old_claim_ids)) |
                                      ((Edge.target_type == "Claim") & Edge.target_id.in_(old_claim_ids))))
        db.execute(delete(Claim).where(Claim.version_id == version.id))
        db.flush()
    if ctx.currency is None:
        for span in spans:
            for m in AMOUNT_RE.finditer(span.text or ""):
                _, cur, _ = normalize_money(m.group(0))
                if cur:
                    ctx.currency = cur
                    break
            if ctx.currency:
                break
    drafts = extract_claims_from_spans(spans, ctx)
    prior = db.execute(
        select(Claim, DocumentVersion).join(DocumentVersion, Claim.version_id == DocumentVersion.id)
        .where(DocumentVersion.document_id == version.document_id, Claim.version_id != version.id,
               DocumentVersion.version_number < version.version_number)
        .order_by(DocumentVersion.version_number.desc())
    ).all()
    prior_index: dict[str, Claim] = {}
    for c, _v in prior:
        prior_index.setdefault(c.claim_key, c)
    created: list[Claim] = []
    seen: dict[tuple, Claim] = {}
    for draft, span in drafts:
        entity: Entity | None = None
        if draft.entity_id:
            entity = db.get(Entity, draft.entity_id)
        elif draft.entity_name:
            from app.services.entities import get_or_create_entity
            entity = get_or_create_entity(db, version.workspace_id, draft.entity_name)
        subject = entity.canonical_name if entity else (draft.entity_name or "")
        key = claim_key_for(entity, draft.entity_name, draft.predicate)
        claim = Claim(
            workspace_id=version.workspace_id, version_id=version.id, span_id=span.id,
            entity_id=entity.id if entity else None, subject_text=subject, predicate=draft.predicate,
            claim_key=key, object_text=draft.object_text, quoted_span=draft.qualifiers["sentence"],
            value_numeric=draft.value_numeric, value_text=draft.value_text, unit=draft.unit,
            currency=draft.currency, scope=draft.scope, qualifiers=draft.qualifiers,
            polarity=draft.polarity, effective_date=draft.effective_date, valid_until=draft.valid_until,
            observed_date=ctx.observed_date, extraction_method="rule_based",
            extraction_confidence=draft.confidence, verification_status="unverified",
            meta={"version_number": ctx.version_number, "is_current_version": ctx.is_current,
                  "raw_value": draft.raw_value},
        )
        db.add(claim)
        db.flush()
        dedupe_key = (claim.claim_key, claim.entity_id or "",
                      normalize_text_value(claim.scope or ""),
                      round(claim.value_numeric, 6) if claim.value_numeric is not None else None,
                      normalize_text_value(claim.value_text or ""), claim.polarity)
        if dedupe_key in seen:
            claim.duplicate_of_id = seen[dedupe_key].id
            claim.meta = {**claim.meta, "duplicate": True}
        else:
            seen[dedupe_key] = claim
        prior_claim = prior_index.get(key)
        if prior_claim and prior_claim.id != claim.id:
            same_value = (
                (prior_claim.value_numeric is not None and claim.value_numeric is not None and
                 abs(prior_claim.value_numeric - claim.value_numeric) < 1e-9 and
                 (prior_claim.unit or "") == (claim.unit or "") and (prior_claim.currency or "") == (claim.currency or ""))
                or normalize_text_value(prior_claim.value_text or "") == normalize_text_value(claim.value_text or "")
            )
            claim.meta = {**claim.meta, "revision_of": prior_claim.id,
                          "changed_from_previous_version": not same_value}
            if not same_value:
                db.add(Edge(
                    workspace_id=version.workspace_id, source_type="Claim", source_id=claim.id,
                    target_type="Claim", target_id=prior_claim.id, relation="INVALIDATES",
                    provenance={"document_id": version.document_id, "version_id": version.id,
                                "prior_version_id": prior_claim.version_id},
                    justification=f"New version {ctx.version_number} changes {claim.predicate} value "
                                  f"from {prior_claim.object_text!r} to {claim.object_text!r}",
                    confidence=0.95, created_by="claim_revision",
                ))
                prior_claim.meta = {**(prior_claim.meta or {}), "superseded_by_claim": claim.id}
        db.add(Edge(
            workspace_id=version.workspace_id, source_type="Claim", source_id=claim.id,
            target_type="SourceSpan", target_id=span.id, relation="DERIVED_FROM",
            provenance={"version_id": version.id, "locator": span.locator_type,
                        "page": span.page, "sheet": span.sheet, "cell": span.cell_range},
            justification="Claim extracted from this exact source span", confidence=claim.extraction_confidence,
        ))
        if entity:
            db.add(Edge(
                workspace_id=version.workspace_id, source_type="Claim", source_id=claim.id,
                target_type="Entity", target_id=entity.id, relation="MENTIONS", confidence=0.9,
                justification="Subject entity resolved during extraction",
            ))
        created.append(claim)
    db.flush()
    return created


def citation_resolves(claim: Claim, span: SourceSpan | None) -> tuple[bool, str]:
    if span is None:
        return False, "cited source span no longer exists"
    span_text = (span.text or "").lower()
    quote = (claim.quoted_span or "").lower()
    if not quote:
        return False, "claim has no quoted source span"
    norm_span = re.sub(r"\s+", " ", span_text).strip()
    norm_quote = re.sub(r"\s+", " ", quote).strip()
    if norm_quote in norm_span:
        return True, ""
    # Spreadsheet/JSON cells may be truncated in the quote; compare the short object value too.
    if claim.object_text and re.sub(r"\s+", " ", claim.object_text.lower()) in norm_span:
        return True, ""
    return False, "quoted span does not resolve to the cited source content"
