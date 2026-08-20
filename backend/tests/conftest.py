"""Pytest fixtures.

By default the suite runs against a throwaway SQLite database and temporary upload/report
directories, seeded from the shipped synthetic development dataset.

Set `EGA_TEST_DATABASE_URL` (for example
`postgresql+psycopg://ega:ega@localhost:5432/ega_test`) to run the identical suite against
PostgreSQL. The target schema is dropped and rebuilt at session start, so point it at a
database you are willing to lose.

Environment variables must be set before any application module imports the cached settings
object.
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="ega-tests-")
# Optional external database for cross-backend parity runs (CI matrix). Never used by default.
_EXTERNAL_DATABASE_URL = os.environ.get("EGA_TEST_DATABASE_URL", "").strip()
os.environ.update({
    "EGA_DATABASE_URL": _EXTERNAL_DATABASE_URL or f"sqlite:///{_TMP}/test.db",
    "EGA_DATA_DIR": _TMP,
    "EGA_ENVIRONMENT": "test",
    "EGA_SEED_DEMO_DATA": "false",
    "EGA_LOG_LEVEL": "WARNING",
    "EGA_API_RATE_LIMIT_PER_MINUTE": "10000",
    # The acceptance suite exercises local/Ollama-style endpoints on purpose.
    "EGA_ALLOW_LOCAL_PROVIDER_URLS": "true",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, engine, init_db  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import load_manifest, seed_dataset_into_workspace  # noqa: E402

WORKSPACE_SLUG = "test-dev"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def dataset():
    return load_manifest("dev")


@pytest.fixture(scope="session")
def seeded(client, dataset):
    if _EXTERNAL_DATABASE_URL:
        # A leftover workspace from a previous run would short-circuit seeding and make the
        # suite assert against stale state, so start from an empty schema.
        Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    try:
        from sqlalchemy import select
        from app.db.models import Workspace
        existing = db.execute(select(Workspace).where(Workspace.slug == WORKSPACE_SLUG)).scalars().first()
        if existing:
            return {"workspace_id": existing.id, "workspace_slug": WORKSPACE_SLUG}
        base, manifest = dataset
        result = seed_dataset_into_workspace(db, base, manifest, workspace_slug=WORKSPACE_SLUG,
                                             workspace_name="Test Workspace")
        return result
    finally:
        db.close()


@pytest.fixture(scope="session")
def credentials(dataset):
    manifest = dataset[1]
    return {u["role"]: {"email": u["email"], "password": u["password"]} for u in manifest["users"]}


@pytest.fixture(scope="session")
def tokens(client, seeded, credentials):
    out = {}
    for role, creds in credentials.items():
        response = client.post("/api/auth/login", json=creds)
        assert response.status_code == 200, response.text
        out[role] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return out


@pytest.fixture(scope="session")
def decision_index(client, tokens):
    response = client.get("/api/decisions?page_size=50", headers=tokens["admin"])
    assert response.status_code == 200
    return {item["title"]: item for item in response.json()["items"]}


@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
