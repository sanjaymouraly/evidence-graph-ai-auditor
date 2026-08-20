from __future__ import annotations

import io
import logging

from app.config import get_settings
from app.parsers.base import ParsedDocument, ParserError, SpanData, normalize_text, split_paragraphs

log = logging.getLogger(__name__)


def _lines_with_boxes(page) -> list[list]:
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    lines: list[list] = []
    current: list[dict] = []
    for w in words:
        if current and abs(w["top"] - current[-1]["top"]) > 3:
            lines.append(current)
            current = []
        current.append(w)
    if current:
        lines.append(current)
    out = []
    for line in lines:
        text = " ".join(w["text"] for w in line)
        box = [round(min(w["x0"] for w in line), 2), round(min(w["top"] for w in line), 2),
               round(max(w["x1"] for w in line), 2), round(max(w["bottom"] for w in line), 2)]
        out.append([text, box])
    return out


def parse_pdf(data: bytes, filename: str) -> ParsedDocument:
    settings = get_settings()
    pages_text: list[str] = []
    pages_boxes: list[list[list]] = []
    warnings: list[str] = []
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > settings.max_pdf_pages:
                raise ParserError(f"PDF exceeds {settings.max_pdf_pages} page limit")
            for page in pdf.pages:
                text = normalize_text(page.extract_text() or "")
                pages_text.append(text)
                pages_boxes.append(_lines_with_boxes(page))
    except ParserError:
        raise
    except Exception as exc:  # pragma: no cover - fallback path
        log.warning("pdfplumber failed for %s: %s; trying pypdf", filename, exc)
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if len(reader.pages) > settings.max_pdf_pages:
                raise ParserError(f"PDF exceeds {settings.max_pdf_pages} page limit")
            for p in reader.pages:
                pages_text.append(normalize_text(p.extract_text() or ""))
                pages_boxes.append([])
            warnings.append("PDF parsed with pypdf fallback; bounding boxes unavailable")
        except ParserError:
            raise
        except Exception as exc2:
            raise ParserError(f"Unable to parse PDF: {exc2}") from exc2
    if not any(t.strip() for t in pages_text):
        warnings.append("PDF has no extractable text layer; OCR is not enabled in this deployment")
    spans: list[SpanData] = []
    ordinal = 0
    for page_no, (text, boxes) in enumerate(zip(pages_text, pages_boxes), start=1):
        if not text.strip():
            continue
        paragraphs = split_paragraphs(text, min_len=2)
        if not paragraphs:
            paragraphs = [(0, len(text), text)]
        for start, end, para in paragraphs:
            spans.append(SpanData(
                locator_type="page", page=page_no, char_start=start, char_end=end,
                text=para, ordinal=ordinal, section_label=f"p.{page_no}",
                meta={"line_boxes": boxes},
            ))
            ordinal += 1
    full_text = "\n\n".join(pages_text)
    return ParsedDocument(
        spans=spans, text=full_text,
        metadata={"page_count": len(pages_text), "has_text_layer": any(t.strip() for t in pages_text)},
        warnings=warnings,
    )


def guess_pdf_metadata(data: bytes) -> dict:
    try:
        from pypdf import PdfReader
        info = PdfReader(io.BytesIO(data)).metadata or {}
        return {"pdf_title": str(info.get("/Title") or ""), "pdf_author": str(info.get("/Author") or "")}
    except Exception:
        return {}
