"""
Prometheus metrics Answers: how slow is the LLM, how many tokens/dollars do we burn, how often does it
fail, how often do we fall back, did the notification go out?

Naming: incidentlens_<what>_<unit>. Labels stay small and fixed (purpose, model, status).
Never put incident ids or free text in a label: every new value creates a new time series.

IMPORTANT: the API and the Celery worker are SEPARATE processes with SEPARATE counters.
  - API    -> GET /metrics on the FastAPI port (router below)
  - worker -> its own HTTP server on METRICS_WORKER_PORT (start_worker_metrics_server)
Prometheus must scrape both.
"""
import logging

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
    start_http_server,
)

from app.config import settings

logger = logging.getLogger(__name__)

# Histogram "buckets" are the latency ranges we count into (seconds).
# They let Grafana compute p50/p95 later with histogram_quantile().
_LLM_BUCKETS = (0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)
_TRIAGE_BUCKETS = (1, 2, 5, 10, 20, 30, 60, 120, 300)

"""
This below variables : tells your app: "Only track these specific numbers 
(like token counts, costs, and latencies), format them neatly, and expose them at 
/metrics so Prometheus can collect them cleanly for your Grafana dashboards without 
all the extra noise.
"""
# ---------------- worker side: the AI pipeline ----------------
LLM_REQUESTS = Counter(
    "incidentlens_llm_requests_total",
    "LLM / embedding API calls (every attempt, including failed ones)",
    ["purpose", "model", "status"],          # purpose: triage|embedding, status: success|failure
)
LLM_LATENCY = Histogram(
    "incidentlens_llm_request_duration_seconds",
    "Latency of one LLM / embedding API call",
    ["purpose", "model"],
    buckets=_LLM_BUCKETS,
)
LLM_TOKENS = Counter(
    "incidentlens_llm_tokens_total",
    "Tokens used",
    ["purpose", "model", "direction"],       # direction: input|output
)
LLM_COST = Counter(
    "incidentlens_llm_cost_usd_total",
    "Money spent on LLM calls in USD (0 on free tiers)",
    ["purpose", "model"],
)
CACHE_HITS = Counter(
    "incidentlens_triage_cache_hits_total",
    "Triages answered from an earlier incident with the same fingerprint (no LLM call)",
)
INCIDENTS_TRIAGED = Counter(
    "incidentlens_incidents_triaged_total",
    "Finished triage runs by outcome",
    ["ai_status"],                           # OK | FALLBACK | FAILED
)
TRIAGE_DURATION = Histogram(
    "incidentlens_triage_duration_seconds",
    "Whole triage task: collect context + embed + LLM + save (notification excluded)",
    buckets=_TRIAGE_BUCKETS,
)
TRIAGE_OUTCOME = Counter(
    "incidentlens_triage_total", 
    "Triage results by AI status", 
    ["ai_status"]
)
COLLECTOR_RESULTS = Counter(
    "incidentlens_collector_results_total",
    "Did each evidence source return data?",
    ["source", "available"],                 # source: metrics|logs|deploys
)
NOTIFICATIONS = Counter(
    "incidentlens_notifications_total",
    "Notification attempts",
    ["channel", "status"],                   # channel: slack|email, status: sent|failed
)

POSTMORTEMS = Counter(
    "incidentlens_postmortems_total",
    "Postmortem draft attempts by outcome",
    ["outcome"],                             # generated | fallback | skipped | failed
)

# ---------------- API side ----------------
ALERTS_RECEIVED = Counter(
    "incidentlens_alerts_received_total",
    "Alerts handled by POST /alerts",
    ["result"],                              # created | duplicate | resolved
)

AUTH_EVENTS = Counter(
    "incidentlens_auth_events_total",
    "Authentication / authorization outcomes",
    ["event"],       # login_success | login_failure | forbidden | webhook_rejected
)


def record_llm_call(purpose: str, model: str, input_tokens: int, output_tokens: int,
                    cost_usd: float, latency_ms: int, success: bool) -> None:
    """
    Whenever an AI call finishes, we take the exact metrics (tokens, cost, latency) 
    saved in PostgreSQL and immediately pass them to Prometheus too, ensuring both 
    systems match while guaranteeing any metrics error is safely logged so it never 
    breaks the triage flow.
    """
    try:
        # checks if the fields exist and then pass to prometheus 
        LLM_REQUESTS.labels(purpose, model, "success" if success else "failure").inc()
        if latency_ms and latency_ms > 0:  # skip calls that never really ran
            LLM_LATENCY.labels(purpose, model).observe(latency_ms / 1000)
        if input_tokens:
            LLM_TOKENS.labels(purpose, model, "input").inc(input_tokens)
        if output_tokens:
            LLM_TOKENS.labels(purpose, model, "output").inc(output_tokens)
        if cost_usd:
            LLM_COST.labels(purpose, model).inc(float(cost_usd))
    except Exception:
        logger.warning("could not record llm metrics", exc_info=True)


def record_collector_results(context: dict) -> None:
    """Count, per evidence source, whether it returned data (collect_context output)."""
    try:
        for source in ("metrics", "logs", "deploys"):
            section = (context or {}).get(source) or {}
            COLLECTOR_RESULTS.labels(source, "true" if section.get("available") else "false").inc()
    except Exception:
        logger.warning("could not record collector metrics", exc_info=True)

        

# ---------------- exposing the metrics (for fastapi scraping) ----------------
router = APIRouter()

@router.get("/metrics", include_in_schema=False)
def metrics_endpoints() -> Response:
    """API process: Prometheus scrapes this URL. generate_latest() renders the text format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

_worker_server_started = False

def start_worker_metrics_server() -> None:
    """
    Worker process: it has no web server, so start a tiny one just for /metrics
    (runs in a background thread). Called once from the worker_ready signal.
    """
    global _worker_server_started
    if _worker_server_started: 
        return

    try: 
        start_http_server(settings.METRICS_WORKER_PORT)
        _worker_server_started = True
        logger.info("worker metrics on :%s/metrics", settings.METRICS_WORKER_PORT)
    except OSError as e:
        # port already used (for example a second worker on the same machine)
        logger.warning("worker metrics server not started: %s", e) 