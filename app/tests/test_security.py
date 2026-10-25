import uuid

import jwt
import pytest

from app import models, security
from app.config import settings
from app.database import SessionLocal
from app.tests.conftest import WEBHOOK_TOKEN, alert_body, needs_db


# ---------------- passwords and tokens (no database) ----------------
def test_password_hash_roundtrip():
    stored = security.hash_password("correct horse battery")
    assert stored != "correct horse battery"
    assert security.verify_password(stored, "correct horse battery")
    assert not security.verify_password(stored, "wrong password")
    assert not security.verify_password("this-is-not-a-hash", "anything")


def test_token_roundtrip():
    token, ttl = security.create_access_token(42)
    assert security.decode_token(token) == 42
    assert ttl == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", -1)
    token, _ = security.create_access_token(1)
    assert security.decode_token(token) is None


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode({"sub": "1", "exp": 4102444800}, "x" * 40, algorithm="HS256")
    assert security.decode_token(forged) is None


def test_unsigned_alg_none_token_is_rejected():
    unsigned = jwt.encode({"sub": "1", "exp": 4102444800}, None, algorithm="none")
    assert security.decode_token(unsigned) is None


def test_startup_validation_rejects_weak_settings(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "too-short")
    with pytest.raises(RuntimeError):
        security.validate_auth_settings()
    monkeypatch.setattr(settings, "SECRET_KEY", "k" * 40)
    monkeypatch.setattr(settings, "ALGORITHM", "none")
    with pytest.raises(RuntimeError):
        security.validate_auth_settings()


# ---------------- webhook (no database needed) ----------------
def test_webhook_rejects_missing_or_wrong_token(client):
    client.headers.pop("Authorization", None)
    assert client.post("/alerts", json={}).status_code == 401
    assert client.post("/alerts", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/alerts", json={}, headers={"Authorization": f"Basic {WEBHOOK_TOKEN}"}).status_code == 401


def test_webhook_accepts_the_right_token(client):
    r = client.post("/alerts", json={}, headers={"Authorization": f"Bearer {WEBHOOK_TOKEN}"})
    assert r.status_code == 200 and r.json()["received"] == 0


def test_webhook_fails_closed_without_a_configured_token(client, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_TOKEN", None)
    assert client.post("/alerts", json={}).status_code == 503


# ---------------- who may do what (denials never reach the database) ----------------
def test_incident_endpoints_need_login(client):
    client.no_user()
    assert client.get("/incidents").status_code == 401
    assert client.get("/incidents", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_viewer_cannot_modify_anything(client):
    client.set_role("viewer")
    assert client.post("/incidents/1/resolve").status_code == 403
    assert client.patch("/incidents/1/postmortem", json={"summary": "x"}).status_code == 403
    assert client.post("/incidents/1/postmortem/review").status_code == 403


def test_developer_cannot_sign_off_a_postmortem(client):
    client.set_role("developer")
    assert client.post("/incidents/1/postmortem/review").status_code == 403


# ---------------- real login flow (needs the test database) ----------------
def make_user(role: str, password: str = "pytest-password-123", active: bool = True):
    name = f"pytest-user-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(models.User(username=name, password_hash=security.hash_password(password),
                           role=role, is_active=active))
        db.commit()
    return name, password


def login(client, name: str, password: str):
    return client.post("/auth/login", data={"username": name, "password": password})


@needs_db
def test_login_success_and_me(client):
    name, pw = make_user("sre")
    client.no_user()
    r = login(client, name, pw)
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["username"] == name and me["role"] == "sre"


@needs_db
def test_login_failures_all_look_the_same(client):
    name, pw = make_user("sre")
    inactive, inactive_pw = make_user("sre", active=False)
    client.no_user()
    for user, password in ((name, "wrong"), ("pytest-nobody", pw), (inactive, inactive_pw)):
        r = login(client, user, password)
        assert r.status_code == 401 and r.json()["detail"] == "Incorrect username or password"


@needs_db
def test_resolve_records_who_did_it(client, fp):
    incident_id = client.post("/alerts", json=alert_body(fp)).json()["incident_ids"][0]
    name, pw = make_user("developer")
    client.no_user()
    token = login(client, name, pw).json()["access_token"]
    r = client.post(f"/incidents/{incident_id}/resolve", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["resolved_by"] == name


@needs_db
def test_role_change_takes_effect_immediately(client, fp):
    incident_id = client.post("/alerts", json=alert_body(fp)).json()["incident_ids"][0]
    name, pw = make_user("developer")
    client.no_user()
    headers = {"Authorization": f"Bearer {login(client, name, pw).json()['access_token']}"}
    with SessionLocal() as db:        # demote while the token is still valid
        db.query(models.User).filter(models.User.username == name).update({"role": "viewer"})
        db.commit()
    assert client.post(f"/incidents/{incident_id}/resolve", headers=headers).status_code == 403


@needs_db
def test_deactivated_user_loses_access_with_a_valid_token(client):
    name, pw = make_user("sre")
    client.no_user()
    headers = {"Authorization": f"Bearer {login(client, name, pw).json()['access_token']}"}
    with SessionLocal() as db:
        db.query(models.User).filter(models.User.username == name).update({"is_active": False})
        db.commit()
    assert client.get("/incidents", headers=headers).status_code == 401


@needs_db
def test_editing_after_review_requires_a_new_review(client, fp):
    incident_id = client.post("/alerts", json=alert_body(fp)).json()["incident_ids"][0]
    with SessionLocal() as db:
        db.add(models.Postmortem(incident_id=incident_id, summary="first draft", status="DRAFT"))
        db.commit()
    reviewed = client.post(f"/incidents/{incident_id}/postmortem/review").json()
    assert reviewed["status"] == "REVIEWED" and reviewed["reviewed_by"] == "tester"
    edited = client.patch(f"/incidents/{incident_id}/postmortem", json={"summary": "changed"}).json()
    assert edited["status"] == "DRAFT" and edited["reviewed_by"] is None