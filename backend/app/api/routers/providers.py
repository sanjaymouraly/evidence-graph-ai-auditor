"""AI provider configuration with masked credentials and usage accounting."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ProviderCreate, ProviderUpdate
from app.core.security import require_permission
from app.db.models import AIUsage, ProviderConfig, User
from app.db.session import get_db
from app.services.audit import append_event
from app.services.providers import (KINDS, create_provider, discover_models, env_specs,
                                    serialize_provider, test_provider, update_provider,
                                    usage_summary)

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("/types")
def provider_types(user: User = Depends(require_permission("provider:read"))) -> dict:
    return {"kinds": sorted(KINDS), "notes": {
        "offline": "No network calls. Deterministic retrieval-based analysis remains available.",
        "openai": "OpenAI API or any OpenAI-compatible gateway.",
        "anthropic": "Anthropic Messages API.",
        "openrouter": "OpenRouter aggregator (OpenAI-compatible).",
        "custom": "Self-hosted OpenAI-compatible endpoint (LM Studio, vLLM, etc.).",
        "ollama": "Local Ollama server.",
    }}


@router.get("")
def list_providers(user: User = Depends(require_permission("provider:read")),
                   db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(ProviderConfig).where(ProviderConfig.workspace_id == user.workspace_id)
                      .order_by(ProviderConfig.created_at)).scalars().all()
    env = [{"id": f"env:{spec.label}", "label": spec.label, "kind": spec.kind, "model": spec.model,
            "source": "environment", "api_key_set": bool(spec.api_key)} for spec in env_specs()]
    return {"items": [serialize_provider(r) for r in rows], "environment_providers": env,
            "offline_available": True}


@router.post("", status_code=201)
def add_provider(payload: ProviderCreate, user: User = Depends(require_permission("*")),
                 db: Session = Depends(get_db)) -> dict:
    try:
        cfg = create_provider(db, user, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    append_event(db, user.workspace_id, "provider.created", "ProviderConfig", cfg.id,
                 {"name": cfg.name, "kind": cfg.kind, "model": cfg.model}, actor=user, commit=True)
    return serialize_provider(cfg)


@router.patch("/{provider_id}")
def patch_provider(provider_id: str, payload: ProviderUpdate,
                   user: User = Depends(require_permission("*")),
                   db: Session = Depends(get_db)) -> dict:
    cfg = db.get(ProviderConfig, provider_id)
    if not cfg or cfg.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Provider not found")
    try:
        cfg = update_provider(db, cfg, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    append_event(db, user.workspace_id, "provider.updated", "ProviderConfig", cfg.id,
                 {"fields": sorted(payload.model_dump(exclude_unset=True))}, actor=user, commit=True)
    return serialize_provider(cfg)


@router.delete("/{provider_id}")
def delete_provider(provider_id: str, user: User = Depends(require_permission("*")),
                    db: Session = Depends(get_db)) -> dict:
    cfg = db.get(ProviderConfig, provider_id)
    if not cfg or cfg.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Provider not found")
    db.delete(cfg)
    append_event(db, user.workspace_id, "provider.deleted", "ProviderConfig", provider_id,
                 {"name": cfg.name}, actor=user, commit=False)
    db.commit()
    return {"detail": "Provider deleted"}


@router.post("/{provider_id}/test")
def test(provider_id: str, user: User = Depends(require_permission("*")),
         db: Session = Depends(get_db)) -> dict:
    cfg = db.get(ProviderConfig, provider_id)
    if not cfg or cfg.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Provider not found")
    result = test_provider(db, cfg)
    append_event(db, user.workspace_id, "provider.tested", "ProviderConfig", cfg.id,
                 {"ok": result.get("ok"), "message": result.get("message")}, actor=user, commit=True)
    return result


@router.post("/{provider_id}/discover-models")
def discover(provider_id: str, user: User = Depends(require_permission("*")),
             db: Session = Depends(get_db)) -> dict:
    cfg = db.get(ProviderConfig, provider_id)
    if not cfg or cfg.workspace_id != user.workspace_id:
        raise HTTPException(status_code=404, detail="Provider not found")
    return discover_models(db, cfg)


@router.get("/usage/summary")
def usage(user: User = Depends(require_permission("provider:read")),
          db: Session = Depends(get_db)) -> dict:
    summary = usage_summary(db, user.workspace_id)
    rows = db.execute(select(AIUsage).where(AIUsage.workspace_id == user.workspace_id)
                      .order_by(AIUsage.created_at.desc()).limit(100)).scalars().all()
    summary["recent"] = [{
        "id": r.id, "operation": r.operation, "model": r.model, "provider_config_id": r.provider_config_id,
        "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
        "estimated_cost_usd": r.estimated_cost_usd, "latency_ms": r.latency_ms,
        "success": r.success, "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]
    return summary
