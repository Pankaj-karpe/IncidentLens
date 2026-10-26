import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schema, security
from ..database import get_db
from ..observability import metrics
from ..workers.enqueue import enqueue
from ..workers.tasks import generate_postmortem, triage_incident

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])

def _clean_ends_at(dt: datetime | None) -> datetime | None:
    # Alertmanager sends 0001-01-01T00:00:00Z for alerts that are still firing
    if dt is None or dt.year <= 1:
        return None
    return dt


@router.post("", response_model=schema.AlertIngestResponse, dependencies=[Depends(security.verify_webhook)])
def receive_alerts(payload: schema.AlertmanagerWebhook, db: Session = Depends(get_db)):
    created, resolved, incident_ids = 0, 0, []

    for a in payload.alerts:
        incident = db.scalar(
            select(models.Incident).where(
                models.Incident.fingerprint == a.fingerprint,
                models.Incident.status.in_(["OPEN", "TRIAGED"]),
            )
        )

        if a.status == "firing":
            if incident:
                # Alertmanager repeats firing alerts; same incident, nothing to do
                incident_ids.append(incident.id)
                metrics.ALERTS_RECEIVED.labels("duplicate").inc()
                continue

            alertname = a.labels.get("alertname")
            incident = models.Incident(
                fingerprint=a.fingerprint,
                title=alertname or a.annotations.get("summary") or "Unnamed alert",
                service=a.labels.get("service") or a.labels.get("job"),
                severity=a.labels.get("severity"),
                status="OPEN",
                started_at=a.starts_at,
            )
            db.add(incident)
            db.flush()  # get incident.id

            db.add(
                models.Alert(
                    incident_id=incident.id,
                    alertname=alertname,
                    status=a.status,
                    labels=a.labels,
                    annotations=a.annotations,
                    starts_at=a.starts_at,
                    ends_at=None,
                    raw_payload=a.model_dump(mode="json", by_alias=True),
                )
            )
            db.commit()
            created += 1
            metrics.ALERTS_RECEIVED.labels("created").inc()
            incident_ids.append(incident.id)

            # enqueue AFTER the commit so the worker can see the row
            enqueue(triage_incident, incident.id)

        elif a.status == "resolved" and incident:
            incident.status = "RESOLVED"
            incident.resolved_at = _clean_ends_at(a.ends_at) or datetime.now(UTC)
            incident.resolved_by = "alertmanager"
            
            alert_row = db.scalar(
                select(models.Alert)
                .where(models.Alert.incident_id == incident.id)
                .order_by(models.Alert.id.desc())
            )
            if alert_row:
                alert_row.status = "resolved"
                alert_row.ends_at = incident.resolved_at

            db.commit()
            resolved += 1
            metrics.ALERTS_RECEIVED.labels("resolved").inc()
            incident_ids.append(incident.id)

            # the incident is closed: draft the postmortem in the background
            enqueue(generate_postmortem, incident.id)

    logger.info("alerts received=%s created=%s resolved=%s",
                len(payload.alerts), created, resolved)
    return schema.AlertIngestResponse(
        received=len(payload.alerts),
        created=created,
        resolved=resolved,
        incident_ids=incident_ids,
    )