"""Tamper-evident hash-chained audit trail."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, User, Workspace


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _ts(value: datetime) -> str:
    """Stable UTC timestamp string used both when sealing and verifying events.

    SQLite returns naive datetimes, so the hash must not depend on the tzinfo marker.
    """
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def append_event(
    db: Session,
    workspace_id: str,
    action: str,
    object_type: str = "",
    object_id: str = "",
    payload: dict | None = None,
    actor: User | None = None,
    actor_label: str | None = None,
    correlation_id: str | None = None,
    commit: bool = True,
) -> AuditEvent:
    # Serialize chain writes per workspace where the database supports row locks.
    db.execute(select(Workspace).where(Workspace.id == workspace_id).with_for_update()).scalar_one_or_none()
    prev = db.execute(
        select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = prev.event_hash if prev else ""
    now = datetime.now(timezone.utc)
    safe_payload = payload or {}
    event = AuditEvent(
        workspace_id=workspace_id,
        actor_id=actor.id if actor else None,
        actor_label=actor_label or (actor.email if actor else "system"),
        action=action,
        object_type=object_type,
        object_id=object_id,
        payload=safe_payload,
        correlation_id=correlation_id,
        prev_hash=prev_hash,
        created_at=now,
    )
    db.add(event)
    db.flush()  # assign id before hashing
    body = {
        "id": event.id, "workspace_id": workspace_id, "actor": event.actor_label,
        "action": action, "object_type": object_type, "object_id": object_id,
        "payload": safe_payload, "prev_hash": prev_hash, "created_at": _ts(now),
    }
    event.event_hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
    if commit:
        db.commit()
    return event


def verify_chain(db: Session, workspace_id: str) -> dict:
    events = db.execute(
        select(AuditEvent).where(AuditEvent.workspace_id == workspace_id).order_by(AuditEvent.created_at, AuditEvent.id)
    ).scalars().all()
    prev = ""
    errors: list[dict] = []
    for ev in events:
        body = {
            "id": ev.id, "workspace_id": ev.workspace_id, "actor": ev.actor_label,
            "action": ev.action, "object_type": ev.object_type, "object_id": ev.object_id,
            "payload": ev.payload or {}, "prev_hash": ev.prev_hash,
            "created_at": _ts(ev.created_at),
        }
        recomputed = hashlib.sha256(_canonical(body).encode()).hexdigest()
        if ev.prev_hash != prev:
            errors.append({"event_id": ev.id, "error": "prev_hash mismatch"})
        if recomputed != ev.event_hash:
            errors.append({"event_id": ev.id, "error": "event_hash mismatch"})
        prev = ev.event_hash
    return {
        "events": len(events), "valid": not errors, "errors": errors,
        "head_hash": prev, "verified_at": datetime.now(timezone.utc).isoformat(),
    }
