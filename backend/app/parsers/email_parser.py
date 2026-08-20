from __future__ import annotations

import email
from email import policy

from app.parsers.base import ParsedDocument, SpanData, decode_bytes, normalize_text, split_paragraphs


def parse_eml(data: bytes, filename: str) -> ParsedDocument:
    msg = email.message_from_bytes(data, policy=policy.default)
    headers = {
        "message_id": msg.get("Message-ID", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "subject": msg.get("Subject", ""),
        "date": msg.get("Date", ""),
    }
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                payload = part.get_payload(decode=True) or b""
                body, _ = decode_bytes(payload)
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html" and part.get_content_disposition() != "attachment":
                    import re
                    raw = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
                    body = re.sub(r"<[^>]+>", " ", raw)
                    break
    else:
        body = msg.get_body(preferencelist=("plain",)).get_content() if msg.get_body() else ""
    body = normalize_text(body)
    spans: list[SpanData] = []
    ordinal = 0
    header_text = " | ".join(f"{k}: {v}" for k, v in headers.items() if v)
    if header_text:
        spans.append(SpanData(locator_type="email_section", section_label="headers",
                              text=header_text, ordinal=ordinal,
                              meta={"section": "headers", **headers}))
        ordinal += 1
    offset = len(header_text) + 2
    for i, (start, end, para) in enumerate(split_paragraphs(body)):
        spans.append(SpanData(locator_type="email_section", section_label=f"body ¶{i + 1}",
                              text=para, ordinal=ordinal, char_start=offset + start,
                              char_end=offset + end, meta={"section": f"body ¶{i + 1}"}))
        ordinal += 1
    return ParsedDocument(spans=spans, text=body, metadata={"email": headers},
                          warnings=[] if body else ["Email has no plain-text body"])
