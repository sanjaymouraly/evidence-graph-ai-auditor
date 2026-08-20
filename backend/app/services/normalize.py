"""Deterministic parsing/normalization of numbers, money, units, dates and standards."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
CURRENCY_WORDS = {"dollar": "USD", "dollars": "USD", "usd": "USD", "euro": "EUR", "euros": "EUR",
                  "eur": "EUR", "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "rupee": "INR",
                  "rupees": "INR", "inr": "INR", "sgd": "SGD", "aud": "AUD", "cad": "CAD", "jpy": "JPY"}
MAGNITUDE = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000,
             "b": 1_000_000_000, "billion": 1_000_000_000}

MASS_FACTORS = {"mg": 0.000001, "g": 0.001, "kg": 1.0, "lb": 0.45359237, "lbs": 0.45359237,
                "t": 1000.0, "tonne": 1000.0, "tonnes": 1000.0, "ton": 1000.0, "tons": 1000.0}
DURATION_FACTORS = {"hour": 1 / 24, "hours": 1 / 24, "day": 1.0, "days": 1.0, "week": 7.0,
                    "weeks": 7.0, "month": 30.0, "months": 30.0, "year": 365.0, "years": 365.0}
POWER_FACTORS = {"w": 1.0, "kw": 1000.0, "mw": 1_000_000.0, "hp": 745.7}
TEMPERATURE_UNITS = {"c": "C", "°c": "C", "f": "F", "°f": "F"}

DATE_SRC = (r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|"
            r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}|"
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})")
DATE_RE = re.compile(DATE_SRC, re.IGNORECASE)
AMOUNT_SRC = (r"(?:(?:USD|EUR|INR|GBP|SGD|AUD|CAD|JPY|\$|€|£|₹|¥)\s?)?"
              r"\d[\d,]*(?:\.\d+)?\s?(?:[KMB]\b|thousand\b|million\b|billion\b)?"
              r"(?:\s?(?:USD|EUR|INR|GBP|SGD|AUD|CAD|JPY|dollars?|euros?|rupees?|pounds?))?")
AMOUNT_RE = re.compile(r"(?<![\w.])" + AMOUNT_SRC + r"(?![\w])", re.IGNORECASE)


def parse_number(raw: str) -> float | None:
    s = (raw or "").strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        # Decide decimal separator by the right-most occurrence
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3):
            s = "".join(parts)
        else:
            s = s.replace(",", ".")
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return None


def normalize_money(raw: str) -> tuple[float | None, str | None, str]:
    text = unicodedata.normalize("NFKC", raw or "")
    if not text:
        return None, None, ""
    currency = None
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in text:
            currency = code
            text = text.replace(sym, " ")
    low = text.lower()
    for word, code in CURRENCY_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            currency = currency or code
            low = re.sub(rf"\b{re.escape(word)}\b", " ", low)
    mult = 1.0
    for mag, factor in MAGNITUDE.items():
        if re.search(rf"\b(?:{mag})\b", low):
            mult = factor
            low = re.sub(rf"\b{mag}\b", " ", low)
            break
    m = re.search(r"\d[\d,]*(?:\.\d+)?", low)
    if not m:
        return None, currency, raw.strip()
    value = parse_number(m.group(0))
    if value is None:
        return None, currency, raw.strip()
    return round(value * mult, 4), currency, raw.strip()


def normalize_percent(raw: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)", raw or "")
    return parse_number(m.group(1)) if m else None


def normalize_duration(value_raw: str, unit_raw: str) -> tuple[float | None, str]:
    value = parse_number(value_raw)
    unit = (unit_raw or "").strip().lower().rstrip("s") + "s"
    if unit not in DURATION_FACTORS:
        unit = "days"
    if value is None:
        return None, "days"
    return round(value * DURATION_FACTORS[unit], 3), "days"


def convert_mass(value: float | None, unit: str | None) -> tuple[float | None, str | None]:
    if value is None:
        return None, unit
    u = (unit or "kg").lower().rstrip(".")
    if u not in MASS_FACTORS:
        return value, u
    return round(value * MASS_FACTORS[u], 6), "kg"


def convert_power(value: float | None, unit: str | None) -> tuple[float | None, str | None]:
    if value is None:
        return None, unit
    u = (unit or "kw").lower()
    if u not in POWER_FACTORS:
        return value, u
    return round(value * POWER_FACTORS[u], 4), "W"


def normalize_temperature(value: float | None, unit: str | None) -> tuple[float | None, str]:
    u = (unit or "C").lower().replace("°", "")
    if value is None:
        return None, "C"
    if u == "f":
        return round((value - 32) * 5 / 9, 2), "C"
    return value, "C"


def normalize_date(raw: str) -> tuple[date | None, bool]:
    """Parse common business date formats. Returns (date, ambiguous_day_month_order)."""
    s = (raw or "").strip().replace("Sept", "Sep")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    s = s.strip(" .,;")
    ambiguous = False
    try:
        return datetime.fromisoformat(s[:10]).date(), False
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})$", s)
    if m:
        month = MONTHS.get(m.group(2)[:3].lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1))), False
            except ValueError:
                return None, False
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        month = MONTHS.get(m.group(1)[:3].lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2))), False
            except ValueError:
                return None, False
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        # ISO-like 4-digit-year-last formats with day>12 are unambiguous
        if a > 12 and b <= 12:
            day, month = a, b
        elif b > 12 and a <= 12:
            day, month = b, a
        else:
            day, month = a, b  # business default: DD/MM
            ambiguous = True
        try:
            return date(y, month, day), ambiguous
        except ValueError:
            return None, False
    return None, False


def format_date(d: date | None) -> str:
    return d.isoformat() if d else ""


STANDARD_ALIASES = {
    r"\biso\s*9001(?::\d{4})?\b": "ISO 9001",
    r"\biso\s*14001(?::\d{4})?\b": "ISO 14001",
    r"\biso\s*45001(?::\d{4})?\b": "ISO 45001",
    r"\biso\s*27001(?::\d{4})?\b": "ISO 27001",
    r"\biso\s*13485(?::\d{4})?\b": "ISO 13485",
    r"\biso\s*50001(?::\d{4})?\b": "ISO 50001",
    r"\biec\s*61508(?:\s*sil\s*[1-4])?\b": "IEC 61508",
    r"\biso\s*13849(?:\s*pl\s*[a-e])?\b": "ISO 13849",
    r"\biec\s*62443\b": "IEC 62443",
    r"\bsoc\s*2\b": "SOC 2",
    r"\brohs\b": "RoHS",
    r"\breach\b": "REACH",
    r"\bce\b(?!\w)": "CE",
    r"\bul\s*\d+\b": "UL",
    r"\batex\b": "ATEX",
    r"\bapi\s*610\b": "API 610",
}


def normalize_standard(raw: str) -> str:
    text = (raw or "").lower()
    for pattern, canonical in STANDARD_ALIASES.items():
        if re.search(pattern, text):
            return canonical
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (raw or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()[:120]


def normalize_text_value(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw or "").lower()
    s = re.sub(r"\b(private|pvt\.?)\s+limited\b", "ltd", s)
    s = re.sub(r"[^a-z0-9%.:/ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sentence_split(text: str) -> list[tuple[int, int, str]]:
    """Sentence splitting with offsets; conservative about decimals and abbreviations."""
    out: list[tuple[int, int, str]] = []
    start = 0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in ".!?;\n":
            if ch == "." and i > 0 and i + 1 < n and text[i - 1].isdigit() and text[i + 1].isdigit():
                i += 1
                continue
            if ch == "." and re.search(r"\b(?:p|no|approv|inc|ltd|co|vs|e\.g|i\.e)\.$", text[start:i + 1], re.I):
                i += 1
                continue
            end = i + 1
            chunk = text[start:end].strip()
            if chunk:
                leading = len(text[start:end]) - len(text[start:end].lstrip())
                out.append((start + leading, end, chunk))
            start = end
        i += 1
    tail = text[start:].strip()
    if tail:
        leading = len(text[start:]) - len(text[start:].lstrip())
        out.append((start + leading, len(text), tail))
    return [(s, e, c) for s, e, c in out if c]


def overlap(a_start: date | None, a_end: date | None, b_start: date | None, b_end: date | None,
            default=date(1900, 1, 1), far=date(2999, 12, 31)) -> bool:
    a0 = a_start or default
    a1 = a_end or far
    b0 = b_start or default
    b1 = b_end or far
    return a0 <= b1 and b0 <= a1
