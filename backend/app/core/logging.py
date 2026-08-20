"""Structured JSON logging with correlation IDs and secret redaction."""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
SENSITIVE_KEYS = re.compile(r"(authorization|api[_-]?key|password|secret|token|cookie|credential)", re.I)

REDACT_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.=]+"),
    re.compile(r"(?i)(\"?(?:api[_-]?key|password|secret|authorization)\"?\s*[:=]\s*\"?)([^\",}\s]+)"),
]


def redact(value):
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if SENSITIVE_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        out = value
        for pat in REDACT_PATTERNS:
            out = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "***REDACTED***", out)
        return out
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "correlation_id": correlation_id.get(""),
        }
        for key in ("method", "path", "status_code", "duration_ms", "user_id", "event", "detail"):
            if hasattr(record, key):
                payload[key] = redact(getattr(record, key))
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").handlers = [handler]
    logging.getLogger("pdfminer").setLevel(logging.ERROR)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cid = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex[:16]
        token = correlation_id.set(cid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logging.getLogger("ega.request").exception(
                "unhandled error", extra={"method": request.method, "path": request.url.path}
            )
            raise
        finally:
            correlation_id.reset(token)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        if request.url.path.startswith("/api") and not request.url.path.startswith("/api/health"):
            logging.getLogger("ega.request").info(
                "request", extra={
                    "method": request.method, "path": request.url.path,
                    "status_code": response.status_code, "duration_ms": duration_ms,
                },
            )
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response
