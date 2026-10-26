import logging
import math
from datetime import UTC, datetime, timedelta

import httpx

from ..config import settings
from ._util import as_utc, iso_z, safe_label

logger = logging.getLogger(__name__)

# {svc} is injected only after safe_label() sanitising.
QUERIES = {
    # Calculates the total request rate (requests per second) for the service over a rolling 5-minute window.
    "request_rate_rps": 'sum(rate(http_requests_total{{job="{svc}"}}[5m]))',

    # Measures the rate of server errors (HTTP 5xx status codes) per second over the last 5 minutes
    "error_rate_5xx_rps": 'sum(rate(http_requests_total{{job="{svc}", status=~"5.."}}[5m]))',

    # Calculates the 95th percentile latency (the response time below which 95% of requests fall)
    # using Prometheus histogram buckets over a 5-minute window
    "p95_latency_seconds": (
        'histogram_quantile(0.95, sum by (le) '
        '(rate(http_request_duration_seconds_bucket{{job="{svc}"}}[5m])))'    
    ),

    # Tracks service uptime availability. targets_up counts how many instances are currently healthy and responding, 
    # while targets_total counts how many instances are configured
    "targets_up": 'sum(up{{job="{svc}"}})',
    "targets_total": 'count(up{{job="{svc}"}})',

    # Counts the total number of container restarts across all pods matching 
    # the service name over the last 30 minutes (useful for spotting crash loops)
    "pod_restarts_30m": (
        'sum(increase(kube_pod_container_status_restarts_total{{pod=~"{svc}.*"}}[30m]))'
    ),
}

def _summarize(values: list) -> dict | None:
    """Reduce a time series to a few numbers so the LLM prompt stays small."""
    nums = []
    for _, raw in values:  # ignoring the timestamp _ and looking at the value raw)
        try:
            # converts values to floats, skipping any invalid data types, strings, 
            # NaN (Not a Number), or Inf (Infinity) values that would break calculations
            f = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        nums.append(f)
    if not nums:
        return None
    return{
        "first": round(nums[0], 4), 
        "last": round(nums[-1], 4),
        "min": round(min(nums), 4),
        "max": round(max(nums), 4),
        "avg": round(sum(nums) / len(nums), 4),
    }

def collect(service: str, started_at: datetime) -> dict:
    # Sanitize service name and calculate metric time window
    svc = safe_label(service)
    now = datetime.now(UTC)
    started = min(as_utc(started_at), now)      # a future timestamp can't break the window
    lookback = timedelta(minutes=settings.LOOKBACK_MINUTES)
    start = started - lookback
    end = min(now, started + lookback)

    # Initialize default response structure
    result = {
        "available": False,
        "window": {"start": iso_z(start), "end": iso_z(end)},
        "metrics": {},
        "error": None,
    }

    # Ensure service label exists before attempting queries
    if not svc:
        result["error"] = "no service label on alert"
        return result

    try:
        # Query Prometheus range API for each metric and summarize results
        with httpx.Client(base_url=settings.PROMETHEUS_URL,
                          timeout=settings.COLLECTOR_TIMEOUT_SECONDS) as client:
            for name, template in QUERIES.items():
                try:
                    resp = client.get(
                        "/api/v1/query_range",
                        params={
                            "query": template.format(svc=svc),
                            "start": start.timestamp(),
                            "end": end.timestamp(),
                            "step": "30s",
                        },
                    )
                    resp.raise_for_status()
                    series = resp.json()["data"]["result"]
                    result["metrics"][name] = _summarize(series[0]["values"]) if series else None
                except httpx.HTTPStatusError as e:
                    logger.warning("prometheus query %s failed: %s", name, e)
                    result["metrics"][name] = None
        result["available"] = any(v is not None for v in result["metrics"].values())
    
    # Gracefully handle Prometheus connection errors without crashing triage
    except httpx.TransportError as e:
        logger.warning("prometheus unreachable: %s", e)
        result["error"] = f"prometheus unreachable: {type(e).__name__}"
        
    return result