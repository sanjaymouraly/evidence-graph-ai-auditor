from __future__ import annotations

import csv
import io
import json

from app.parsers.base import ParsedDocument, ParserError, SpanData, decode_bytes, normalize_text, split_paragraphs


def parse_textlik(data: bytes, filename: str) -> ParsedDocument:
    text, encoding = decode_bytes(data)
    text = normalize_text(text)
    spans: list[SpanData] = []
    for idx, (start, end, para) in enumerate(split_paragraphs(text)):
        spans.append(SpanData(locator_type="text", text=para, ordinal=idx,
                              char_start=start, char_end=end, section_label=f"¶{idx + 1}"))
    return ParsedDocument(spans=spans, text=text, metadata={"encoding": encoding,
                                                            "characters": len(text)})


def parse_csv(data: bytes, filename: str) -> ParsedDocument:
    text, encoding = decode_bytes(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    spans: list[SpanData] = []
    offset = 0
    out_rows: list[list[dict]] = []
    header = rows[0] if rows else []
    for r_idx, row in enumerate(rows):
        label = f"row {r_idx + 1}"
        if header and r_idx > 0:
            pairs = [f"{header[i] if i < len(header) else f'col{i+1}'}: {v}" for i, v in enumerate(row) if v]
            if pairs:
                label = f"row {r_idx + 1} — " + "; ".join(pairs)
        spans.append(SpanData(locator_type="text", text=f"{label}", ordinal=r_idx,
                              char_start=offset, char_end=offset + len(label),
                              section_label=f"row {r_idx + 1}",
                              meta={"row": r_idx + 1, "values": row,
                                    "row_label": row[0] if row else None,
                                    "columns": {header[i]: row[i] for i in range(min(len(header), len(row)))}}))
        offset += len(label) + 1
        out_rows.append([{"cell": f"{chr(65 + min(i, 25))}{r_idx + 1}", "value": v} for i, v in enumerate(row)])
    return ParsedDocument(spans=spans, text="\n".join(s.text for s in spans),
                          metadata={"rows": len(rows), "columns": header, "encoding": encoding},
                          sheets={filename: out_rows})


def parse_json(data: bytes, filename: str) -> ParsedDocument:
    text, encoding = decode_bytes(data)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParserError(f"Invalid JSON: {exc}") from exc
    flat: list[tuple[str, str]] = []

    def walk(node, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            flat.append((path, "" if node is None else str(node)))

    walk(obj, "")
    spans: list[SpanData] = []
    offset = 0
    for i, (k, v) in enumerate(flat):
        label = f"{k}: {v}"
        spans.append(SpanData(locator_type="text", text=label, ordinal=i,
                              char_start=offset, char_end=offset + len(label),
                              section_label=k, meta={"json_path": k, "value": v}))
        offset += len(label) + 1
    return ParsedDocument(spans=spans, text="\n".join(s.text for s in spans),
                          metadata={"encoding": encoding, "keys": len(flat)})
