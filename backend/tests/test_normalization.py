"""Unit tests for numeric, currency, date and duration normalization."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.normalize import (normalize_date, normalize_duration, normalize_money,
                                    normalize_percent, normalize_standard, normalize_temperature,
                                    sentence_split)


@pytest.mark.parametrize("raw,value,currency", [
    ("USD 1.2 million", 1_200_000, "USD"),
    ("$ 12,500.50", 12_500.50, "USD"),
    ("₹ 4,50,000", 450_000, "INR"),
    ("EUR 88,500", 88_500, "EUR"),
    ("usd 9800", 9_800, "USD"),
])
def test_money_normalization(raw, value, currency):
    amount, cur, _display = normalize_money(raw)
    assert amount == pytest.approx(value)
    assert cur == currency


def test_percent_and_standard():
    assert normalize_percent("94%") == pytest.approx(94)
    assert normalize_standard("iso 9001:2015") == "ISO 9001"
    assert normalize_standard("IEC 61508") == "IEC 61508"


@pytest.mark.parametrize("value,unit,expected_days", [
    (45, "days", 45), (6, "weeks", 42), (18, "months", 540), (2, "years", 730), (1, "year", 365),
])
def test_duration_normalization(value, unit, expected_days):
    days, normalized_unit = normalize_duration(str(value), unit)
    assert days == expected_days
    assert normalized_unit == "days"


def test_date_normalization_and_ambiguity():
    parsed, ambiguous = normalize_date("2025-07-15")
    assert parsed == date(2025, 7, 15) and ambiguous is False
    parsed, ambiguous = normalize_date("15/07/2025")
    assert parsed == date(2025, 7, 15) and ambiguous is False
    parsed, ambiguous = normalize_date("03/04/2025")
    assert parsed == date(2025, 4, 3) and ambiguous is True  # DD/MM default is flagged
    assert normalize_date("not a date")[0] is None


def test_temperature_conversion():
    value, unit = normalize_temperature(32, "F")
    assert unit == "C" and value == pytest.approx(0, abs=1e-6)


def test_sentence_split_offsets_are_valid():
    text = "Unit price: USD 10. Delivery: within 45 days of purchase order. Warranty: 24 months."
    sentences = sentence_split(text)
    assert len(sentences) >= 2
    for start, end, sentence in sentences:
        assert text[start:end].strip() == sentence.strip()
        assert 0 <= start < end <= len(text)
