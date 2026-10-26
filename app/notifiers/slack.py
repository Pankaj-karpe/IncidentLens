"""
Notifier: sends the triage result to Slack (incoming webhook) and/or email (SMTP).
Both are optional: a channel with no settings is simply skipped.

Design rules:
  1. Best-effort. notify_triage() NEVER raises: failing to notify must not fail triage.
     It returns {"slack": "...", "email": "..."} which tasks.py stores in incident.context.
  2. Everything that leaves the system goes through redact_text() first.
  3. The message says "AI suggestion": the engineer decides (human in the loop).
  4. Slack uses stdlib urllib ON PURPOSE. httpx logs the full request URL, and the
     OpenTelemetry httpx instrumentation stores it in traces. A Slack webhook URL is a
     secret, so we keep this one call out of both.
"""

import html
import json
import logging
import re
import smtplib
import ssl
import time
import urllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC
from email.message import EmailMessage

from app.ai.redact import redact_text

from .. import models
from ..config import settings
from ..observability import metrics, tracing

logger = logging.getLogger(__name__)

_EMOJI = {"critical": "🔴", "warning": "🟠", "info": "🔵"}

class NotifyError(Exception):
    """One channel failed. Caught in notify_triage(); never leaves this module."""


@dataclass
class Message:
    """Channel-neutral message. Built once, then rendered for Slack and for email."""
    title: str              # headline (with emoji, used by Slack)
    subject: str            # email subject (no emoji, one line)
    fields: list[tuple[str, str]]   # short facts: Service, Severity, ...
    sections: list[tuple[str, str]] = field(default_factory=list)   # (heading, text)
    footer: str = ""
    link: str | None = None


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------
def _s(value) -> str:
    """To string + redact secrets. Used on EVERYTHING that goes into a message."""
    return redact_text(str(value)) if value is not None else ""

def _one_line(text: str) -> str:
    """Collapse all whitespace/newlines. Mandatory for email subjects: a newline in a
    header would be header injection, and alert labels come from outside."""
    return re.sub(r"\s+", " ", text).strip()

def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"

def _slack_escape(text: str) -> str:
    """Slack treats & < > as special in its markup. '&' must be replaced first."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Build the message from the incident
# ---------------------------------------------------------------------------
def _build_message(incident: models.Incident, annotations: dict) -> Message:
    ai = (incident.context or {}).get("ai") or {}
    sev = (incident.severity or "unknown").lower()
    service = incident.service or "unknown service"
    started = (
        incident.started_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        if incident.started_at else "unknown"
    )
    title = _one_line(_s(f"{_EMOJI.get(sev, '⚪')} [{sev.upper()}] {incident.title} on {service}"))
    subject = _trim(_one_line(_s(f"[{sev.upper()}] {incident.title} on {service} (incident #{incident.id})")), 200)

    fields = [
        ("Incident", f"#{incident.id}"),
        ("Service", service),
        ("Severity", sev),
        ("Started", started),
        ("AI status", incident.ai_status),
        ("Confidence", ai.get("confidence") or "n/a"),
    ]
    fields = [(k, _one_line(_s(v))) for k, v in fields]


    sections: list[tuple[str, str]] = []
    if incident.ai_summary:       # for ai_status=FALLBACK this holds the plain raw-facts text
        sections.append(("Summary", _trim(_s(incident.ai_summary), 2500)))
    if incident.ai_root_cause:
        sections.append(("Likely cause", _trim(_s(incident.ai_root_cause), 2500)))
    if isinstance(ai.get("evidence"), list) and ai["evidence"]:
        bullets = "\n".join(f"• {_s(e)}" for e in ai["evidence"][:5])
        sections.append(("Evidence", _trim(bullets, 2500)))
    if isinstance(incident.ai_next_steps, list) and incident.ai_next_steps:
        steps = "\n".join(f"{i}. {_s(step)}" for i, step in enumerate(incident.ai_next_steps, 1))
        sections.append(("Suggested next steps", _trim(steps, 2500)))
    if annotations.get("description"):
        sections.append(("Alert description", _trim(_s(annotations["description"]), 1000)))

     # footer is defined at THIS level so it always exists    
    footer = f"AI suggestion only: a human decides what to do. Model: {ai.get('model') or 'n/a'}."
    if ai.get("cached_from"):
        footer += f" Reused the analysis of incident #{ai['cached_from']}."
    link = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/incidents/{incident.id}" if settings.PUBLIC_BASE_URL else None
    return Message(title, subject, fields, sections, footer, link)        


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------
def _slack_payload(msg: Message) -> dict:
    """Slack 'Block Kit' JSON. Limits: header 150 chars, a text block 3000, a field 2000."""
    # builds the visual layout for a Slack message using Slack's block kit UI framework.
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": msg.title[:150], "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*{k}*\n{_slack_escape(v)}"[:2000]} for k, v in msg.fields
        ]},
    ]

    for heading, text in msg.sections:
        blocks.append({"type": "section", "text": {
            "type": "mrkdwn", "text": f"*{heading}*\n{_slack_escape(text)}"[:2900]}})

    # AFTER the loop: the footer is added exactly once
    footer = _slack_escape(msg.footer)
    if msg.link:
        footer += f"  |  <{msg.link}|Open incident>"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer[:2900]}]})
    # "text" is the fallback shown in phone notifications and screen readers
    return {"text": msg.title, "blocks": blocks}


def _post_slack(payload: dict) -> None:
    """
    POST to the webhook. Retries only what can succeed later (network error, 429, 5xx).
    A 4xx (revoked webhook, bad payload) is permanent: stop at once.
    """
    # grabs the URL, turns the payload into UTF-8 JSON bytes, sets the JSON header, and fires a POST request.
    request = urllib.request.Request(
        settings.SLACK_WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    last = "unknown"
    for attempt in range(settings.NOTIFY_MAX_RETRIES + 1):
        delay = 2 ** attempt                                   # 1s, 2s, 4s
        try:
            with urllib.request.urlopen(request, timeout=settings.NOTIFY_TIMEOUT_SECONDS):
                return                                         # 2xx = delivered
        except urllib.error.HTTPError as e:                    # server answered with an error code
            if e.code == 429 or e.code >= 500:
                last = f"http {e.code}"
                try:
                    delay = min(int(e.headers.get("Retry-After", delay)), 10)
                except ValueError:
                    pass
            else:
                body = e.read().decode("utf-8", errors="replace")[:100]
                raise NotifyError(f"slack http {e.code}: {body}") from e
        except ValueError as e:                                # urlopen: URL is not a valid URL
            raise NotifyError("slack webhook URL is invalid") from e
        except OSError as e:                                   # timeout, DNS, connection refused
            last = type(e).__name__
        if attempt < settings.NOTIFY_MAX_RETRIES:
            time.sleep(delay)
    raise NotifyError(f"slack failed after retries: {last}")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
#checks if all the required email settings are provided, returning True if email alerts are ready to go or False if they should be skipped.
def _email_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.EMAIL_FROM and settings.EMAIL_TO)

#it takes the structured Message object flattens all of its parts into a clean, plain-text email body.
def _to_text(msg: Message) -> str:
    lines = [msg.title, ""]
    lines += [f"{k}: {v}" for k, v in msg.fields]
    for heading, text in msg.sections:
        lines += ["", heading.upper(), text]
    lines += ["", msg.footer]
    if msg.link:
        lines.append(f"Open: {msg.link}")
    return "\n".join(lines)

def _to_html(msg: Message) -> str:
    esc = html.escape                                          # never put raw text in HTML

    def br(text: str) -> str:
        return esc(text).replace("\n", "<br>")

    rows = "".join(f"<tr><td><b>{esc(k)}</b>&nbsp;</td><td>{esc(v)}</td></tr>" for k, v in msg.fields)
    secs = "".join(f"<h4>{esc(h)}</h4><p>{br(t)}</p>" for h, t in msg.sections)
    link = f'<p><a href="{esc(msg.link)}">Open incident</a></p>' if msg.link else ""
    return (f"<h3>{esc(msg.title)}</h3><table>{rows}</table>{secs}"
            f"<p><i>{esc(msg.footer)}</i></p>{link}")

def _send_email(msg: Message) -> None:
    """One attempt with a timeout (email is the secondary channel; Slack gets the retries)."""
    recipients = [a.strip() for a in (settings.EMAIL_TO or "").split(",") if a.strip()]
    mail = EmailMessage()
    mail["Subject"] = msg.subject
    mail["From"] = settings.EMAIL_FROM
    mail["To"] = ", ".join(recipients)
    mail.set_content(_to_text(msg))                            # plain-text version
    mail.add_alternative(_to_html(msg), subtype="html")        # HTML version (clients pick one)

    tls = ssl.create_default_context()
    try:
        if settings.SMTP_SECURITY == "ssl":                    # usually port 465
            server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                      timeout=settings.NOTIFY_TIMEOUT_SECONDS, context=tls)
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                                  timeout=settings.NOTIFY_TIMEOUT_SECONDS)
        with server:                                           # closes the connection for us
            if settings.SMTP_SECURITY == "starttls":           # usually port 587
                server.starttls(context=tls)
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(mail)
    except (smtplib.SMTPException, OSError) as e:
        raise NotifyError(f"email failed: {type(e).__name__}: {str(e)[:100]}") from e


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _dispatch(msg: Message, incident_id: int) -> dict:
    """Send ONE Message to every configured channel. Never raises.
    Returns e.g. {"slack": "sent", "email": "skipped (not configured)"}."""
    channels = [
        ("slack", bool(settings.SLACK_WEBHOOK_URL), lambda: _post_slack(_slack_payload(msg))),
        ("email", _email_configured(), lambda: _send_email(msg)),
    ]
    results: dict[str, str] = {}
    for name, enabled, send in channels:
        if not enabled:
            results[name] = "skipped (not configured)"
            continue
        try:
            # a tracing span (visible in Jaeger) + a Prometheus counter around each send
            with tracing.span(f"notify.{name}", {"incident.id": incident_id}):
                send()
            results[name] = "sent"
            metrics.NOTIFICATIONS.labels(name, "sent").inc()
        except NotifyError as e:
            results[name] = f"failed: {e}"
            metrics.NOTIFICATIONS.labels(name, "failed").inc()
            logger.warning("%s notification failed for incident %s: %s", name, incident_id, e)
        except Exception as e:  # a bug here must not kill the task either
            results[name] = f"failed: {type(e).__name__}"
            metrics.NOTIFICATIONS.labels(name, "failed").inc()
            logger.exception("%s notification crashed for incident %s", name, incident_id)

    return results

def notify_triage(incident: models.Incident, annotations: dict) -> dict:
    """
    Send the triage result to every configured channel. Never raises.
    Returns e.g. {"slack": "sent", "email": "skipped (not configured)"}.
    """
    # Checks if notifications are globally disabled
    if not settings.NOTIFY_ENABLED:
        return {"slack": "disabled", "email": "disabled"}

    #Generates the universal Message object building the message fails due to bad dat it handels it safely
    try:
        msg = _build_message(incident, annotations)
    except Exception:
        logger.exception("could not build notification for incident %s", incident.id)
        return {"error": "could not build message"}

    return _dispatch(msg, incident.id)


def _build_postmortem_message(incident: models.Incident, pm: models.Postmortem) -> Message:
    """Postmortem draft -> converts postmortem data into a generic Message object that isn't tied to any specific platform (like Slack or Email)."""

    # 1. Extract base metadata and calculate downtime duration
    sev = (incident.severity or "unknown").lower()
    service = incident.service or "unknown service"
    duration = "unknown"
    if incident.started_at and incident.resolved_at:
        duration = f"{int((incident.resolved_at - incident.started_at).total_seconds() // 60)} min"

    # 2. Build platform-specific chat title and email subject
    title = _one_line(_s(f"📝 Postmortem draft: {incident.title} on {service}"))
    subject = _trim(_one_line(_s(
        f"[POSTMORTEM DRAFT] {incident.title} on {service} (incident #{incident.id})")), 200)
    
    # 3. Compile core key-value metadata fields
    fields = [
        ("Incident", f"#{incident.id}"), 
        ("Service", service), 
        ("Severity", sev),
        ("Duration", duration), 
        ("Status", pm.status), 
        ("Written by", pm.model_used or "n/a"),
    ]
    fields = [(k, _one_line(_s(v))) for k, v in fields]

    # 4. Add core text sections (Summary, Impact, Root cause) with character limits
    sections: list[tuple[str, str]] = []
    for heading, value in (("Summary", pm.summary), ("Impact", pm.impact), ("Root cause", pm.root_cause)):
        if value:
            sections.append((heading, _trim(_s(value), 2500)))
    
    # 5. Parse JSONB timeline into a safe bulleted list (max 12 events)
    if isinstance(pm.timeline, list) and pm.timeline:
        lines = [f"• {_s(e.get('time'))}: {_s(e.get('event'))}"
                 for e in pm.timeline[:12] if isinstance(e, dict)]

        if lines:
            sections.append(("Timeline", _trim("/n". join(lines), 2500)))
    
    # 6. Format action items into an ordered numbered list
    if isinstance(pm.action_items, list) and pm.action_items:
        items = "/n".join(f"{i}. {_s(x)}" for i, x in enumerate(pm.action_items, 1))
        sections.append(("Action items", _trim(items, 2500)))

    # 7. Append human-review footer warning and UI web link
    footer = ("AI-written DRAFT: a human must review and edit it before it is shared. "
              f"Sign off with POST /incidents/{incident.id}/postmortem/review.")
    link = (f"{settings.PUBLIC_BASE_URL.rstrip('/')}/incidents/{incident.id}/postmortem"
            if settings.PUBLIC_BASE_URL else None)
    
    # 8. Return unified platform-agnostic Message object
    return Message(title, subject, fields, sections, footer, link)


def notify_postmortem(incident: models.Incident, pm: models.Postmortem) -> dict:
    """Send the postmortem draft. Never raises."""
    if not settings.NOTIFY_ENABLED:
        return {"slack": "disabled", "email": "disabled"}
    try:
        msg = _build_postmortem_message(incident, pm)
    except Exception:
        logger.exception("could not build postmortem notification for incident %s", incident.id)
        return {"error": "could not build message"}
    return _dispatch(msg, incident.id)     