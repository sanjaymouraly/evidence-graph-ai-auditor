"""Parser contracts and shared text-normalization utilities."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class SpanData:
    locator_type: str
    text: str
    ordinal: int = 0
    page: int | None = None
    bbox: list[float] | None = None
    paragraph_index: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    char_start: int = 0
    char_end: int = 0
    section_label: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class ParsedDocument:
    spans: list[SpanData]
    text: str = ""
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    sheets: dict[str, list[list[dict]]] = field(default_factory=dict)


class ParserError(Exception):
    pass


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WHITESPACE = re.compile(r"[ \t\u00a0]+")


def normalize_text(text: str) -> str:
    """NFKC-normalize but keep line breaks stable so character offsets remain meaningful."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_CHARS.sub("", text)
    lines = [WHITESPACE.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(lines)


def split_paragraphs(text: str, min_len: int = 2) -> list[tuple[int, int, str]]:
    """Split text on blank lines, preserving exact character offsets into `text`."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for block in re.split(r"\n\s*\n", text):
        start = text.find(block, pos)
        if start < 0:
            start = pos
        end = start + len(block)
        pos = end
        stripped = block.strip()
        if len(stripped) >= min_len:
            leading = len(block) - len(block.lstrip())
            out.append((start + leading, start + leading + len(stripped), stripped))
    return out


def decode_bytes(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) (instructions|prompts?|rules)",
    r"disregard (all )?(previous|prior|system) (instructions|prompts?)",
    r"you are now (a|an|the)\b",
    r"reveal (the )?(system prompt|hidden instructions|api key|credentials)",
    r"(send|post|exfiltrate|upload) .{0,60}(to|at) https?://",
    r"do not (mention|report|disclose) (this|these) (instruction|document)",
    r"override (the )?(gate|approval|policy)",
    r"mark (this|the) decision as (approved|sufficient)",
]
INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> list[str]:
    return sorted({m.group(0)[:120] for m in INJECTION_RE.finditer(text)})


def find_phrase_bbox(line_boxes: list[list], quote: str) -> list[float] | None:
    """Return the union bbox for the first line-block containing `quote` (normalized)."""
    q = re.sub(r"\s+", " ", quote).strip().lower()
    if not q:
        return None
    for line_text, box in line_boxes:
        if q in re.sub(r"\s+", " ", line_text).lower():
            return box
    return None
