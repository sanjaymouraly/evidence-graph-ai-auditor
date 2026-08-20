"""Authentication and user administration."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.schemas import LoginRequest, UserCreate, UserPatch
from app.core.security import (create_access_token, hash_password, require_permission,
                               verify_password)
from app.db.models import User, Workspace
from app.db.session import get_db
from app.services.audit import append_event

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


def _serialize_user(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "name": u.name, "role": u.role,
        "is_active": u.is_active, "workspace_id": u.workspace_id,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    email = payload.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalars().first()
    # Constant-ish work factor even for unknown users keeps timing less informative.
    stored = user.password_hash if user else hash_password("not-a-real-password")
    ok = verify_password(payload.password, stored) and user is not None
    if not ok or not user.is_active:
        if user:
            append_event(db, user.workspace_id, "auth.login_failed", "User", user.id,
                         {"email": email, "ip": request.client.host if request.client else None},
                         actor_label=email, commit=True)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user.last_login_at = datetime.now(timezone.utc)
    ws = db.get(Workspace, user.workspace_id)
    append_event(db, user.workspace_id, "auth.login", "User", user.id,
                 {"ip": request.client.host if request.client else None}, actor=user, commit=True)
    token = create_access_token(user)
    return {"access_token": token, "token_type": "bearer", "expires_in_minutes": 480,
            "user": _serialize_user(user), "workspace": {"id": ws.id, "name": ws.name, "slug": ws.slug}}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ws = db.get(Workspace, user.workspace_id)
    return {"user": _serialize_user(user), "workspace": {"id": ws.id, "name": ws.name, "slug": ws.slug}}


@router.post("/logout")
def logout(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    append_event(db, user.workspace_id, "auth.logout", "User", user.id, {}, actor=user, commit=True)
    return {"detail": "Signed out"}


@users_router.get("")
def list_users(user: User = Depends(require_permission("audit:read")), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(User).where(User.workspace_id == user.workspace_id)
                      .order_by(User.created_at)).scalars().all()
    return {"items": [_serialize_user(u) for u in rows], "total": len(rows)}


@users_router.post("", status_code=201)
def create_user(payload: UserCreate, user: User = Depends(require_permission("*")),
                db: Session = Depends(get_db)) -> dict:
    existing = db.execute(select(User).where(User.email == payload.email)).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    new = User(workspace_id=user.workspace_id, email=payload.email, name=payload.name,
               password_hash=hash_password(payload.password), role=payload.role)
    db.add(new)
    db.flush()
    append_event(db, user.workspace_id, "user.created", "User", new.id,
                 {"email": new.email, "role": new.role}, actor=user, commit=False)
    db.commit()
    return _serialize_user(new)


@users_router.patch("/{user_id}")
def patch_user(user_id: str, payload: UserPatch, user: User = Depends(require_permission("*")),
               db: Session = Depends(get_db)) -> dict:
    target = db.get(User, user_id)
    if not target or target.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="User not found")
    changes = payload.model_dump(exclude_unset=True)
    if "password" in changes:
        target.password_hash = hash_password(changes.pop("password"))
    for key, value in changes.items():
        setattr(target, key, value)
    append_event(db, user.workspace_id, "user.updated", "User", target.id,
                 {"fields": sorted(changes)}, actor=user, commit=False)
    db.commit()
    return _serialize_user(target)
