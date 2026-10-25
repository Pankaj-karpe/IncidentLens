import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from ._util import as_utc, iso_z, safe_label

logger = logging.getLogger(__name__)

# Uses a case-insensitive regular expression to match common error keywords
ERROR_PATTERN = r"(?i)(error|exception|traceback|critical|fatal|timeout|refused|failed)"

def collect(service: str, started_at: datetime) -> dict:
    # Sanitize service name and calculate metric time window
    svc = safe_label(service)
    now = datetime.now(timezone.utc)
    started = min(as_utc(started_at), now)      # a future timestamp can't break the window
    lookback = timedelta(minutes=settings.LOOKBACK_MINUTES)
    start = started - lookback
    end = min(now, started + lookback)

    #Initializes a dictionary template to store the log lines, 
    # total match count, window range, and error status.
    result = {
        "available": False,
        "window": {"start": iso_z(start), "end": iso_z(end)},
        "lines": [],
        "total_matched": 0,
        "error": None,
    }
    if not svc:
        result["error"] = "no service label on alert"
        return result

    #This tells Loki: "Give me log lines for this service that contain any error-related keywords."
    label = safe_label(settings.LOKI_SERVICE_LABEL) or "app"
    query = f'{{{label}="{svc}"}} |~ "{ERROR_PATTERN}"'

    try:
        with httpx.Client(base_url=settings.LOKI_URL,
                          timeout=settings.COLLECTOR_TIMEOUT_SECONDS) as client:
            #It sends a GET request to Loki’s range query endpoint
            resp = client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": int(start.timestamp() * 1e9),   # Loki wants nanoseconds
                    "end": int(end.timestamp() * 1e9),
                    "limit": 200,
                    "direction": "backward",
                },
            )
            resp.raise_for_status()
            streams = resp.json()["data"]["result"]
    except httpx.TransportError as e:
        logger.warning("loki unreachable: %s", e)
        result["error"] = f"loki unreachable: {type(e).__name__}"
        return result
    except httpx.HTTPStatusError as e:
        logger.warning("loki query failed: %s", e)
        result["error"] = f"loki http {e.response.status_code}"
        return result

    entries = []
    #Loki returns data grouped into streams. The code extracts all timestamp-and-line 
    # pairs and sorts them chronologically (oldest first) so they read like a timeline.
    for stream in streams:
        for ts_ns, line in stream["values"]:
            entries.append((int(ts_ns), line))
    entries.sort()  # oldest first reads like a timeline
    result["total_matched"] = len(entries)

    # collapse consecutive duplicate lines (stack traces repeat), then cap size
    lines, prev = [], None
    for ts_ns, line in entries:
        trimmed = line.strip()[: settings.MAX_LOG_LINE_CHARS]
        if trimmed == prev:
            continue
        prev = trimmed
        lines.append({
            "time": datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat(),
            "line": trimmed,
        })

    # keep the most recent N so the cap doesn't drop the freshest errors
    result["lines"] = lines[-settings.MAX_LOG_LINES:]
    result["available"] = bool(result["lines"])

    return result