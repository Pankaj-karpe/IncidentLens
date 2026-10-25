"""
Instead of skipping the AI call, this system acts as a smart historical assistant
that gives the AI extra context to decode the issue more accurately. When a new 
incident occurs, it turns the description into an embedding (a list of numbers)
and uses pgvector to mathematically search PostgreSQL for past incidents that mean
the same thing—even if they use completely different words. If it finds a similar 
past issue, it pulls up those historical details and injects them straight into 
the AI prompt, giving the model a "cheat sheet" to diagnose and handle the 
current error much better.
"""

import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.ai.redact import redact_text
from app.config import settings


class EmbeddingError(Exception):
    pass

@dataclass
class Embedding:
    vector: list[float]
    tokens: int
    latency_ms: int
    model: str

def is_configured() -> bool:
    return bool(settings.EMBEDDING_API_KEY)

def build_embedding_text(incident: models.Incident, annotations: dict) -> str:
    """
    A short description of 'what this incident looks like': alert name, service,
    severity, the alert's own summary, and the first 3 error lines.
    Redacted, because it leaves our system too.
    (get all the incident info and redact it)
    """
    ctx = incident.context or {}
    log_lines = [l.get("line", "") for l in ((ctx.get("logs") or {}).get("lines") or [])[:3]]
    parts = [
        f"alert: {incident.title}",
        f"service: {incident.service or 'unknown'}",
        f"severity: {incident.severity or 'unknown'}",
        f"summary: {annotations.get('summary', '')}",
        f"description: {annotations.get('description', '')}",
        *[f"log: {l}" for l in log_lines],
    ]
    return redact_text("\n".join(parts))[:4000]


def embed_text(text: str) -> Embedding:
    # (sends that text to the AI model to turn it into those 1536 numbers (the vector).)
    if not is_configured():
        raise EmbeddingError("embeddings not configured")
    body_in = {"model": settings.EMBEDDING_MODEL, "input": text}
    if settings.EMBEDDING_DIMENSIONS:
        body_in["dimensions"] = settings.EMBEDDING_DIMENSIONS   # shrink to fit the DB column

    started = time.perf_counter()
    try:
        resp = httpx.post(
            f"{settings.EMBEDDING_BASE_URL.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
            json=body_in,
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        vector = body["data"][0]["embedding"]
        tokens = int((body.get("usage") or {}).get("prompt_tokens", 0))
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise EmbeddingError(f"embedding call failed: {type(e).__name__}") from e
    
    # The DB column is fixed-size. A different model would produce a different size.
    if len(vector) != models.EMBEDDING_DIM:
        raise EmbeddingError(
            f"embedding has {len(vector)} dims, DB column expects {models.EMBEDDING_DIM}"
        )
    return Embedding(vector, tokens, int((time.perf_counter() - started) * 1000), settings.EMBEDDING_MODEL)


def find_similar(db: Session, incident_id: int, vector: list[float]) -> list[dict]:
    """
    cosine_distance: 0 = identical direction, 2 = opposite. similarity = 1 - distance.
    Uses the HNSW index created by the migration. Only incidents that have an
    AI root cause are useful as "past knowledge" (fallback ones have none).
    """
    """
    the find_similar function acts as a smart historical search engine, using vector
    math to compare the current incident's coordinates against past successful
    incidents in the database. It calculates similarity scores, filters out weak
    matches, and packages the closest historical root causes into a clean "cheat 
    sheet." This list is then handed directly to the AI prompt, giving the model 
    precise context on how similar past issues were resolved.
    """
    distance = models.Incident_Embedding.embedding.cosine_distance(vector).label("distance")
    rows = db.execute(
        select(models.Incident, distance)
        .join(models.Incident_Embedding,
              models.Incident_Embedding.incident_id == models.Incident.id)
        .where(models.Incident.id != incident_id,
               models.Incident.ai_root_cause.isnot(None))
        .order_by(distance)
        .limit(settings.SIMILAR_INCIDENT_LIMIT)
    ).all()

    similar = []
    for inc, dist in rows:
        score = round(1 - float(dist), 3)
        if score < settings.SIMILARITY_MIN_SCORE:   # too different to be helpful
            continue
        similar.append({
            "incident_id": inc.id,
            "title": inc.title,
            "service": inc.service,
            "similarity": score,
            "summary": inc.ai_summary,
            "root_cause": inc.ai_root_cause,
        })
    return similar

def store_embedding(db: Session, incident_id: int, content: str, vector: list[float]) -> None:
    """Insert or update (incident_id is UNIQUE in the table), then commit."""
    row = db.scalar(select(models.Incident_Embedding)
                    .where(models.Incident_Embedding.incident_id == incident_id))
    if row:
        row.content, row.embedding = content, vector
    else:
        db.add(models.Incident_Embedding(incident_id=incident_id, content=content,
                                         embedding=vector))
    db.commit()