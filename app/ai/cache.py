"""
To cut costs and speed up response times, we treat our existing incidents table 
as a built-in cache instead of setting up external tools. When a new alert comes 
in, the system checks if a recently analyzed incident shares the exact same 
fingerprint (meaning it's the same recurring issue). If a matching analysis exists
within our allowed time window (CACHE_TTL_MINUTES) and was marked successful 
(ai_status == "OK"), we simply reuse that past debugging result instead of 
making a costly new call to the AI model.
"""

from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timezone, timedelta
from .. import models
from ..config import settings

def find_cached_triage(db: Session, incident: models.Incident) -> models.Incident | None:
    if not incident.fingerprint or settings.CACHE_TTL_MINUTES <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.CACHE_TTL_MINUTES)
    return db.scalar(
        select(models.Incident).where(
            models.Incident.fingerprint == incident.fingerprint,
            models.Incident.id != incident.id,   # not itself
            models.Incident.ai_status == "OK",
            models.Incident.created_at >= cutoff,
        ).order_by(models.Incident.created_at.desc()).limit(1)
    )

def cached_meta(source: models.Incident) -> dict:
    """The extra AI details (confidence, evidence, model) saved in source.context['ai']."""
    return dict((source.context or {}).get("ai") or {})