from sqlalchemy import func, select

from app import models
from app.database import SessionLocal
from app.tests.conftest import alert_body, needs_db


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_metrics_endpoint_serves_our_counters(client):
    assert "incidentlens_alerts_received_total" in client.get("/metrics").text


@needs_db
def test_readyz_checks_the_database(client):
    assert client.get("/readyz").json() == {"status": "ready"}


@needs_db
def test_firing_alert_creates_one_incident_and_one_triage_job(client, fp):
    r = client.post("/alerts", json=alert_body(fp)).json()
    assert r["created"] == 1
    assert client.triage.calls == [(r["incident_ids"][0],)]


@needs_db
def test_repeated_firing_alert_is_a_duplicate(client, fp):
    client.post("/alerts", json=alert_body(fp))
    r = client.post("/alerts", json=alert_body(fp)).json()
    assert r["created"] == 0
    assert len(client.triage.calls) == 1


@needs_db
def test_resolved_alert_closes_the_incident_and_queues_a_postmortem(client, fp):
    incident_id = client.post("/alerts", json=alert_body(fp)).json()["incident_ids"][0]
    r = client.post("/alerts", json=alert_body(fp, status="resolved", ends_at="2026-10-23T10:15:00Z")).json()
    assert r["resolved"] == 1
    assert client.get(f"/incidents/{incident_id}").json()["status"] == "RESOLVED"
    assert client.postmortem.calls == [(incident_id,)]


@needs_db
def test_manual_resolve_is_idempotent(client, fp):
    incident_id = client.post("/alerts", json=alert_body(fp)).json()["incident_ids"][0]
    assert client.post(f"/incidents/{incident_id}/resolve").json()["status"] == "RESOLVED"
    assert client.post(f"/incidents/{incident_id}/resolve").json()["status"] == "RESOLVED"
    assert len(client.postmortem.calls) == 1             # the second click queues nothing


@needs_db
def test_status_filter_ignores_case(client, fp):
    incident_id = client.post("/alerts", json=alert_body(fp)).json()["incident_ids"][0]
    ids = [i["id"] for i in client.get("/incidents", params={"status": "open", "limit": 200}).json()]
    assert incident_id in ids


@needs_db
def test_unknown_incident_is_404(client):
    assert client.get("/incidents/999999999").status_code == 404


@needs_db
def test_postmortem_lifecycle_with_the_no_ai_fallback(client, fp):
    incident_id = client.post("/alerts", json=alert_body(fp)).json()["incident_ids"][0]
    assert client.get(f"/incidents/{incident_id}/postmortem").status_code == 404    # nothing yet

    client.post(f"/incidents/{incident_id}/resolve")
    with SessionLocal() as db:              # pretend triage already finished (no waiting/retry)
        db.get(models.Incident, incident_id).ai_status = "FALLBACK"
        db.commit()

    from app.workers.tasks import generate_postmortem
    generate_postmortem(incident_id)         # runs the REAL task; no LLM is configured -> fallback
    generate_postmortem(incident_id)         # second run must be a no-op

    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(models.Postmortem)
                          .where(models.Postmortem.incident_id == incident_id))
    assert count == 1

    pm = client.get(f"/incidents/{incident_id}/postmortem").json()
    assert pm["status"] == "DRAFT" and pm["model_used"].startswith("fallback")
    assert pm["timeline"], "fallback draft must contain a timeline"

    edited = client.patch(f"/incidents/{incident_id}/postmortem",
                          json={"root_cause": "Confirmed: exhausted DB pool"}).json()
    assert edited["root_cause"] == "Confirmed: exhausted DB pool"
    assert edited["summary"] == pm["summary"]            # fields not sent are untouched

    reviewed = client.post(f"/incidents/{incident_id}/postmortem/review").json()
    assert reviewed["status"] == "REVIEWED"