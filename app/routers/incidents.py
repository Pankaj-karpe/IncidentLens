from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schema, security
from ..database import get_db
from ..workers.enqueue import enqueue
from ..workers.tasks import generate_postmortem

# EVERY route here needs a logged-in, active user (any role = read access).
router = APIRouter(prefix="/incidents", tags=["incidents"],
                   dependencies=[Depends(security.get_current_user)])


# Write access is stricter, per endpoint (the roles are defined in security.py).
can_modify = security.require_roles(*security.MODIFY_ROLES)   # developer, devops, sre
can_review = security.require_roles(*security.REVIEW_ROLES)   # devops, sre


def _get_incident(db: Session, incident_id: int) -> models.Incident:
    incident = db.get(models.Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def _get_postmortem(db: Session, incident_id: int) -> models.Postmortem:
    _get_incident(db, incident_id)    # 404 "Incident not found" takes priority
    pm = db.scalar(select(models.Postmortem).where(models.Postmortem.incident_id == incident_id))
    if not pm:
        raise HTTPException(status_code=404,
                            detail="Postmortem not generated yet (it is drafted after the incident is resolved)")
    return pm


################### get all the incidents ###################
@router.get("", response_model=list[schema.IncidentOut])
def list_incidents(
    status: str | None = Query(None, description="OPEN | TRIAGED | RESOLVED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    # latest incident is at top
    stmt = select(models.Incident).order_by(models.Incident.created_at.desc())
    if status:
        stmt = stmt.where(models.Incident.status == status.upper())
    return db.scalars(stmt.limit(limit).offset(offset)).all()


################### get a incident ###################
@router.get("/{incident_id}", response_model=schema.IncidentOut)
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    return _get_incident(db, incident_id)


################## resolve (human in the loop) ###################
@router.post("/{incident_id}/resolve", response_model=schema.IncidentOut)
def resolve_incident(incident_id: int, db: Session = Depends(get_db), user: models.User = Depends(can_modify)):
    incident = _get_incident(db, incident_id)
    if incident.status == "RESOLVED":
        return incident  # idempotent: no second postmortem job

    incident.status = "RESOLVED"
    incident.resolved_at = datetime.now(UTC)
    incident.resolved_by = user.username
    db.commit()
    db.refresh(incident)
    security.audit(user, "incident.resolve", incident=incident.id)
    enqueue(generate_postmortem, incident.id)
    return incident


################### postmortem: read, edit, review ###################
@router.get("/{incident_id}/postmortem", response_model=schema.PostmortemOut)
def get_postmortem(incident_id: int, db: Session = Depends(get_db)):
    return _get_postmortem(db, incident_id)


@router.patch("/{incident_id}/postmortem", response_model=schema.PostmortemOut)
def edit_postmortem(incident_id: int, body: schema.PostmortemUpdate, db: Session = Depends(get_db), user: models.User = Depends(can_modify)):
    """The AI writes a DRAFT; the engineer corrects it. Only the fields sent are changed."""
    pm = _get_postmortem(db, incident_id)
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:      # null means "leave as is"
            setattr(pm, key, value)       # assigning a NEW list/str lets SQLAlchemy see the change

    if changes and pm.status == "REVIEWED":
        # The sign-off applied to the OLD text, so an edited postmortem needs a new review.
        pm.status = "DRAFT"
        pm.reviewed_by = None
    db.commit()
    db.refresh(pm)
    security.audit(user, "postmortem.edit", incident=incident_id, fields=",".join(sorted(changes)) or "-")
    return pm


################### postmortem sign-off (devops, sre only) ###################
@router.post("/{incident_id}/postmortem/review", response_model=schema.PostmortemOut)
def review_postmortem(incident_id: int, db: Session = Depends(get_db),
                      user: models.User = Depends(can_review)):
    """The human sign-off: DRAFT -> REVIEWED. Nothing is ever auto-published."""
    pm = _get_postmortem(db, incident_id)
    pm.status = "REVIEWED"
    pm.reviewed_by = user.username
    db.commit()
    db.refresh(pm)
    security.audit(user, "postmortem.review", incident=incident_id)
    return pm
