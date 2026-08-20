"""Typed predicate catalog: comparison semantics shared by extraction, verification and gates."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PredicateSpec:
    code: str
    label: str
    kind: str  # money|percent|number|duration|date|text|ordinal|range|standard
    direction: str = "neutral"  # min|max|neutral
    comparable: bool = True
    unit: str | None = None


P: dict[str, PredicateSpec] = {p.code: p for p in [
    PredicateSpec("unit_price", "Unit price", "money", "min"),
    PredicateSpec("price", "Total price", "money", "min"),
    PredicateSpec("discount_percent", "Discount", "percent", "max"),
    PredicateSpec("payment_terms", "Payment terms", "text", comparable=True),
    PredicateSpec("delivery_days", "Delivery lead time", "duration", "min", unit="days"),
    PredicateSpec("delivery_date", "Delivery date", "date", "min"),
    PredicateSpec("milestone_date", "Milestone date", "date", comparable=False),
    PredicateSpec("warranty_days", "Warranty", "duration", "max", unit="days"),
    PredicateSpec("certification", "Certification held", "standard"),
    PredicateSpec("conformance", "Standards conformance", "standard"),
    PredicateSpec("valid_until", "Validity / expiry date", "date", "max"),
    PredicateSpec("effective_date", "Effective date", "date"),
    PredicateSpec("sla_uptime", "SLA uptime", "percent", "max"),
    PredicateSpec("spec_efficiency", "Efficiency", "percent", "max"),
    PredicateSpec("spec_accuracy", "Accuracy", "number", "min"),
    PredicateSpec("operating_temperature", "Operating temperature range", "range"),
    PredicateSpec("spec_weight", "Weight", "number", "min", unit="kg"),
    PredicateSpec("spec_power", "Power rating", "number", "neutral", unit="W"),
    PredicateSpec("spec_load_capacity", "Load capacity", "number", "max", unit="kg"),
    PredicateSpec("spec_throughput", "Throughput", "number", "max"),
    PredicateSpec("spec_resolution", "Resolution", "text"),
    PredicateSpec("spec_ip_rating", "IP rating", "text"),
    PredicateSpec("noise_level", "Noise level", "number", "min"),
    PredicateSpec("quantity", "Quantity", "number", "neutral"),
    PredicateSpec("liability_cap", "Liability cap", "money", "max"),
    PredicateSpec("penalty", "Penalty", "money", "max"),
    PredicateSpec("risk_level", "Risk rating", "ordinal", "min"),
    PredicateSpec("incoterm", "Incoterm", "text", comparable=False),
    PredicateSpec("recommendation", "Recommendation", "text", comparable=False),
    PredicateSpec("requirement_statement", "Requirement statement", "text", comparable=False),
    PredicateSpec("policy_statement", "Policy statement", "text", comparable=False),
    PredicateSpec("compliance_declaration", "Compliance declaration", "standard"),
    PredicateSpec("certificate_number", "Certificate number", "text", comparable=False),
]}

RISK_ORDINAL = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Predicates whose equality differences are contractually meaningful.
CONTRACTUAL = {"unit_price", "price", "payment_terms", "delivery_days", "delivery_date", "warranty_days",
               "certification", "conformance", "liability_cap", "penalty", "incoterm", "discount_percent",
               "sla_uptime", "certificate_number"}
TECHNICAL = {"spec_efficiency", "spec_accuracy", "operating_temperature", "spec_weight", "spec_power",
             "spec_load_capacity", "spec_throughput", "spec_resolution", "spec_ip_rating", "noise_level",
             "conformance", "certification"}

PRICE_PREDICATES = {"unit_price", "price"}
DELIVERY_PREDICATES = {"delivery_days", "delivery_date"}
CERTIFICATION_PREDICATES = {"certification", "conformance"}
