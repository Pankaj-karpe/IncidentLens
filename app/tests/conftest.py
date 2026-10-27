"""
Shared test setup.
SAFETY: tests never use your dev database. DB tests need TEST_DATABASE_URL (a throwaway DB
with the schema applied: `alembic upgrade head`) and are SKIPPED otherwise.
This file sets env vars BEFORE the app is imported, because settings are read at import time
(real env vars beat the .env file, so your real keys/webhook can't leak into tests).
"""
import os
import uuid
from types import SimpleNamespace

import pytest

TEST_DB = os.getenv("TEST_DATABASE_URL")
_dummy = "postgresql+psycopg://test:test@localhost:5432/test"
WEBHOOK_TOKEN = "test-webhook-token-0123456789abcdef"

os.environ["DATABASE_URL"] = TEST_DB or _dummy       # engine is lazy: nothing connects at import
os.environ["ALEMBIC_URL"] = TEST_DB or _dummy
os.environ["BROKER_URL"] = "memory://"               # never talk to a real RabbitMQ
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-only-0123456789abcdef"
os.environ["ALGORITHM"] = "HS256"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
os.environ["WEBHOOK_TOKEN"] = WEBHOOK_TOKEN

needs_db = pytest.mark.skipif(not TEST_DB, reason="set TEST_DATABASE_URL to run DB tests")


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    """Whatever is in .env, tests have no LLM, no embeddings, no Slack, no email, no tracing."""
    from app.config import settings
    for name, value in {
        "LLM_API_KEY": None, "LLM_BASE_URL": None, "LLM_FALLBACK_MODEL": None,
        "LLM_FALLBACK_BASE_URL": None, "EMBEDDING_API_KEY": None, "SLACK_WEBHOOK_URL": None,
        "SMTP_HOST": None, "NOTIFY_ENABLED": False, "OTEL_ENABLED": False,
    }.items():
        monkeypatch.setattr(settings, name, value)


class FakeTask:
    """Stands in for a Celery task: records .delay() calls instead of using a broker."""
    name = "fake-task"

    def __init__(self):
        self.calls = []

    def delay(self, *args):
        self.calls.append(args)


def fake_user(role: str = "sre", username: str = "tester"):
    """A logged-in user without touching the database."""
    return SimpleNamespace(id=0, username=username, role=role, is_active=True)


@pytest.fixture()
def client(monkeypatch):
    """
    Default: logged in as an "sre" (override of get_current_user) and sending the webhook
    token, so every pre-auth test keeps working. Change who is calling with:
        client.set_role("viewer")      client.no_user()   (= real token check)
    """
    from fastapi.testclient import TestClient

    from app import security
    from app.main import app
    from app.routers import alerts, incidents

    triage, postmortem = FakeTask(), FakeTask()
    monkeypatch.setattr(alerts, "triage_incident", triage)
    monkeypatch.setattr(alerts, "generate_postmortem", postmortem)
    monkeypatch.setattr(incidents, "generate_postmortem", postmortem)

    def set_role(role: str, username: str = "tester"):
        app.dependency_overrides[security.get_current_user] = lambda: fake_user(role, username)

    def no_user():
        app.dependency_overrides.pop(security.get_current_user, None)

    set_role("sre")
    with TestClient(app, headers={"Authorization": f"Bearer {WEBHOOK_TOKEN}"}) as c:
        c.triage, c.postmortem, c.set_role, c.no_user = triage, postmortem, set_role, no_user
        yield c
    no_user()


@pytest.fixture()
def fp():
    """A unique fingerprint; the cleanup below deletes everything that starts with 'pytest-'."""
    return f"pytest-{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def cleanup_db():
    yield
    if not TEST_DB:
        return
    from sqlalchemy import delete

    from app import models
    from app.database import SessionLocal
    with SessionLocal() as db:   # child tables go too: their foreign keys are ON DELETE CASCADE
        db.execute(delete(models.Incident).where(models.Incident.fingerprint.like("pytest-%")))
        db.execute(delete(models.User).where(models.User.username.like("pytest-%")))
        db.commit()


def alert_body(fingerprint: str, status: str = "firing", service: str = "backend", ends_at: str | None = None) -> dict:
    alert = {
        "status": status,
        "labels": {"alertname": "HighErrorRate", "service": service, "severity": "critical"},
        "annotations": {"summary": "5xx above 5%"},
        "startsAt": "2026-10-23T10:00:00Z",
        "fingerprint": fingerprint,
    }
    if ends_at:
        alert["endsAt"] = ends_at
    return {"version": "4", "status": status, "alerts": [alert]}