"""Shared API dependencies: auth, pagination, workspace guards."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.models import AuditEvent, DecisionCase, Document, User, Workspace
from app.db.session import get_db


def current_user(user: User = Depends(get_current_user)) -> User:
    return user


def workspace_for(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Workspace:
    ws = db.get(Workspace, user.workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


@dataclass
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def pagination(page: int = Query(1, ge=1, le=10_000),
               page_size: int = Query(25, ge=1, le=200)) -> Pagination:
    return Pagination(page=page, page_size=page_size)


def paged(items: list, total: int, pg: Pagination) -> dict:
    return {
        "items": items,
        "total": total,
        "page": pg.page,
        "page_size": pg.page_size,
        "pages": max(1, (total + pg.page_size - 1) // pg.page_size),
    }


def get_document_or_404(db: Session, user: User, document_id: str) -> Document:
    doc = db.get(Document, document_id)
    if not doc or doc.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def get_decision_or_404(db: Session, user: User, decision_id: str) -> DecisionCase:
    case = db.get(DecisionCase, decision_id)
    if not case or case.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Decision case not found")
    return case


def workspace_audit_tail(db: Session, workspace_id: str, limit: int = 10) -> list[dict]:
    rows = db.execute(
        select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
    ).scalars().all()
    return [{
        "id": e.id, "action": e.action, "object_type": e.object_type, "object_id": e.object_id,
        "actor": e.actor_label, "created_at": e.created_at.isoformat() if e.created_at else None,
        "payload": e.payload or {},
    } for e in rows]
