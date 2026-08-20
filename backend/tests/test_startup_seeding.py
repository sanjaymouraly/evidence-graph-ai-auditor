"""Multi-worker startup behaviour.

`uvicorn --workers N` runs the lifespan in every worker. Two of them starting at once must
not both seed the demo workspace: the loser used to log a UNIQUE-constraint error, and a
production deployment looked broken on first boot.
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import delete, func, select

from app.db.models import Workspace
from app.db.session import SessionLocal, init_db

SLUG_PREFIX = "startup-test-"


def _cleanup() -> None:
    db = SessionLocal()
    try:
        db.execute(delete(Workspace).where(Workspace.slug.like(f"{SLUG_PREFIX}%")))
        db.commit()
    finally:
        db.close()


def _workspace_total() -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.count()).select_from(Workspace)) or 0
    finally:
        db.close()


def test_concurrent_seed_creates_exactly_one_workspace(monkeypatch):
    from app import main as app_main

    init_db()
    _cleanup()
    monkeypatch.setattr(app_main.settings, "seed_demo_data", True)
    monkeypatch.setattr(app_main.settings, "environment", "development")

    created: list[str] = []

    def slow_fake_seed(force: bool = False):
        # A real seed parses 25 documents; the delay widens the race window so the lock,
        # not luck, has to be what prevents a second insert.
        time.sleep(0.2)
        db = SessionLocal()
        try:
            db.add(Workspace(name="Startup test", slug=f"{SLUG_PREFIX}{len(created)}", settings={}))
            db.commit()
            created.append("seeded")
        finally:
            db.close()
        return {"workspace_slug": "startup-test"}

    monkeypatch.setattr("app.seed.seed_demo", slow_fake_seed)
    # Emulate "empty database until someone seeds" independently of whatever workspaces the
    # rest of the suite created: the lock must be what serialises the two workers.
    monkeypatch.setattr(app_main, "_workspace_count", lambda: 0 if not created else 1)

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            app_main._ensure_seed()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(created) == 1, f"expected exactly one seed, got {len(created)}"
    assert _workspace_total() >= 1
    _cleanup()


def test_seed_is_skipped_when_a_workspace_exists(monkeypatch):
    from app import main as app_main

    init_db()
    db = SessionLocal()
    try:
        db.add(Workspace(name="Existing", slug=f"{SLUG_PREFIX}existing", settings={}))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(app_main.settings, "seed_demo_data", True)
    monkeypatch.setattr(app_main.settings, "environment", "development")
    called: list[bool] = []
    monkeypatch.setattr("app.seed.seed_demo", lambda force=False: called.append(force))

    app_main._ensure_seed()
    assert called == []
    _cleanup()


def test_seed_is_disabled_in_test_environment(monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main.settings, "seed_demo_data", True)
    monkeypatch.setattr(app_main.settings, "environment", "test")
    called: list[bool] = []
    monkeypatch.setattr("app.seed.seed_demo", lambda force=False: called.append(force))

    app_main._ensure_seed()
    assert called == []
