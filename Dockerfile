# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Stage 1 — build the static Next.js export
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# The export is fully static; NEXT_TELEMETRY_DISABLED keeps builds quiet and offline-safe.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Python runtime (FastAPI + uvicorn, serving the built UI)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EGA_ENVIRONMENT=production \
    EGA_DATA_DIR=/app/data \
    EGA_DATABASE_URL=sqlite:////app/data/auditor.db \
    EGA_FRONTEND_DIR=/app/frontend/out \
    EGA_SERVE_FRONTEND=true \
    EGA_SEED_DEMO_DATA=true \
    EGA_ALLOW_LOCAL_PROVIDER_URLS=false \
    PORT=8000

# libpq for psycopg, plus the fonts/libs ReportLab needs for PDF output.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements-prod.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements-prod.txt

COPY backend/ ./backend/
COPY datasets/ ./datasets/
COPY evaluation/ ./evaluation/
COPY --from=frontend /build/out ./frontend/out

# Run as an unprivileged user; the data volume is the only writable path.
RUN useradd --create-home --uid 10001 ega \
 && mkdir -p /app/data/uploads /app/data/reports \
 && chown -R ega:ega /app/data
USER ega

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

# Migrations are applied on start so an existing volume is upgraded in place.
CMD ["sh", "-c", "cd /app/backend && PYTHONPATH=. alembic -c alembic.ini upgrade head && cd /app && PYTHONPATH=backend exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
