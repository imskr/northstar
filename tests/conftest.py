from __future__ import annotations

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NORTHSTAR_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("ALLOW_REGISTRATION", "true")

    from northstar.db import get_database

    get_database.cache_clear()
    from northstar import create_app

    application = create_app({"TESTING": True})
    yield application
    get_database.cache_clear()


@pytest.fixture()
def client(app):
    return app.test_client()
