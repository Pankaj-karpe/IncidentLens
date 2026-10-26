# When an alert fires, this module checks if any code changes
# were pushed to your repository shortly before the incident, 
# helping the LLM determine if a recent deployment caused the bug.
import logging
from datetime import datetime, timedelta

import httpx

from app.config import settings

from ._util import as_utc, iso_z

logger = logging.getLogger(__name__)

def collect(started_at: datetime) -> dict:
    #initializes a dictionary template
    result = {"available": False, "deploys": [], "error": None}
    repo = settings.GITHUB_DEPLOY_REPO
    if not repo:
        result["error"] = "GITHUB_DEPLOY_REPO not configured"
        return result

    # It looks backward by a set number of hours (DEPLOY_LOOKBACK_HOURS) 
    # to catch recent commits, and extends forward by 5 minutes
    started = as_utc(started_at)
    since = started - timedelta(hours=settings.DEPLOY_LOOKBACK_HOURS)
    until = started + timedelta(minutes=5)

    headers = {"Accept": "application/vnd.github+json"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/commits",
            params={"since": iso_z(since), "until": iso_z(until), "per_page": 10},
            headers=headers,
            timeout=settings.COLLECTOR_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except httpx.TransportError as e:  
        logger.warning("github unreachable: %s", e)
        result["error"] = f"github unreachable: {type(e).__name__}"
        return result
    except httpx.HTTPStatusError as e:
        result["error"] = f"github http {e.response.status_code}"
        return result
    
    for c in resp.json():
        info = c["commit"]
        committed = datetime.fromisoformat(info["author"]["date"].replace("Z", "+00:00"))

            # For each commit returned by GitHub, it extracts:
        result["deploys"].append({
            "sha": c["sha"][:7],
            "message": info["message"].splitlines()[0][:200],
            "author": info["author"]["name"],
            "time": committed.isoformat(),
            "minutes_before_alert": round((started - committed).total_seconds() / 60, 1),
        })
    result["available"] = bool(result["deploys"])

    return result