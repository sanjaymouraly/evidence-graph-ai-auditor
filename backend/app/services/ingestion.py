"""Upload storage, immutable source versions, parsing, provenance and claim extraction."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Claim, Document, DocumentVersion, Edge, Entity, IngestionJob,
    SourceSpan, User,
)
from app.parsers.base import ParsedDocument, ParserError, detect_injection, normalize_text
from app.parsers.registry import SUPPORTED_EXTENSIONS, mime_for, parse_document
from app.services.audit import append_event
from app.services.claims import DocContext, extract_and_store_claims
from app.services.contradictions import detect_contradictions
from app.services.entities import display_name, find_entities_in_text, get_or_create_entity
from app.services.normalize import normalize_date, normalize_money

log = logging.getLogger(__name__)
INGEST_LOCK = threading.Lock()
SAFE_NAME = re.compile(r"[^A-Za-z0-9._()\- ]+")
VERSION_MARKER = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:v(?:er(?:sion)?)?|rev(?:ision)?)[\s._-]*\d+(?![A-Za-z0-9])|\(\d+\)|\brevised\b|\bfinal\b",
    re.I)

DOC_TYPE_RULES = [
    ("quote", r"quot|proforma|proposal|bid|tender"),
    ("invoice", r"invoice|tax bill"),
    ("contract", r"contract|agreement|purchase order|^po[-_ ]|framework"),
    ("certificate", r"certificat|certificate of|accredit"),
    ("spec", r"spec|datasheet|data sheet|technical description"),
    ("requirements", r"requirement|sow|statement of work|rfp|specification requirement"),
    ("risk_register", r"risk register|risk log|risk assessment"),
    ("policy", r"policy|procedure|standard operating|guideline"),
    ("schedule", r"schedule|milestone|project plan|timeline"),
    ("technical_assessment", r"assessment|evaluation report|test report|feasibility"),
    ("decision_record", r"decision record|decision memo|minutes of|approval record|\bmemo\b"),
    ("spreadsheet", r"register|tracker|matrix|cost model|pricing sheet"),
]
EFFECTIVE_DOC_RE = re.compile(
    r"(?:effective\s*(?:date)?|valid\s+from|commencing)\s*[:\-]?\s*(" + r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|"
    r"\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}" + r")", re.I)
ISSUED_DOC_RE = re.compile(
    r"(?:date|issued(?:\s+on)?|quote\s+date|document\s+date|prepared\s+on)\s*[:\-]\s*("
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|"
    r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})", re.I)
VALID_DOC_RE = re.compile(
    r"(?:valid\s*(?:until|till|through)|expires?(?:\s+on)?|expiry\s*date)\s*[:\-]?\s*("
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|"
    r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})", re.I)
PARTY_RES = [
    ("supplier", re.compile(r"(?:supplier|vendor|seller|contractor|provided\s+by)\s*[:\-]\s*([^\n|;]{3,120})", re.I)),
    ("buyer", re.compile(r"(?:buyer|client|purchaser|customer)\s*[:\-]\s*([^\n|;]{3,120})", re.I)),
]
SCOPE_DOC_RE = re.compile(
    r"(?:scope(?:\s+of\s+(?:supply|work))?|subject|product|item|for\s+the\s+supply\s+of)\s*[:\-]\s*([^\n|;]{3,140})", re.I)
AUTHOR_DOC_RE = re.compile(r"(?:prepared\s+by|author|issued\s+by|from)\s*[:\-]\s*([^\n|]{2,120})", re.I)


def safe_filename(name: str) -> str:
    base = (name or "upload").replace("\\", "/").split("/")[-1]
    base = SAFE_NAME.sub("_", base).strip(" ._")
    if not base:
        base = "upload"
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = stem[:120]
    return f"{stem}.{ext[:12]}" if ext else stem


def derive_doc_key(filename: str, title: str | None = None) -> str:
    base = Path(filename).stem
    cleaned = VERSION_MARKER.sub("", base)
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", cleaned.lower()).strip("-")
    if not cleaned:
        cleaned = hashlib.sha256(filename.encode()).hexdigest()[:10]
    return cleaned[:140]


def classify_doc_type(filename: str, head_text: str, explicit: str | None) -> str:
    if explicit and explicit not in {"auto", "generic"}:
        return explicit
    probe = f"{filename} {head_text[:1500]}".lower()
    ext = Path(filename).suffix.lower()
    for dtype, pattern in DOC_TYPE_RULES:
        if re.search(pattern, probe, re.I):
            if dtype == "spreadsheet" and ext not in {".xlsx", ".csv"}:
                continue
            return dtype
    return {".xlsx": "spreadsheet", ".csv": "spreadsheet", ".eml": "email"}.get(ext, "generic")


def _extract_party(text: str) -> dict:
    out = {}
    for kind, rx in PARTY_RES:
        m = rx.search(text)
        if m:
            value = display_name(m.group(1))
            if value:
                out[kind] = value
    return out


def _scope_from_text(text: str) -> str:
    m = SCOPE_DOC_RE.search(text)
    if m:
        value = re.sub(r"\s+", " ", m.group(1)).strip(" .,:;")
        if 2 < len(value) <= 140:
            return value
    return ""


def derive_metadata(parsed: ParsedDocument, filename: str, title: str, doc_type: str,
                    explicit_entity: str | None) -> dict:
    text = parsed.text or ""
    head = text[:6000]
    meta: dict = {}
    for key, rx in (("effective_date", EFFECTIVE_DOC_RE), ("issued_date", ISSUED_DOC_RE),
                    ("valid_until", VALID_DOC_RE)):
        m = rx.search(head)
        if m:
            d, ambiguous = normalize_date(m.group(1))
            if d:
                meta[key] = d.isoformat()
                if ambiguous:
                    meta[f"{key}_ambiguous"] = True
    parties = _extract_party(head)
    meta["parties"] = parties
    if explicit_entity:
        meta["primary_entity"] = display_name(explicit_entity)
    elif parties.get("supplier"):
        meta["primary_entity"] = parties["supplier"]
    else:
        orgs = find_entities_in_text(head)
        if orgs:
            counts: dict[str, int] = {}
            for o in orgs:
                counts[o] = counts.get(o, 0) + 1
            meta["primary_entity"] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    author = AUTHOR_DOC_RE.search(head)
    if author:
        meta["author"] = display_name(author.group(1))
    scope = _scope_from_text(head)
    if scope:
        meta["scope_hint"] = scope
    elif title:
        meta["scope_hint"] = title
    currencies: dict[str, int] = {}
    for m in re.finditer(r"(USD|EUR|INR|GBP|SGD|AUD|CAD|\$|€|£|₹)\s?\d", text):
        _, cur, _ = normalize_money(m.group(0))
        if cur:
            currencies[cur] = currencies.get(cur, 0) + 1
    if currencies:
        meta["currency"] = sorted(currencies.items(), key=lambda kv: -kv[1])[0][0]
    return meta


def ingest_document(db: Session, user: User, filename: str, data: bytes, *,
                    title: str | None = None, doc_type: str = "auto", doc_key: str | None = None,
                    effective_date: str | None = None, valid_until: str | None = None,
                    primary_entity: str | None = None, trust_level: float = 0.7,
                    description: str = "", allow_duplicate: bool = False) -> dict:
    settings = get_settings()
    if not data:
        raise ValueError("Empty file")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValueError(f"File exceeds {settings.max_upload_mb} MB limit")
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    clean_name = safe_filename(filename)
    content_hash = hashlib.sha256(data).hexdigest()
    duplicate = db.execute(
        select(DocumentVersion).where(DocumentVersion.workspace_id == user.workspace_id,
                                      DocumentVersion.content_hash == content_hash)
    ).scalars().first()
    if duplicate and not allow_duplicate:
        return {"duplicate": True, "version_id": duplicate.id, "document_id": duplicate.document_id,
                "message": "Identical content already ingested; original version preserved.",
                "content_hash": content_hash}
    chosen_title = display_name(title) if title else display_name(Path(clean_name).stem.replace("_", " ").replace("-", " "))
    key = derive_doc_key(clean_name, chosen_title) if not doc_key else derive_doc_key(doc_key)
    if doc_key and not re.fullmatch(r"[A-Za-z0-9_-]+", doc_key):
        raise ValueError("doc_key may only contain letters, digits, hyphens and underscores")
    collision = db.execute(select(Document).where(Document.workspace_id == user.workspace_id,
                                                   Document.doc_key == key)).scalar_one_or_none()
    has_version_marker = bool(VERSION_MARKER.search(Path(clean_name).stem))
    if collision and not doc_key and not has_version_marker:
        # Different files sharing a generic name are NOT silently treated as revisions.
        key = f"{key}-{content_hash[:6]}"
        collision = None
    if collision:
        document = collision
        existing_versions = db.execute(
            select(DocumentVersion).where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number.desc())
        ).scalars().all()
        version_number = (existing_versions[0].version_number + 1) if existing_versions else 1
        previous = [v for v in existing_versions if v.is_current]
    else:
        document = Document(workspace_id=user.workspace_id, doc_key=key, title=chosen_title,
                            doc_type=(doc_type if doc_type not in {"auto", "generic"} else "generic"),
                            description=description, created_by=user.id)
        db.add(document)
        db.flush()
        version_number = 1
        previous = []
    version = DocumentVersion(
        document_id=document.id, workspace_id=user.workspace_id, version_number=version_number,
        filename=clean_name, mime_type=mime_for(clean_name), content_hash=content_hash,
        size_bytes=len(data), storage_path="", status="uploaded", is_current=True,
        effective_date=normalize_date(effective_date)[0] if effective_date else None,
        valid_until=normalize_date(valid_until)[0] if valid_until else None,
        source_author=None, source_org=None, primary_entity=display_name(primary_entity) if primary_entity else None,
        trust_level=max(0.0, min(1.0, trust_level)), uploaded_by=user.id, meta={},
    )
    db.add(version)
    db.flush()
    folder = settings.upload_dir / user.workspace_id / version.id
    folder.mkdir(parents=True, exist_ok=True)
    storage_path = folder / clean_name
    # Exclusive create prevents silently overwriting evidence if two uploads race.
    with open(storage_path, "xb") as fh:
        fh.write(data)
    version.storage_path = str(storage_path)
    for prev in previous:
        prev.is_current = False
        prev.superseded_by_version_id = version.id
        db.add(Edge(workspace_id=user.workspace_id, source_type="DocumentVersion", source_id=version.id,
                    target_type="DocumentVersion", target_id=prev.id, relation="SUPERSEDES",
                    provenance={"document_id": document.id, "previous_version": prev.version_number,
                                "new_version": version_number},
                    justification=f"Version {version_number} supersedes version {prev.version_number}",
                    confidence=1.0, created_by="ingestion"))
    job = IngestionJob(workspace_id=user.workspace_id, version_id=version.id, status="queued", stage="queued")
    db.add(job)
    append_event(db, user.workspace_id, "document.version_uploaded", "DocumentVersion", version.id, {
        "document_id": document.id, "filename": clean_name, "content_hash": content_hash,
        "version_number": version_number, "size_bytes": len(data), "duplicate": duplicate is not None,
    }, actor=user, commit=False)
    db.commit()
    return {"duplicate": False, "document_id": document.id, "version_id": version.id,
            "job_id": job.id, "version_number": version_number, "content_hash": content_hash,
            "status": version.status}


def _set_status(db: Session, version: DocumentVersion, job: IngestionJob | None, status: str,
                stage: str, error: str | None = None) -> None:
    version.status = status
    if error:
        version.error = error[:4000]
    if job:
        job.status = status
        job.stage = stage
        if status in {"processed", "failed"}:
            job.finished_at = datetime.now(timezone.utc)


def process_version(version_id: str, actor_id: str | None = None) -> dict:
    """Idempotent background ingestion pipeline. Safe to retry; derived data is rebuilt."""
    from app.db.session import session_scope
    from app.services.gate import reevaluate_decisions_for_document

    with INGEST_LOCK, session_scope() as db:
        version = db.get(DocumentVersion, version_id)
        if not version:
            return {"status": "missing"}
        if version.status == "processed" and version.processed_at:
            return {"status": "already_processed", "version_id": version_id}
        job = db.execute(select(IngestionJob).where(IngestionJob.version_id == version_id)
                         .order_by(IngestionJob.created_at.desc())).scalars().first()
        if job:
            job.attempts += 1
        _set_status(db, version, job, "processing", "parsing")
        db.commit()
        savepoint = None
        try:
            with open(version.storage_path, "rb") as fh:
                data = fh.read()
            parsed = parse_document(version.filename, data)
            document = db.get(Document, version.document_id)
            meta = derive_metadata(parsed, version.filename, document.title, document.doc_type,
                                   version.primary_entity)
            doc_type = classify_doc_type(version.filename, parsed.text, document.doc_type)
            document.doc_type = doc_type
            version.effective_date = version.effective_date or (
                normalize_date(meta["effective_date"])[0] if meta.get("effective_date") else None)
            version.issued_date = normalize_date(meta["issued_date"])[0] if meta.get("issued_date") else None
            version.valid_until = version.valid_until or (
                normalize_date(meta["valid_until"])[0] if meta.get("valid_until") else None)
            version.source_author = meta.get("author")
            version.source_org = meta.get("parties", {}).get("supplier")
            if not version.primary_entity:
                version.primary_entity = meta.get("primary_entity")
            # Derived data is replaced inside a savepoint: if parsing/extraction fails halfway,
            # rolling back restores the previously processed spans and claims instead of
            # leaving a failed version with its evidence silently deleted.
            savepoint = db.begin_nested()
            # Remove old derived data for this version (reprocessing is idempotent).
            old_spans = db.execute(select(SourceSpan).where(SourceSpan.version_id == version.id)).scalars().all()
            old_ids = [s.id for s in old_spans]
            db.execute(delete(Claim).where(Claim.version_id == version.id))
            if old_ids:
                db.execute(delete(Edge).where(Edge.workspace_id == version.workspace_id,
                                              (Edge.target_type == "SourceSpan") & Edge.target_id.in_(old_ids)))
            db.execute(delete(SourceSpan).where(SourceSpan.version_id == version.id))
            db.flush()
            injection_flags: list[dict] = []
            spans: list[SourceSpan] = []
            for i, sd in enumerate(parsed.spans):
                text = normalize_text(sd.text)
                if not text.strip():
                    continue
                flags = detect_injection(text)
                span_meta = dict(sd.meta or {})
                if flags:
                    span_meta["injection"] = True
                    span_meta["injection_flags"] = flags
                    injection_flags.append({"span": i, "page": sd.page, "sheet": sd.sheet,
                                            "flags": flags, "excerpt": text[:180]})
                span = SourceSpan(
                    workspace_id=version.workspace_id, version_id=version.id, ordinal=i,
                    locator_type=sd.locator_type, page=sd.page, bbox=sd.bbox,
                    paragraph_index=sd.paragraph_index, sheet=sd.sheet, cell_range=sd.cell_range,
                    char_start=sd.char_start, char_end=sd.char_end, section_label=sd.section_label,
                    text=text, text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(), meta=span_meta,
                )
                db.add(span)
                spans.append(span)
            db.flush()
            version.injection_flags = injection_flags
            version.warnings = list(dict.fromkeys(parsed.warnings + (
                [f"{len(injection_flags)} span(s) contain instructions that resemble prompt injection; "
                 f"excluded from extraction"] if injection_flags else [])))
            extra_meta = {k: v for k, v in meta.items() if k not in {"effective_date", "issued_date", "valid_until"}}
            version.meta = {**(version.meta or {}), **extra_meta, **parsed.metadata, "span_count": len(spans),
                            "characters": len(parsed.text)}
            if parsed.sheets:
                version.meta["sheets"] = {name: rows[:2000] for name, rows in parsed.sheets.items()}
            version.source_author = version.source_author or meta.get("author")
            primary = version.primary_entity
            if primary:
                get_or_create_entity(db, version.workspace_id, primary)
            orgs = find_entities_in_text(parsed.text[:12000])
            for org in list(dict.fromkeys(orgs))[:25]:
                get_or_create_entity(db, version.workspace_id, org)
            entities = db.execute(select(Entity).where(Entity.workspace_id == version.workspace_id)).scalars().all()
            ctx = DocContext(
                workspace_id=version.workspace_id, version_id=version.id,
                version_number=version.version_number, effective_date=version.effective_date,
                valid_until=version.valid_until, observed_date=version.issued_date or version.created_at.date(),
                scope_hint=(version.meta or {}).get("scope_hint", ""), primary_entity=version.primary_entity,
                currency=(version.meta or {}).get("currency"), is_current=version.is_current,
                entities=[(a.lower(), e.canonical_name, e.id) for e in entities for a in
                          ([e.canonical_name] + list(e.aliases or []))],
            )
            claims = extract_and_store_claims(db, version, spans, ctx)
            savepoint.commit()
            version.status = "processed"
            version.error = None
            version.processed_at = datetime.now(timezone.utc)
            _set_status(db, version, job, "processed", "complete")
            append_event(db, version.workspace_id, "document.processed", "DocumentVersion", version.id, {
                "document_id": version.document_id, "spans": len(spans), "claims": len(claims),
                "doc_type": document.doc_type, "warnings": version.warnings,
                "injection_flags": len(injection_flags),
            }, actor_label="ingestion", commit=False)
            summary = {"status": "processed", "version_id": version.id, "spans": len(spans),
                       "claims": len(claims), "warnings": version.warnings,
                       "injection_flags": len(injection_flags), "doc_type": document.doc_type}
            db.commit()
            stats = detect_contradictions(db, version.workspace_id, commit=True)
            impacted = reevaluate_decisions_for_document(db, version.workspace_id, version.document_id,
                                                         reason="source version processed")
            return {**summary, "contradictions": stats, "reevaluated_decisions": impacted}
        except (ParserError, ValueError, OSError) as exc:
            log.warning("ingestion failed for %s: %s", version_id, exc)
            if savepoint is not None and savepoint.is_active:
                savepoint.rollback()
            _set_status(db, version, job, "failed", "failed", error=str(exc))
            append_event(db, version.workspace_id, "document.processing_failed", "DocumentVersion", version.id,
                         {"error": str(exc)[:1000]}, actor_label="ingestion", commit=False)
            db.commit()
            return {"status": "failed", "version_id": version.id, "error": str(exc)}
        except Exception as exc:  # defensive: record and keep prior evidence intact
            log.exception("unexpected ingestion failure for %s", version_id)
            if savepoint is not None and savepoint.is_active:
                savepoint.rollback()
            _set_status(db, version, job, "failed", "failed", error=f"Unexpected error: {exc}")
            append_event(db, version.workspace_id, "document.processing_failed", "DocumentVersion", version.id,
                         {"error": f"Unexpected error: {exc}"[:1000]}, actor_label="ingestion", commit=False)
            db.commit()
            return {"status": "failed", "version_id": version.id, "error": f"Unexpected error: {exc}"}


def reprocess_version(db: Session, user: User, version_id: str) -> dict:
    version = db.get(DocumentVersion, version_id)
    if not version or version.workspace_id != user.workspace_id:
        raise ValueError("Version not found")
    version.status = "uploaded"
    version.processed_at = None
    job = IngestionJob(workspace_id=version.workspace_id, version_id=version.id, status="queued", stage="queued")
    db.add(job)
    append_event(db, user.workspace_id, "document.reprocess_requested", "DocumentVersion", version.id,
                 {"document_id": version.document_id}, actor=user, commit=False)
    db.commit()
    return {"version_id": version.id, "job_id": job.id, "status": "queued"}


def soft_delete_version(db: Session, user: User, version_id: str) -> dict:
    """Evidence is never silently destroyed: versions are marked superseded/inactive, files retained."""
    version = db.get(DocumentVersion, version_id)
    if not version or version.workspace_id != user.workspace_id:
        raise ValueError("Version not found")
    version.is_current = False
    version.status = "archived"
    append_event(db, user.workspace_id, "document.version_archived", "DocumentVersion", version.id,
                 {"document_id": version.document_id}, actor=user, commit=True)
    detect_contradictions(db, user.workspace_id, commit=True)
    return {"version_id": version.id, "status": "archived"}
