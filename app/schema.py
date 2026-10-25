from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Optional, List, Any , Literal
from decimal import Decimal

# Base Config to enable ORM mode across all schemas
class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

# ==========================================
# 1. ALERTS
# ==========================================

# Represents a single individual alert sent from Prometheus, 
# capturing its specific status, labels, and timestamps.
class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generatorURL: str | None = None
    fingerprint: str


# Acts as the outer envelope for the incoming Prometheus payload, 
# safely accepting the batch wrapper so your code can focus directly on the alerts list.
class AlertmanagerWebhook(BaseModel):
    version: str | None = None
    groupKey: str | None = None
    status: str | None = None
    receiver: str | None = None
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str | None = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


# Returns a quick summary report of what happened after 
# processing the batch (counts of received, created, and resolved items).
class AlertIngestResponse(BaseModel):
    received: int
    created: int
    resolved: int
    incident_ids: list[int]


# ==========================================
# 1. Incidents
# ==========================================

# Acts as a safe API boundary filter that translates internal 
# database models into clean, controlled JSON responses for the client.
class IncidentOut(ORMBase):
    id: int
    fingerprint: str
    title: str
    service: str | None = None
    severity: str | None = None
    status: Literal["OPEN", "TRIAGED", "RESOLVED"]
    ai_status: Literal["PENDING", "OK", "FAILED", "FALLBACK"]
    ai_summary: str | None = None
    ai_root_cause: str | None = None
    ai_next_steps: list | dict | None = None
    started_at: datetime
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None

# ==========================================
# LLM OUTPUT SCHEMAS (what we REQUIRE the model to return)
# ==========================================
class TriageOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=1000)    # 1-3 sentences
    likely_cause: str = Field(min_length=1, max_length=1000)    # getting root cause
    confidence: Literal["low", "medium", "high"] 
    evidence: list[str] = Field(default_factory=list, max_length=10)  # facts used to figure out the cause
    next_steps: list[str] = Field(min_length=1, max_length=8)    # what to check


class TimelineEvent(BaseModel):
    time: str = Field(min_length=1, max_length=64)
    event: str = Field(min_length=1, max_length=500)


class PostmortemOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=1500)
    impact: str = Field(min_length=1, max_length=1500)
    root_cause: str = Field(min_length=1, max_length=1500)
    timeline: list[TimelineEvent] = Field(min_length=1, max_length=20)
    action_items: list[str] = Field(min_length=1, max_length=10)


# ==========================================
# POSTMORTEM API MODELS
# ==========================================
class PostmortemOut(ORMBase):
    """What GET /incidents/{id}/postmortem returns (reads straight from the ORM row)."""
    id: int
    incident_id: int
    summary: str | None = None
    impact: str | None = None
    root_cause: str | None = None
    timeline: list | None = None          # [{"time": "...", "event": "..."}]
    action_items: list | None = None
    status: Literal["DRAFT", "REVIEWED"]
    model_used: str | None = None         # model name, or "fallback (no AI)"
    created_at: datetime
    reviewed_by: str | None = None


class PostmortemUpdate(BaseModel):
    """PATCH body: the engineer edits the AI draft. Only the fields sent are changed."""
    summary: str | None = Field(default=None, max_length=1500)
    impact: str | None = Field(default=None, max_length=1500)
    root_cause: str | None = Field(default=None, max_length=1500)
    timeline: list[TimelineEvent] | None = Field(default=None, max_length=20)
    action_items: list[str] | None = Field(default=None, max_length=10)


# ==========================================
# AUTH
# ==========================================
class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int                          # seconds until the token expires


class UserOut(ORMBase):
    id: int
    username: str
    role: Literal["viewer", "developer", "devops", "sre"]
    is_active: bool