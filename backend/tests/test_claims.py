"""Claim extraction, normalization and citation-integrity unit tests."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.claims import DocContext, _extract_sentence, citation_resolves, validate_draft
from app.services.claims import ClaimDraft


@pytest.fixture()
def ctx():
    return DocContext(workspace_id="w", version_id="v", effective_date=date(2025, 7, 1),
                      valid_until=date(2025, 12, 31), scope_hint="Supply of compressors", currency="USD")


def extract(sentence: str, ctx: DocContext):
    return {d.predicate: d for d in _extract_sentence(sentence, ctx)}


def test_price_and_unit_price_are_separate_predicates(ctx):
    claims = extract("Unit price: USD 10,080 per compressor", ctx)
    assert claims["unit_price"].value_numeric == 10080
    claims = extract("Total price: USD 252,000 for 25 units", ctx)
    assert claims["price"].value_numeric == 252000


@pytest.mark.parametrize("sentence,days", [
    ("Delivery: within 45 days of purchase order", 45),
    ("Delivery within 60 days", 60),
    ("delivery time: 30 days", 30),
    ("Lead time 6 weeks", 42),
])
def test_delivery_lead_time(ctx, sentence, days):
    claims = extract(sentence, ctx)
    assert claims["delivery_days"].value_numeric == days
    assert claims["delivery_days"].unit == "days"


def test_delivery_tolerance_is_absolute(ctx):
    """Regression: a relative tolerance made 5-day delivery differences look 'equal'."""
    from app.services.contradictions import values_equal
    a = extract("Delivery: within 50 days of purchase order", ctx)["delivery_days"]
    b = extract("Delivery: within 45 days of purchase order", ctx)["delivery_days"]
    assert not values_equal(a, b)
    eq = extract("Delivery: within 45 days of purchase order", ctx)["delivery_days"]
    assert values_equal(b, eq)


def test_warranty_and_payment_and_certification(ctx):
    assert extract("Warranty: 24 months from commissioning", ctx)["warranty_days"].value_numeric == 720
    assert extract("Payment terms: Net 30", ctx)["payment_terms"].value_numeric == 30
    assert extract("Certification: Apex Systems Pvt Ltd holds ISO 9001:2015 certification.", ctx)[
        "certification"].value_text == "ISO 9001"


def test_certification_negation_polarity(ctx):
    claim = extract("Vendor no longer holds ISO 9001 certification.", ctx)["certification"]
    assert claim.polarity == -1


def test_validate_draft_rejects_numeric_predicate_without_value(ctx):
    bad = ClaimDraft("price", "USD 10", "USD 10", value_numeric=None)
    problems = validate_draft(bad)
    assert problems and "numeric value" in problems[0]


def test_quote_must_resolve_to_span():
    class FakeClaim:
        def __init__(self, quote):
            self.quoted_span = quote
            self.object_text = quote

    class FakeSpan:
        def __init__(self, text):
            self.text = text

    assert citation_resolves(FakeClaim("USD 245,000"), FakeSpan("Total price:  USD 245,000 for 25 units"))[0]
    resolves, reason = citation_resolves(FakeClaim("USD 999,999"), FakeSpan("Total price: USD 245,000"))
    assert not resolves and "does not resolve" in reason.lower()


def test_milestone_and_requirement_extraction(ctx):
    claims = extract("Milestone date: 2025-10-30", ctx)
    assert claims["milestone_date"].value_text == "2025-10-30"
    claims = extract("The upgraded controller must achieve efficiency of 94% at rated load.", ctx)
    assert "requirement_statement" in claims
