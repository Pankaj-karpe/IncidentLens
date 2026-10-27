from datetime import UTC, datetime
from types import SimpleNamespace

from app.ai import prompts
from app.config import settings


def make_incident(lines, metrics_error=None):
    ctx = {
        "metrics": {"available": False, "error": metrics_error, "metrics": {}, "window": {}},
        "logs": {"available": bool(lines), "error": None, "window": {}, "total_matched": len(lines),
                 "lines": [{"time": "t", "line": s} for s in lines]},
        "deploys": {"available": False, "deploys": [], "error": None},
    }
    return SimpleNamespace(title="HighErrorRate", service="backend", severity="critical",
                           started_at=datetime(2026, 10, 23, 10, 0, tzinfo=UTC), context=ctx)


def test_secrets_never_reach_the_prompt():
    inc = make_incident(["db postgresql://u:Sup3rS3cret@h/db failed", "Authorization: Bearer abcdefghijklmnop12345"])
    _, user = prompts.build_triage_messages(inc, {"summary": "token=abc12345678"}, [])
    for secret in ("Sup3rS3cret", "abcdefghijklmnop12345", "abc12345678"):
        assert secret not in user


def test_data_gaps_are_reported_to_the_model():
    inc = make_incident([], metrics_error="prometheus unreachable: ConnectError")
    _, user = prompts.build_triage_messages(inc, {}, [])
    assert "prometheus unreachable: ConnectError" in user
    assert "nothing found in the time window" in user


def test_prompt_budget_drops_the_oldest_log_lines_first(monkeypatch):
    monkeypatch.setattr(settings, "MAX_PROMPT_CHARS", 3000)
    lines = [f"log-entry-{i:03d} " + "x" * 150 for i in range(100)]
    _, user = prompts.build_triage_messages(make_incident(lines), {}, [])
    assert len(user) <= 3000 + 100
    assert "log-entry-099" in user          # the newest line survives
    assert "log-entry-000" not in user      # the oldest was dropped


def test_system_prompt_contains_the_output_schema():
    system, _ = prompts.build_triage_messages(make_incident([]), {}, [])
    assert "likely_cause" in system and "next_steps" in system