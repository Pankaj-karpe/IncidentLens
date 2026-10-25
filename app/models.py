from .database import Base
from sqlalchemy.sql import func
from sqlalchemy import Column, Integer, String, Text, Numeric, TIMESTAMP, ForeignKey, Index, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from sqlalchemy.sql import func, expression

EMBEDDING_DIM = 1536 


############################ USERS ############################
class User(Base):
    """People who may use the API. Created from the command line (python -m app.cli)."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, unique=True, index=True)   # always lowercase
    password_hash = Column(String, nullable=False)                       # argon2, never the password
    role = Column(String, nullable=False, default="viewer", server_default="viewer")  # viewer, developer, devops, sre
    is_active = Column(Boolean, nullable=False, default=True, server_default=expression.true())
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


    
############################ INCIDENTS ############################
class Incident(Base):
    __tablename__ = "incidents"
    id = Column(Integer, primary_key=True)
    fingerprint = Column(String, index=True)
    title = Column(String, nullable=False)
    service = Column(String, index=True)
    severity = Column(String)  # critical, warning, info
    status = Column(String, index=True, nullable=False, default="OPEN", server_default="OPEN")  # OPEN, TRIAGED, RESOLVED
    context = Column(JSONB, nullable=True)
    ai_summary = Column(Text, nullable=True)
    ai_root_cause = Column(Text, nullable=True)
    ai_next_steps = Column(JSONB, nullable=True)
    ai_status = Column(String, nullable=False, default="PENDING", server_default="PENDING")  # PENDING, OK, FAILED, FALLBACK
    started_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by = Column(String, nullable=True)   # username, or "alertmanager"
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    alerts = relationship("Alert", back_populates="incident", cascade="all, delete-orphan")
    postmortem = relationship("Postmortem", back_populates="incident", cascade="all, delete-orphan")
    incident_embeddings = relationship("Incident_Embedding", back_populates="incident", uselist=False, cascade="all, delete-orphan")
    llm_calls = relationship("Llm_Call", back_populates="incident", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_incidents_service_started", "service", "started_at"),
    )


############################ ALERTS ############################
class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    alertname = Column(String)
    status = Column(String)  # firing, resolved
    labels = Column(JSONB)
    annotations = Column(JSONB)
    starts_at = Column(TIMESTAMP(timezone=True), nullable=True)
    ends_at = Column(TIMESTAMP(timezone=True), nullable=True)
    raw_payload = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    incident = relationship("Incident", back_populates="alerts")

############################ POSTMORTEMS ############################
class Postmortem(Base):
    __tablename__ = "postmortems"
    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, unique=True)
    summary = Column(Text)
    timeline = Column(JSONB)
    root_cause = Column(Text)
    impact = Column(Text)
    action_items = Column(JSONB)
    status = Column(String, nullable=False, default="DRAFT", server_default="DRAFT")  # DRAFT, REVIEWED
    model_used = Column(String)
    reviewed_by = Column(String, nullable=True)   # who signed it off    
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    incident = relationship("Incident", back_populates="postmortem")

############################ INCIDENT_EMBEDDINGS ############################
class Incident_Embedding(Base):
    __tablename__ = "incident_embeddings"
    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, unique=True)
    content = Column(Text)
    embedding = Column(Vector(EMBEDDING_DIM))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    incident = relationship("Incident", back_populates="incident_embeddings")

    __table_args__ = (
        Index(
            "idx_incident_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

############################ LLM_CALLS ############################
class Llm_Call(Base):
    __tablename__ = "llm_calls"
    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=True, index=True)
    purpose = Column(String)  # triage, postmortem, embedding
    model = Column(String)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    cost_usd = Column(Numeric(10,6))
    latency_ms = Column(Integer)
    success = Column(Boolean)
    error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    incident = relationship("Incident", back_populates="llm_calls")