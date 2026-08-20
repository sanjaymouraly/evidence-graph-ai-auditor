"""Workspace-wide grounded Q&A and AI usage visibility."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import AskRequest
from app.core.security import require_permission
from app.db.models import User
from app.db.session import get_db
from app.services.analysis import grounded_answer
from app.services.providers import usage_summary

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/ask")
def ask(payload: AskRequest, user: User = Depends(require_permission("qa:ask")),
        db: Session = Depends(get_db)) -> dict:
    return grounded_answer(db, user, payload.question, decision_id=payload.decision_id,
                           top_k=payload.top_k, use_llm=payload.use_llm)


@router.get("/usage")
def usage(user: User = Depends(require_permission("provider:read")),
          db: Session = Depends(get_db)) -> dict:
    return usage_summary(db, user.workspace_id)
