"""AI provider adapters with encrypted credentials, SSRF guards, retries and usage accounting.

Supported: OpenAI and OpenAI-compatible endpoints (OpenRouter, custom, Ollama), Anthropic
Claude, and a deterministic offline mode. Provider failures are surfaced, never hidden.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.db.models import AIUsage, ProviderConfig, User

log = logging.getLogger(__name__)
KINDS = {"openai", "anthropic", "openrouter", "custom", "ollama", "offline"}
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}
ENV_KEYS = {
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "gpt-4o-mini", "text-embedding-3-small"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "claude-3-5-sonnet-latest", None),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "openai/gpt-4o-mini", None),
}
SECRETISH = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]+|x-api-key[=:]\s*\S+)", re.I)


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        self.message = sanitize_error(message)
        self.status_code = status_code
        super().__init__(self.message)


def sanitize_error(message: str, max_len: int = 500) -> str:
    out = SECRETISH.sub("***REDACTED***", str(message))
    return out[:max_len]


def _is_non_public(ip: ipaddress._BaseAddress) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)


def _resolves_to_non_public(host: str) -> bool:
    """Resolve a hostname and report whether *any* address is private/internal.

    Checking only literal IPs lets a public-looking hostname resolve to 127.0.0.1 or a
    private range (including DNS-rebinding setups). Resolution happens per validation so a
    changed answer is observed; a hostname that cannot be resolved is left for the provider
    request to fail cleanly.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            if _is_non_public(ipaddress.ip_address(address)):
                return True
        except ValueError:
            continue
    return False


def validate_base_url(url: str, allow_local: bool | None = None) -> str:
    settings = get_settings()
    allow_local = settings.allow_local_provider_urls if allow_local is None else allow_local
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError("Base URL must use http or https")
    if not parsed.hostname:
        raise ProviderError("Base URL is missing a hostname")
    if parsed.username or parsed.password:
        raise ProviderError("Base URL must not embed credentials")
    host = parsed.hostname.lower()
    if host in {"169.254.169.254", "metadata.google.internal", "metadata"}:
        raise ProviderError("Cloud metadata endpoints are blocked")
    blocked_hosts = {"0.0.0.0", "255.255.255.255", "::"}
    if host in blocked_hosts:
        raise ProviderError("Unroutable host is blocked")
    try:
        is_private = _is_non_public(ipaddress.ip_address(host))
    except ValueError:
        is_private = (host in {"localhost", "host.docker.internal", "ollama"} or host.endswith(".local")
                      or _resolves_to_non_public(host))
    if is_private and not allow_local:
        raise ProviderError("Private/local provider URLs are disabled. Set EGA_ALLOW_LOCAL_PROVIDER_URLS=true "
                            "for Ollama or on-prem gateways.")
    return url.rstrip("/")


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


@dataclass
class ProviderSpec:
    id: str | None
    label: str
    kind: str
    base_url: str
    api_key: str = ""
    model: str = ""
    embedding_model: str | None = None
    rerank_model: str | None = None
    timeout: int = 60
    retries: int = 2
    params: dict = field(default_factory=dict)
    pricing: dict = field(default_factory=dict)
    max_context_tokens: int = 32000
    from_env: bool = False


def _config_to_spec(cfg: ProviderConfig) -> ProviderSpec:
    return ProviderSpec(
        id=cfg.id, label=cfg.name, kind=cfg.kind, base_url=(cfg.base_url or DEFAULT_BASE_URLS.get(cfg.kind, "")).rstrip("/"),
        api_key=decrypt_secret(cfg.api_key_encrypted), model=cfg.model or "", embedding_model=cfg.embedding_model,
        rerank_model=cfg.rerank_model, timeout=cfg.timeout_seconds, retries=cfg.retries,
        params=cfg.generation_params or {}, pricing=cfg.pricing or {}, max_context_tokens=cfg.max_context_tokens,
    )


def env_specs() -> list[ProviderSpec]:
    specs = []
    for kind, (key_env, base_env, model_default, embed_default) in ENV_KEYS.items():
        import os
        key = os.getenv(key_env, "")
        if not key:
            continue
        base = os.getenv(base_env, DEFAULT_BASE_URLS[kind])
        specs.append(ProviderSpec(id=None, label=f"{kind.title()} (environment)", kind=kind, base_url=base.rstrip("/"),
                                  api_key=key, model=os.getenv("EGA_DEFAULT_MODEL", model_default),
                                  embedding_model=embed_default, from_env=True))
    return specs


def select_spec(db: Session, workspace_id: str, purpose: str = "generation") -> ProviderSpec | None:
    configs = db.execute(select(ProviderConfig).where(ProviderConfig.workspace_id == workspace_id,
                                                       ProviderConfig.enabled.is_(True))).scalars().all()
    offline = [c for c in configs if c.kind == "offline"]
    usable = [c for c in configs if c.kind != "offline"]
    if purpose == "embedding":
        preferred = [c for c in usable if c.is_default_embedding and c.embedding_model]
        fallback = [c for c in usable if c.embedding_model]
    elif purpose == "rerank":
        preferred = [c for c in usable if c.rerank_model]
        fallback = preferred
    else:
        preferred = [c for c in usable if c.is_default_generation and c.model]
        fallback = [c for c in usable if c.model]
    chosen = (preferred or fallback or [])
    if chosen:
        return _config_to_spec(chosen[0])
    if offline:
        return _config_to_spec(offline[0])
    env = env_specs()
    if env:
        return env[0]
    return None


class BaseAdapter:
    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.settings = get_settings()

    @property
    def name(self) -> str:
        return self.spec.label

    def _headers(self) -> dict:
        return {}

    def _chat_url(self) -> str:
        return f"{validate_base_url(self.spec.base_url)}/chat/completions"

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(max(0, self.spec.retries) + 1):
            try:
                with httpx.Client(timeout=self.spec.timeout, follow_redirects=False) as client:
                    resp = client.request(method, url, **kwargs)
                if resp.status_code in {408, 429, 500, 502, 503, 504} and attempt < self.spec.retries:
                    time.sleep(min(2.0, 0.4 * (2 ** attempt)))
                    continue
                return resp
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.spec.retries:
                    time.sleep(min(2.0, 0.4 * (2 ** attempt)))
                    continue
        raise ProviderError(f"Provider request failed: {last_error}")

    def generate(self, prompt: str, system: str = "", *, max_tokens: int | None = None,
                 temperature: float | None = None, json_mode: bool = False) -> dict:
        raise NotImplementedError

    def embed(self, texts: list[str]) -> dict:
        raise NotImplementedError

    def list_models(self) -> dict:
        raise NotImplementedError

    def test_connection(self) -> dict:
        started = time.perf_counter()
        models = self.list_models()
        latency = round((time.perf_counter() - started) * 1000, 2)
        return {"ok": True, "latency_ms": latency, "models": models.get("models", [])[:50],
                "model_count": len(models.get("models", [])), "message": "Connection succeeded"}


class OpenAICompatibleAdapter(BaseAdapter):
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.spec.api_key:
            headers["Authorization"] = f"Bearer {self.spec.api_key}"
        if self.spec.kind == "openrouter":
            headers["HTTP-Referer"] = "https://evidence-graph-auditor.local"
            headers["X-Title"] = "Evidence-Graph AI Auditor"
        return headers

    def generate(self, prompt: str, system: str = "", *, max_tokens: int | None = None,
                 temperature: float | None = None, json_mode: bool = False) -> dict:
        if not self.spec.model:
            raise ProviderError("No generation model configured for this provider")
        params = self.spec.params or {}
        body = {
            "model": self.spec.model,
            "messages": ([{"role": "system", "content": system}] if system else []) +
                        [{"role": "user", "content": prompt}],
            "temperature": params.get("temperature", 0.2) if temperature is None else temperature,
            "max_tokens": max_tokens or params.get("max_tokens", 1200),
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = self._headers()
        started = time.perf_counter()
        resp = self._request("POST", self._chat_url(), json=body, headers=headers)
        if resp.status_code == 400 and json_mode:
            body.pop("response_format", None)
            resp = self._request("POST", self._chat_url(), json=body, headers=headers)
        latency = round((time.perf_counter() - started) * 1000, 2)
        if resp.status_code >= 400:
            raise ProviderError(f"Provider HTTP {resp.status_code}: {resp.text[:300]}", resp.status_code)
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Unexpected provider response shape: {str(data)[:200]}") from exc
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or estimate_tokens(prompt + system)
        completion_tokens = usage.get("completion_tokens") or estimate_tokens(text)
        return {"text": text, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                "latency_ms": latency, "model": self.spec.model, "provider": self.spec.label}

    def embed(self, texts: list[str]) -> dict:
        if not self.spec.embedding_model:
            raise ProviderError("No embedding model configured for this provider")
        body = {"model": self.spec.embedding_model, "input": texts}
        started = time.perf_counter()
        resp = self._request("POST", f"{validate_base_url(self.spec.base_url)}/embeddings", json=body,
                             headers=self._headers())
        latency = round((time.perf_counter() - started) * 1000, 2)
        if resp.status_code >= 400:
            raise ProviderError(f"Embedding HTTP {resp.status_code}: {resp.text[:300]}", resp.status_code)
        data = resp.json()
        vectors = [d["embedding"] for d in data.get("data", [])]
        usage = data.get("usage") or {}
        return {"vectors": vectors, "prompt_tokens": usage.get("prompt_tokens") or sum(estimate_tokens(t) for t in texts),
                "latency_ms": latency, "model": self.spec.embedding_model}

    def list_models(self) -> dict:
        resp = self._request("GET", f"{validate_base_url(self.spec.base_url)}/models", headers=self._headers())
        if resp.status_code >= 400:
            raise ProviderError(f"Models endpoint HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
        data = resp.json()
        raw = data.get("data", data if isinstance(data, list) else [])
        models = sorted({m.get("id") for m in raw if isinstance(m, dict) and m.get("id")})
        return {"models": models}


class AnthropicAdapter(BaseAdapter):
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.spec.api_key:
            headers["x-api-key"] = self.spec.api_key
        return headers

    def generate(self, prompt: str, system: str = "", *, max_tokens: int | None = None,
                 temperature: float | None = None, json_mode: bool = False) -> dict:
        if not self.spec.model:
            raise ProviderError("No Anthropic model configured")
        params = self.spec.params or {}
        body = {"model": self.spec.model,
                "max_tokens": max_tokens or params.get("max_tokens", 1200),
                "temperature": params.get("temperature", 0.2) if temperature is None else temperature,
                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        base = validate_base_url(self.spec.base_url)
        url = f"{base}/v1/messages" if not base.endswith("/v1") else f"{base}/messages"
        started = time.perf_counter()
        resp = self._request("POST", url, json=body, headers=self._headers())
        latency = round((time.perf_counter() - started) * 1000, 2)
        if resp.status_code >= 400:
            raise ProviderError(f"Provider HTTP {resp.status_code}: {resp.text[:300]}", resp.status_code)
        data = resp.json()
        try:
            text = "".join(block.get("text", "") for block in data.get("content", []))
        except AttributeError as exc:
            raise ProviderError("Unexpected Anthropic response") from exc
        usage = data.get("usage") or {}
        return {"text": text,
                "prompt_tokens": usage.get("input_tokens") or estimate_tokens(prompt + system),
                "completion_tokens": usage.get("output_tokens") or estimate_tokens(text),
                "latency_ms": latency, "model": self.spec.model, "provider": self.spec.label}

    def list_models(self) -> dict:
        base = validate_base_url(self.spec.base_url)
        url = f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
        resp = self._request("GET", url, headers=self._headers())
        if resp.status_code >= 400:
            raise ProviderError(f"Models endpoint HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
        data = resp.json()
        models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
        return {"models": sorted(models)}


class OfflineAdapter(BaseAdapter):
    """Deterministic, credential-free mode. Generation is unavailable; local embeddings work."""
    def generate(self, prompt: str, system: str = "", **kwargs) -> dict:
        raise ProviderError("Offline mode has no generative model. Configure an AI provider to use LLM analysis; "
                            "deterministic analysis remains available.", 503)

    def embed(self, texts: list[str]) -> dict:
        from app.services.retrieval import LocalHashEmbedder
        vectors = LocalHashEmbedder(self.settings.embedding_dim).embed(texts)
        return {"vectors": vectors, "prompt_tokens": sum(estimate_tokens(t) for t in texts), "latency_ms": 0,
                "model": "local-hashing-256"}

    def list_models(self) -> dict:
        return {"models": []}

    def test_connection(self) -> dict:
        return {"ok": True, "latency_ms": 0, "models": [], "model_count": 0,
                "message": "Offline mode active: deterministic verification and stub-free analysis work without credentials."}


def build_adapter(spec: ProviderSpec | None) -> BaseAdapter:
    if spec is None or spec.kind == "offline":
        return OfflineAdapter(spec or ProviderSpec(id=None, label="Offline deterministic", kind="offline",
                                                   base_url="", model=""))
    if spec.kind == "anthropic":
        return AnthropicAdapter(spec)
    if spec.kind in {"openai", "openrouter", "custom", "ollama"}:
        if not spec.base_url:
            raise ProviderError("Base URL is required for this provider")
        return OpenAICompatibleAdapter(spec)
    raise ProviderError(f"Unsupported provider kind '{spec.kind}'")


def record_usage(db: Session, workspace_id: str, spec: ProviderSpec | None, operation: str,
                 result: dict | None = None, *, success: bool = True, error: str | None = None,
                 latency_ms: int | None = None) -> AIUsage:
    usage = AIUsage(workspace_id=workspace_id, provider_config_id=spec.id if spec else None,
                    operation=operation, model=(spec.model if spec else None),
                    prompt_tokens=(result or {}).get("prompt_tokens", 0),
                    completion_tokens=(result or {}).get("completion_tokens", 0),
                    latency_ms=latency_ms if latency_ms is not None else (result or {}).get("latency_ms", 0),
                    success=success, error=sanitize_error(error) if error else None)
    pricing = (spec.pricing if spec else {}) or {}
    usage.estimated_cost_usd = round(
        usage.prompt_tokens / 1000 * float(pricing.get("input_per_1k", 0)) +
        usage.completion_tokens / 1000 * float(pricing.get("output_per_1k", 0)), 6)
    db.add(usage)
    db.commit()
    return usage


def serialize_provider(cfg: ProviderConfig) -> dict:
    return {
        "id": cfg.id, "name": cfg.name, "kind": cfg.kind, "base_url": cfg.base_url,
        "model": cfg.model, "embedding_model": cfg.embedding_model, "rerank_model": cfg.rerank_model,
        "enabled": cfg.enabled, "is_default_generation": cfg.is_default_generation,
        "is_default_embedding": cfg.is_default_embedding, "generation_params": cfg.generation_params,
        "timeout_seconds": cfg.timeout_seconds, "retries": cfg.retries, "streaming": cfg.streaming,
        "max_context_tokens": cfg.max_context_tokens, "pricing": cfg.pricing,
        "api_key_set": bool(cfg.api_key_encrypted), "api_key_hint": mask_secret(decrypt_secret(cfg.api_key_encrypted)) if cfg.api_key_encrypted else "",
        "last_test_at": cfg.last_test_at.isoformat() if cfg.last_test_at else None,
        "last_test_ok": cfg.last_test_ok, "last_test_message": cfg.last_test_message,
    }


def create_provider(db: Session, user: User, payload: dict) -> ProviderConfig:
    kind = (payload.get("kind") or "").lower()
    if kind not in KINDS:
        raise ValueError(f"Unsupported provider kind. Choose one of {sorted(KINDS)}")
    if not payload.get("name"):
        raise ValueError("Provider name is required")
    base_url = payload.get("base_url") or DEFAULT_BASE_URLS.get(kind, "")
    if kind not in {"offline"} and not base_url:
        raise ValueError("Base URL is required")
    if kind not in {"offline"}:
        validate_base_url(base_url)
    cfg = ProviderConfig(
        workspace_id=user.workspace_id, name=str(payload["name"])[:150], kind=kind, base_url=base_url,
        model=payload.get("model"), embedding_model=payload.get("embedding_model"),
        rerank_model=payload.get("rerank_model"), enabled=bool(payload.get("enabled", True)),
        generation_params=payload.get("generation_params") or {},
        timeout_seconds=int(payload.get("timeout_seconds", 60) or 60),
        retries=int(payload.get("retries", 2) or 0),
        streaming=bool(payload.get("streaming", False)),
        max_context_tokens=int(payload.get("max_context_tokens", 32000) or 32000),
        pricing=payload.get("pricing") or {},
    )
    if payload.get("api_key"):
        cfg.api_key_encrypted = encrypt_secret(str(payload["api_key"]))
    db.add(cfg)
    db.flush()
    _apply_defaults(db, cfg, payload)
    db.commit()
    return cfg


def _apply_defaults(db: Session, cfg: ProviderConfig, payload: dict) -> None:
    if payload.get("is_default_generation"):
        db.query(ProviderConfig).filter(ProviderConfig.workspace_id == cfg.workspace_id,
                                        ProviderConfig.id != cfg.id).update({"is_default_generation": False})
        cfg.is_default_generation = True
    if payload.get("is_default_embedding"):
        db.query(ProviderConfig).filter(ProviderConfig.workspace_id == cfg.workspace_id,
                                        ProviderConfig.id != cfg.id).update({"is_default_embedding": False})
        cfg.is_default_embedding = True


def update_provider(db: Session, cfg: ProviderConfig, payload: dict) -> ProviderConfig:
    if "name" in payload and payload["name"]:
        cfg.name = str(payload["name"])[:150]
    if "base_url" in payload and payload["base_url"]:
        validate_base_url(payload["base_url"])
        cfg.base_url = payload["base_url"]
    for field_name in ("model", "embedding_model", "rerank_model"):
        if field_name in payload:
            setattr(cfg, field_name, payload[field_name] or None)
    if "api_key" in payload and payload["api_key"]:
        cfg.api_key_encrypted = encrypt_secret(str(payload["api_key"]))
    if payload.get("clear_api_key"):
        cfg.api_key_encrypted = None
    for field_name in ("enabled", "streaming"):
        if field_name in payload:
            setattr(cfg, field_name, bool(payload[field_name]))
    for field_name, cast in (("timeout_seconds", int), ("retries", int), ("max_context_tokens", int)):
        if field_name in payload and payload[field_name] is not None:
            setattr(cfg, field_name, cast(payload[field_name]))
    if "generation_params" in payload:
        cfg.generation_params = payload["generation_params"] or {}
    if "pricing" in payload:
        cfg.pricing = payload["pricing"] or {}
    _apply_defaults(db, cfg, payload)
    db.commit()
    return cfg


def test_provider(db: Session, cfg: ProviderConfig) -> dict:
    spec = _config_to_spec(cfg)
    started = time.perf_counter()
    ok, message, models = False, "", []
    try:
        adapter = build_adapter(spec)
        result = adapter.test_connection()
        ok = result["ok"]
        message = result["message"]
        models = result.get("models", [])
    except Exception as exc:  # sanitized below
        ok = False
        message = sanitize_error(getattr(exc, "message", str(exc)))
    latency = round((time.perf_counter() - started) * 1000, 2)
    cfg.last_test_at = datetime.now(timezone.utc)
    cfg.last_test_ok = ok
    cfg.last_test_message = message
    record_usage(db, cfg.workspace_id, spec, "test_connection",
                 {"prompt_tokens": 0, "completion_tokens": 0}, success=ok,
                 error=None if ok else message, latency_ms=int(latency))
    db.commit()
    return {"ok": ok, "message": message, "latency_ms": latency, "models": models,
            "model_count": len(models), "api_key_exposed": False}


def discover_models(db: Session, cfg: ProviderConfig) -> dict:
    spec = _config_to_spec(cfg)
    adapter = build_adapter(spec)
    try:
        result = adapter.list_models()
        record_usage(db, cfg.workspace_id, spec, "list_models", success=True)
        return {"models": result.get("models", [])}
    except Exception as exc:
        record_usage(db, cfg.workspace_id, spec, "list_models", success=False,
                     error=sanitize_error(str(exc)))
        raise ProviderError(sanitize_error(str(getattr(exc, "message", exc)))) from exc


def usage_summary(db: Session, workspace_id: str) -> dict:
    rows = db.execute(select(AIUsage).where(AIUsage.workspace_id == workspace_id)).scalars().all()
    total_cost = round(sum(r.estimated_cost_usd for r in rows), 6)
    return {
        "requests": len(rows), "failures": sum(1 for r in rows if not r.success),
        "prompt_tokens": sum(r.prompt_tokens for r in rows),
        "completion_tokens": sum(r.completion_tokens for r in rows),
        "estimated_cost_usd": total_cost,
        "by_operation": {op: {"requests": sum(1 for r in rows if r.operation == op),
                              "cost_usd": round(sum(r.estimated_cost_usd for r in rows if r.operation == op), 6)}
                         for op in sorted({r.operation for r in rows})},
        "recent": [{"id": r.id, "operation": r.operation, "model": r.model, "success": r.success,
                    "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                    "cost_usd": r.estimated_cost_usd, "latency_ms": r.latency_ms,
                    "error": r.error, "created_at": r.created_at.isoformat()} for r in rows[-25:]][::-1],
        "note": "Token counts are provider-reported when available, otherwise estimated (characters/4). "
                "Costs use configured per-1k pricing and may differ from provider invoices.",
    }
