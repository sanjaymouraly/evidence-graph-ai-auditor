"""Entity canonicalization and cross-document alias resolution."""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Entity

ORG_SUFFIXES = {
    "pvt", "pvt.", "private", "limited", "ltd", "ltd.", "llc", "inc", "inc.", "corp", "corp.",
    "corporation", "company", "co", "co.", "gmbh", "ag", "plc", "bv", "sa", "srl", "llp",
    "pte", "pvt.ltd", "pvt.ltd.",
}
NON_ALNUM = re.compile(r"[^a-z0-9 &]+")
WS = re.compile(r"\s+")
ORG_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,4}?\s+"
    r"(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Limited|Ltd\.?|LLC|Inc\.?|GmbH|PLC|Corp\.?))\b"
)


def display_name(raw: str) -> str:
    name = unicodedata.normalize("NFKC", raw or "").strip(" .,:;|\t\n")
    name = WS.sub(" ", name)
    return name[:300]


def entity_key(raw: str) -> str:
    name = display_name(raw).lower().replace("&", " and ")
    name = NON_ALNUM.sub(" ", name.replace(".", ""))
    tokens = [t for t in name.split() if t and t not in ORG_SUFFIXES]
    return " ".join(tokens) or display_name(raw).lower()


def find_entities_in_text(text: str) -> list[str]:
    return [display_name(m.group(1)) for m in ORG_PATTERN.finditer(text or "")]


def get_or_create_entity(db: Session, workspace_id: str, raw_name: str, entity_type: str = "organization",
                         source_display: str | None = None) -> Entity | None:
    display = display_name(source_display or raw_name)
    if not display or len(display) < 2:
        return None
    key = entity_key(display)
    candidates = db.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars().all()
    for ent in candidates:
        ent_key = entity_key(ent.canonical_name)
        aliases = [entity_key(a) for a in (ent.aliases or [])]
        if key == ent_key or key in aliases or ent_key in aliases and key == ent_key:
            if display.lower() != ent.canonical_name.lower() and display not in (ent.aliases or []):
                ent.aliases = sorted(set((ent.aliases or []) + [display]))
            return ent
    ent = Entity(workspace_id=workspace_id, canonical_name=display, entity_type=entity_type, aliases=[], meta={})
    db.add(ent)
    db.flush()
    return ent


def resolve_entity(db: Session, workspace_id: str, raw_name: str) -> Entity | None:
    if not raw_name:
        return None
    key = entity_key(raw_name)
    if not key:
        return None
    candidates = db.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars().all()
    for ent in candidates:
        if key == entity_key(ent.canonical_name) or key in [entity_key(a) for a in (ent.aliases or [])]:
            return ent
    return get_or_create_entity(db, workspace_id, raw_name)
