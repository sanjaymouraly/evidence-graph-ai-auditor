"""Environment-based configuration for the Evidence-Graph AI Auditor."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("EGA_DATA_DIR", BASE_DIR / "data"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EGA_", env_file=".env", extra="ignore")

    app_name: str = "Evidence-Graph AI Auditor"
    environment: str = "development"  # development | test | production
    debug: bool = False
    secret_key: str = ""  # generated for dev if empty; must be set in production
    jwt_ttl_minutes: int = 480
    database_url: str = f"sqlite:///{DATA_DIR / 'auditor.db'}"
    auto_create_schema: bool = True
    data_dir: str = str(DATA_DIR)
    uploads_subdir: str = "uploads"
    reports_subdir: str = "reports"
    frontend_dir: str = str(BASE_DIR.parent / "frontend" / "out")
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    serve_frontend: bool = True
    max_upload_mb: int = 25
    max_pdf_pages: int = 250
    max_zip_files: int = 5000
    auth_rate_limit_per_minute: int = 20
    api_rate_limit_per_minute: int = 600
    stale_after_days_default: int = 365
    require_https: bool = False
    # Off by default so a deployment cannot be pointed at loopback/metadata addresses.
    # Ollama and on-prem gateways require EGA_ALLOW_LOCAL_PROVIDER_URLS=true.
    allow_local_provider_urls: bool = False
    log_level: str = "INFO"
    demo_mode: bool = True
    seed_demo_data: bool = True
    embedding_dim: int = 256

    @property
    def upload_dir(self) -> Path:
        return Path(self.data_dir) / self.uploads_subdir

    @property
    def report_dir(self) -> Path:
        return Path(self.data_dir) / self.reports_subdir

    def startup_errors(self) -> list[str]:
        """Configuration problems that must stop the process instead of surfacing later.

        A production deployment with no secret key used to start, report itself healthy and
        then return 500 on every login. Health endpoints must not claim a broken service is
        ready, so these are checked before the app accepts traffic.
        """
        problems: list[str] = []
        if self.environment == "production":
            if not self.secret_key or self.secret_key.strip() in {"", "change-me-in-production"}:
                problems.append("EGA_SECRET_KEY must be set to a unique value in production")
        if self.jwt_ttl_minutes <= 0:
            problems.append("EGA_JWT_TTL_MINUTES must be positive")
        if self.max_upload_mb <= 0:
            problems.append("EGA_MAX_UPLOAD_MB must be positive")
        return problems

    def startup_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.environment == "production" and not self.require_https:
            warnings.append("EGA_REQUIRE_HTTPS is false: terminate TLS in front of the app or enable it")
        if self.allow_local_provider_urls:
            warnings.append("EGA_ALLOW_LOCAL_PROVIDER_URLS=true: private-range provider URLs are permitted")
        return warnings

    def resolved_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        if self.environment == "production":
            raise RuntimeError("EGA_SECRET_KEY must be set in production")
        # Stable per process-owner dev secret persisted so sessions survive restarts.
        secret_file = Path(self.data_dir) / ".dev_secret"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        if secret_file.exists():
            return secret_file.read_text().strip()
        value = secrets.token_urlsafe(48)
        secret_file.write_text(value)
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
        return value


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    Path(s.data_dir).mkdir(parents=True, exist_ok=True)
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    s.report_dir.mkdir(parents=True, exist_ok=True)
    return s
