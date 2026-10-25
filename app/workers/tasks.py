import logging
import time

from celery.exceptions import Retry
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app import models
from app.ai import cache, embeddings, prompts
from app.ai.client import is_configured as llm_configured
from app.ai.validate import run_structured
from app.collectors import collect_context
from app.config import settings
from app.database import SessionLocal
from app.notifiers import slack
from app.observability import metrics, tracing
from app.schema import PostmortemOutput, TriageOutput
from app.workers.celeryapp import celery_app

logger = logging.getLogger(__name__)


def _fallback_summary(incident: models.Incident, annotations: dict) -> str:
    """Graceful degradation: if AI fails, the engineer still gets the raw alert facts."""
    note = annotations.get("summary") or annotations.get("description") or "no description on the alert"
    return (
        f"{incident.title} on {incident.service or 'unknown service'} "
        f"({incident.severity or 'unknown'} severity): {note}. "
        "AI triage unavailable; review the collected metrics, logs and deploys."
    )


def _embed_and_search(db, incident, annotations):
    """
    _embed_and_search coordinates the workflow by building the incident's text 
    summary, converting it into a vector via the embedding API, logging the API 
    usage and costs, and searching the database for similar past incidents to 
    provide the AI with a helpful historical cheat sheet.
    """
    if not embeddings.is_configured():
        return None, None, []

    #Opens an OpenTelemetry span so you can monitor how long embedding and searching take in Jaeger.
    with tracing.span("embed_and_search", {"incident.id": incident.id}) as sp:
        text = embeddings.build_embedding_text(incident, annotations)
        t0 = time.perf_counter()
        try:
            emb = embeddings.embed_text(text)
        except embeddings.EmbeddingError as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("embedding skipped for incident %s: %s", incident.id, e)
            # failures are recorded too, in the DB table AND in Prometheus
            db.add(models.Llm_Call(
                incident_id=incident.id, 
                purpose="embedding", 
                model=settings.EMBEDDING_MODEL,
                input_tokens=0, 
                output_tokens=0, 
                cost_usd=0, 
                latency_ms=elapsed_ms,
                success=False, 
                error=str(e)[:500],
            ))
            metrics.record_llm_call("embedding", settings.EMBEDDING_MODEL, 0, 0, 0.0, elapsed_ms, False)
            return None, None, []

        db.add(models.Llm_Call(
            incident_id=incident.id, 
            purpose="embedding", 
            model=emb.model,
            input_tokens=emb.tokens, 
            output_tokens=0, 
            cost_usd=0,
            latency_ms=emb.latency_ms, 
            success=True,
        ))
        metrics.record_llm_call("embedding", emb.model, emb.tokens, 0, 0.0, emb.latency_ms, True)
        similar = embeddings.find_similar(db, incident.id, emb.vector)    # uses new generated vector to query your db for past incidents
        tracing.set_attrs(sp, {"similar.count": len(similar)})   # attach count of similar incidents to active tracing span
        return text, emb.vector, similar   # Returns the raw text, the vector coordinates, and the list of similar past incidents for the AI prompt context.


def _cap_confidence(model_confidence: str, context: dict) -> str:
    """
    Guardrail the LLM cannot override. If our own collectors FAILED (an 'error' is set,
    not just 'no rows found'), the evidence is incomplete, so confidence is capped:
      both metrics and logs failed -> "low";  one failed -> at most "medium".
    """
    ctx = context or {}
    failed = [s for s in ("metrics", "logs") if (ctx.get(s) or {}).get("error")]
    cap = {0: "high", 1: "medium"}.get(len(failed), "low")
    order = {"low": 0, "medium": 1, "high": 2}
    return model_confidence if order[model_confidence] <= order[cap] else cap


def _llm_triage(db, incident, annotations, similar):
    """
    _llm_triage builds the AI prompt using the incident details and historical 
    similar incidents, runs the structured LLM call (handling any validation retries), 
    logs every attempt's metrics to the database, and returns the AI's final 
    diagnostic diagnosis (root cause, confidence, and evidence).
    """
    if not llm_configured():
        return None, {"error": "LLM not configured"}

    # opens jaeger tracing span(llm.triage) -> assembles system & user prompts (using incident data and past similar incidents) ->
    # executes call(run_structured) -> forcing the LLM response to match a strict Pydantic model (TriageOutput).
    with tracing.span("llm.triage", {"incident.id": incident.id}) as sp:
        system, user = prompts.build_triage_messages(incident, annotations, similar)
        result = run_structured(TriageOutput, system, user, purpose="triage")

        for rec in result.calls:    # one row per API call, successes AND failures
            db.add(models.Llm_Call(
                incident_id=incident.id, 
                purpose=rec.purpose, 
                model=rec.model,
                input_tokens=rec.input_tokens, 
                output_tokens=rec.output_tokens,
                cost_usd=rec.cost_usd, 
                latency_ms=rec.latency_ms,
                success=rec.success, 
                error=rec.error,
            ))
            metrics.record_llm_call(rec.purpose, rec.model, rec.input_tokens, rec.output_tokens, rec.cost_usd, rec.latency_ms, rec.success)

        # Enriches the active Jaeger trace with more metrics as below
        tracing.set_attrs(sp, {
            "llm.model": result.model,
            "llm.attempts": len(result.calls),
            "llm.success": result.data is not None,
            "llm.input_tokens": sum(r.input_tokens for r in result.calls),
            "llm.output_tokens": sum(r.output_tokens for r in result.calls),
            "llm.cost_usd": sum(r.cost_usd for r in result.calls),
        })

    if result.data is None:
        return None, {"error": result.error}
    return result.data, {
        "model": result.model,
        "confidence": _cap_confidence(result.data.confidence, incident.context),
        "model_confidence": result.data.confidence,   # what the model claimed, kept for auditing
        "evidence": result.data.evidence,
    }


def _notify(db, incident, annotations):
    """Send Slack/email, then store what happened. Never raises (notify_triage is safe)."""
    result = slack.notify_triage(incident, annotations)
    try:
        # NEW dict so SQLAlchemy notices the JSONB change
        incident.context = {**(incident.context or {}), "notify": result}
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("could not store notify result for incident %s", incident.id)


@celery_app.task(name="triage_incident")
def triage_incident(incident_id: int):
    started = time.perf_counter()
    db = SessionLocal()
    try:
        incident = db.get(models.Incident, incident_id)
        if not incident:
            logger.warning("incident %s not found", incident_id)
            return
        if incident.ai_status == "OK":      # task was delivered twice: don't pay twice
            logger.info("incident %s already triaged, skipping", incident_id)
            return

        alert = db.scalar(select(models.Alert)
                          .where(models.Alert.incident_id == incident.id)
                          .order_by(models.Alert.id))
        annotations = (alert.annotations if alert else None) or {}

        # 1. Evidence: metrics + logs + deploys (never raises, see collectors/)
        with tracing.span("collect_context", {"incident.id": incident_id}):
            incident.context = collect_context(incident.service or "", incident.started_at)
            metrics.record_collector_results(incident.context)
        db.commit()

        # 2. Similar past incidents 
        emb_text, vector, similar = _embed_and_search(db, incident, annotations)

        # 3. Cache first, LLM second
        cached = cache.find_cached_triage(db, incident)
        if cached:
            metrics.CACHE_HITS.inc()
            triage = None
            meta = {**cache.cached_meta(cached), "cached_from": cached.id}
            logger.info("incident %s reused triage of incident %s", incident_id, cached.id)
        else:
            triage, meta = _llm_triage(db, incident, annotations, similar)

        # 4. Save. Reload first: an engineer or Alertmanager may have resolved
        #    the incident while the LLM was thinking; don't overwrite that.
        db.refresh(incident)
        if cached:
            incident.ai_summary = cached.ai_summary
            incident.ai_root_cause = cached.ai_root_cause
            incident.ai_next_steps = cached.ai_next_steps
            incident.ai_status = "OK"
        elif triage:
            incident.ai_summary = triage.summary
            incident.ai_root_cause = triage.likely_cause
            incident.ai_next_steps = triage.next_steps
            incident.ai_status = "OK"
        else:
            incident.ai_summary = _fallback_summary(incident, annotations)
            incident.ai_status = "FALLBACK"
            logger.warning("incident %s AI fallback: %s", incident_id, meta.get("error"))

        # reassign a NEW dict so SQLAlchemy notices the JSONB change
        incident.context = {**(incident.context or {}), "ai": meta}
        if incident.status == "OPEN":
            incident.status = "TRIAGED"
        db.commit()
        logger.info("incident %s triaged, ai_status=%s", incident_id, incident.ai_status)
        metrics.INCIDENTS_TRIAGED.labels(incident.ai_status).inc()
        metrics.TRIAGE_DURATION.observe(time.perf_counter() - started)

        # 5. Tell the engineer (best effort; also sends when ai_status is FALLBACK,
        #    because raw facts are better than silence)
        if incident.status == "RESOLVED":
            logger.info("incident %s resolved during triage, not notifying", incident_id)
        else:
            _notify(db, incident, annotations)

        # 6. Store the vector so future incidents can find this one (best effort).
        #    Only when the analysis is useful: a "can't tell" answer (low confidence)
        #    would pollute the similar-incident search with noise.
        if vector is not None and meta.get("confidence") in ("medium", "high"):
            try:
                embeddings.store_embedding(db, incident.id, emb_text, vector)
            except Exception:
                db.rollback()
                logger.exception("could not store embedding for incident %s", incident_id)

    except Exception:
        db.rollback()
        logger.exception("triage failed for incident %s", incident_id)
        metrics.INCIDENTS_TRIAGED.labels("FAILED").inc()
        try:  # make the failure visible instead of leaving PENDING forever
            db.execute(update(models.Incident)
                       .where(models.Incident.id == incident_id,
                              models.Incident.ai_status == "PENDING")
                       .values(ai_status="FAILED"))
            db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


# ===========================================================================
# POSTMORTEM: runs after an incident is RESOLVED
# ===========================================================================
def _fallback_postmortem(incident: models.Incident) -> dict:
    """
    Under the philosophy of graceful degradation, IncidentLens ensures that if your 
    LLM provider fails or returns garbage data, the application never crashes or blocks 
    your workflow. Instead, it seamlessly falls back to a pre-populated template 
    automatically built with hard facts like timestamps, service names, and telemetry logs. 
    This means the engineer receives a data-rich draft rather than a blank page, enabling 
    them to quickly refine the root cause and remediation steps without interruption.
    """
    service = incident.service or "unknown service"
    started, resolved = incident.started_at, incident.resolved_at
    duration = (
        f"{int((resolved - started).total_seconds() // 60)} minutes"
        if started and resolved else "an unknown duration"
    )
    timeline = []
    if started:
        timeline.append({"time": started.isoformat(),
                         "event": f"Alert '{incident.title}' fired for {service}"})
    if resolved:
        timeline.append({"time": resolved.isoformat(),
                         "event": "Incident marked resolved"})
    if not timeline:
        timeline.append({"time": "unknown", "event": "No timestamps were recorded"})
    return {
        "summary": (f"{incident.title} on {service} ({incident.severity or 'unknown'} severity) "
                    f"lasted {duration}. This draft was generated WITHOUT AI: please complete it."),
        "impact": "Unknown - needs human input",
        "root_cause": incident.ai_root_cause or "Unknown - needs human input",
        "timeline": timeline,
        "action_items": [
            "Write down the confirmed root cause",
            "Add or tune the alert that would catch this sooner",
            "Add a runbook entry for this failure",
        ],
    }


def _save_llm_calls(db, incident_id: int, calls) -> None:
    """
    The _save_llm_calls function centralizes audit tracking by iterating through 
    every LLM API attempt, recording token usage, cost, and latency to the database, 
    and updating Prometheus telemetry. By performing an immediate database commit, 
    it ensures that your financial and usage records are safely preserved even if 
    a later step in the pipeline fails.
    """
    for rec in calls:
        db.add(models.Llm_Call(
            incident_id=incident_id,
            purpose=rec.purpose,
            model=rec.model,
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cost_usd=rec.cost_usd,
            latency_ms=rec.latency_ms,
            success=rec.success,
            error=rec.error,
        ))
        metrics.record_llm_call(
            rec.purpose,
            rec.model,
            rec.input_tokens,
            rec.output_tokens,
            rec.cost_usd,
            rec.latency_ms,
            rec.success
        )
    db.commit()


def _llm_postmortem(db, incident, alerts):
    """
    the function checks the LLM configuration, wraps the postmortem generation in 
    a Jaeger tracing span using pre-fetched data structured via Pydantic, and 
    saves the LLM audit metrics.
    """
    if not llm_configured():
        return None, None, "LLM not configured"

    try:
        with tracing.span("llm.postmortem", {"incident.id": incident.id}) as sp:
            # the prompt builder redacts everything before it leaves the system
            system, user = prompts.build_postmortem_messages(incident, alerts)
            result = run_structured(
                PostmortemOutput,
                system,
                user,
                purpose="postmortem",
                max_tokens=settings.POSTMORTEM_MAX_OUTPUT_TOKENS
            )
            tracing.set_attrs(sp, {
                "llm.model": result.model,
                "llm.attempts": len(result.calls),
                "llm.success": result.data is not None,
            })
        _save_llm_calls(db, incident.id, result.calls)
        return result.data, result.model, result.error
    except Exception as e:   # a bug here must still leave the engineer a draft
        db.rollback()
        logger.exception("postmortem generation crashed for incident %s", incident.id)
        return None, None, f"postmortem generation crashed: {type(e).__name__}"


def _notify_postmortem(db, incident, pm) -> None:
    """Send to Slack/email and store what happened. Never raises."""
    result = slack.notify_postmortem(incident, pm)
    try:
        # NEW dict so SQLAlchemy notices the JSONB change
        incident.context = {**(incident.context or {}), "notify_postmortem": result}
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not store postmortem notify result for incident %s", incident.id)


@celery_app.task(bind=True, name="generate_postmortem", max_retries=4)
def generate_postmortem(self, incident_id: int):
    """
    Flow: only RESOLVED incidents -> skip if a draft exists (idempotent) -> wait while
    triage is still PENDING (re-queues itself every POSTMORTEM_WAIT_SECONDS, 4 times) ->
    ask the LLM (validated JSON) -> or build the no-AI fallback -> save as DRAFT -> notify.
    """
    db = SessionLocal()
    try:
        incident = db.get(models.Incident, incident_id)
        if not incident:
            logger.warning("incident %s not found", incident_id)
            return
        if incident.status != "RESOLVED":
            logger.info("incident %s is not resolved, no postmortem", incident_id)
            metrics.POSTMORTEMS.labels("skipped").inc()
            return
        if db.scalar(select(models.Postmortem.id).where(models.Postmortem.incident_id == incident_id)):
            logger.info("incident %s already has a postmortem, skipping", incident_id)
            metrics.POSTMORTEMS.labels("skipped").inc()
            return
        
        # Resolved before triage finished? Wait a little: the AI analysis makes a better draft.
        if incident.ai_status == "PENDING" and self.request.retries < self.max_retries:
            raise self.retry(countdown=settings.POSTMORTEM_WAIT_SECONDS)

        alerts = db.scalars(select(models.Alert)
                             .where(models.Alert.incident_id == incident_id)
                             .order_by(models.Alert.id)).all()
        data, model, error = _llm_postmortem(db, incident, alerts)
        if data:
            fields = {
                "summary": data.summary, 
                "impact": data.impact, 
                "root_cause": data.root_cause,
                "timeline": [e.model_dump() for e in data.timeline],
                "action_items": data.action_items,
            }
            model_used, outcome = model, "generated"
        else:
            logger.warning("incident %s posmortem fallback: %s", incident_id, error)
            fields = _fallback_postmortem(incident)
            model_used, outcome = "fallback (no AI)", "fallback"

        pm = models.Postmortem(incident_id=incident.id, status="DRAFT", model_used=model_used, **fields)
        db.add(pm)
        try:
            db.commit()
        except IntegrityError:   # incident_id is UNIQUE: another worker was faster
            db.rollback()
            logger.info("postmortem for incident %s was created concurrently", incident_id)
            metrics.POSTMORTEMS.labels("skipped").inc()
            return

        metrics.POSTMORTEMS.labels(outcome).inc()
        logger.info("incident %s postmortem draft saved (%s)", incident_id, outcome)
        _notify_postmortem(db, incident, pm)

    except Retry:      # Celery's "run me again later" signal: must pass through untouched
        raise
    except Exception:
        db.rollback()
        logger.exception("postmortem failed for incident %s", incident_id)
        metrics.POSTMORTEMS.labels("failed").inc()
        raise
    finally:
        db.close()