from __future__ import annotations

from pathlib import Path

from app.parsers.base import ParsedDocument, ParserError
from app.parsers.docx_parser import parse_docx
from app.parsers.email_parser import parse_eml
from app.parsers.pdf_parser import parse_pdf
from app.parsers.text_parser import parse_csv, parse_json, parse_textlik
from app.parsers.xlsx_parser import parse_xlsx

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", ".json", ".eml"}
MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv", ".txt": "text/plain", ".md": "text/markdown",
    ".json": "application/json", ".eml": "message/rfc822",
}
# magic-number checks to reject mislabeled/unsafe uploads before parsing
MAGIC = {
    ".pdf": (b"%PDF",), ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",),
}


def parse_document(filename: str, data: bytes) -> ParsedDocument:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParserError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    for magic in MAGIC.get(ext, ()):
        if not data.startswith(magic):
            raise ParserError(f"File content does not match the '{ext}' format (magic-number check failed)")
    if ext == ".pdf":
        doc = parse_pdf(data, filename)
    elif ext == ".docx":
        doc = parse_docx(data, filename)
    elif ext == ".xlsx":
        doc = parse_xlsx(data, filename)
    elif ext == ".csv":
        doc = parse_csv(data, filename)
    elif ext == ".json":
        doc = parse_json(data, filename)
    elif ext == ".eml":
        doc = parse_eml(data, filename)
    else:
        doc = parse_textlik(data, filename)
    if not doc.spans:
        doc.warnings.append("No extractable content spans were produced")
    return doc


def mime_for(filename: str) -> str:
    return MIME_BY_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")
