#!/usr/bin/env python
"""
Evaluation harness: "does the AI triage still give the right answer?"
Each case in eval/incidents.json is a FAKE incident with a KNOWN cause. We build the real
triage prompt, ask the real LLM (validated + confidence-capped exactly like production),
then score the answer with simple, explainable checks:
  cause_keyword        the likely cause mentions at least one expected keyword
  confidence           the (capped) confidence is in the expected set
  cause_no_forbidden   the cause does NOT contain a wrong/hallucinated claim
  next_steps_keyword   a next step mentions an expected keyword (optional)
  secrets_not_in_prompt / secrets_not_in_output   redaction really worked
  valid_schema         the model produced valid JSON for TriageOutput

Two rules that keep the score honest:
  1. ONE model per run. The local fallback model is switched off (unless --allow-fallback),
     otherwise a rate-limit would silently swap the model under test halfway through.
  2. A rate limit (HTTP 429) is not an AI failure. We wait and retry; if the quota is really
     used up we STOP, report the rest as "not run", and you continue later with --resume.

Usage (from the repo root):
  python -m eval.run_eval --no-llm              # offline: redaction + prompt checks (uses no quota)
  python -m eval.run_eval                       # full run on the model from .env
  python -m eval.run_eval --resume              # skip cases that already passed with this model
  python -m eval.run_eval --only db-down-03     # one case
  python -m eval.run_eval --model gemini-2.5-flash-lite    # another model (free-tier quota is per model)
Exit code: 0 = all cases ran and the pass rate reached --min-pass (default 100%), 1 = not, 2 = cannot run.
Results are saved as JSON in eval/results/.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # so "import app" works however this file is started

from app.ai import prompts   # noqa: E402
from app.config import settings  # noqa: E402

CASES_FILE = Path(__file__).with_name("incidents.json")
RESULTS_DIR = Path(__file__).with_name("results")


# ---------------------------------------------------------------------------
# Build a fake incident in the exact shape the collectors produce
# ---------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _metric(value):
    """JSON shorthand -> collector shape. null stays null; 5 -> all stats 5; [a,b,c,d,e] -> named."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = [value] * 5
    first, last, low, high, avg = value
    return {"first": first, "last": last, "min": low, "max": high, "avg": avg}


def build_incident(case: dict, now: datetime):
    started = now - timedelta(minutes=5)
    window = {"start": _iso(started - timedelta(minutes=15)), "end": _iso(now)}
    raw = case.get("context") or {}

    m = raw.get("metrics") or {}
    values = {k: _metric(v) for k, v in (m.get("values") or {}).items()}
    metrics_sec = {"available": any(v is not None for v in values.values()), "window": window,
                   "metrics": values, "error": m.get("error")}

    lg = raw.get("logs") or []
    lines, log_error = (([], lg.get("error")) if isinstance(lg, dict) else (lg, None))
    entries = [{"time": _iso(started + timedelta(seconds=20 * i)), "line": s} for i, s in enumerate(lines)]
    logs_sec = {"available": bool(entries), "window": window, "lines": entries,
                "total_matched": len(entries), "error": log_error}

    deploys = [{**d, "time": _iso(started - timedelta(minutes=d["minutes_before_alert"]))}
               for d in (raw.get("deploys") or [])]
    deploys_sec = {"available": bool(deploys), "deploys": deploys, "error": None}

    alert = case["alert"]
    incident = SimpleNamespace(
        title=alert["alertname"], service=alert.get("service"), severity=alert.get("severity"),
        started_at=started, context={"metrics": metrics_sec, "logs": logs_sec, "deploys": deploys_sec},
    )
    return incident, alert.get("annotations") or {}, case.get("similar") or []


# ---------------------------------------------------------------------------
# Checks: each returns (name, passed, detail)
# ---------------------------------------------------------------------------
def prompt_checks(case: dict, user: str) -> list:
    checks = []
    secrets = case["expect"].get("secrets")
    if secrets:
        leaked = [s for s in secrets if s in user]
        checks.append(("secrets_not_in_prompt", not leaked, f"leaked: {leaked}" if leaked else "clean"))
    checks.append(("prompt_within_budget", len(user) <= settings.MAX_PROMPT_CHARS + 200,
                   f"{len(user)} chars (budget {settings.MAX_PROMPT_CHARS})"))
    return checks


def answer_checks(case: dict, data, confidence: str) -> list:
    exp = case["expect"]
    claim = f"{data.summary} {data.likely_cause}".lower()
    everything = " ".join([data.summary, data.likely_cause, *data.evidence, *data.next_steps])
    checks = []
    if "cause_any_of" in exp:
        hit = [k for k in exp["cause_any_of"] if k in claim]
        checks.append(("cause_keyword", bool(hit), f"matched {hit}" if hit else f"none of {exp['cause_any_of']}"))
    if "confidence_in" in exp:
        checks.append(("confidence", confidence in exp["confidence_in"],
                       f"got {confidence}, expected one of {exp['confidence_in']}"))
    if "cause_must_not_mention" in exp:
        bad = [k for k in exp["cause_must_not_mention"] if k in claim]
        checks.append(("cause_no_forbidden", not bad, f"forbidden: {bad}" if bad else "clean"))
    if "next_steps_any_of" in exp:
        steps = " ".join(data.next_steps).lower()
        hit = [k for k in exp["next_steps_any_of"] if k in steps]
        checks.append(("next_steps_keyword", bool(hit), f"matched {hit}" if hit else f"none of {exp['next_steps_any_of']}"))
    if exp.get("secrets"):
        leaked = [s for s in exp["secrets"] if s in everything]
        checks.append(("secrets_not_in_output", not leaked, f"leaked: {leaked}" if leaked else "clean"))
    return checks


# ---------------------------------------------------------------------------
# Rate limits and resuming
# ---------------------------------------------------------------------------
def ask(system: str, user: str, args):
    """
    Call the LLM. On HTTP 429 (free-tier rate limit) wait and try again: a per-minute limit
    clears quickly. Returns (result, still_rate_limited).
    """
    from app.ai.validate import run_structured
    from app.schema import TriageOutput

    result = None
    for attempt in range(args.rate_limit_retries + 1):
        result = run_structured(TriageOutput, system, user, purpose="eval")
        if result.data is not None or "http 429" not in (result.error or ""):
            return result, False
        if attempt < args.rate_limit_retries:
            print(f"        rate limited (429): waiting {args.rate_limit_wait:.0f}s, then retrying")
            time.sleep(args.rate_limit_wait)
    return result, True


def case_hash(case: dict) -> str:
    """Fingerprint of a case's definition: if you edit a case, its old result no longer counts."""
    return hashlib.sha1(json.dumps(case, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def load_reusable(model: str) -> dict:
    """
    Cases whose MOST RECENT run (same model, same definition) passed.
    Files are read oldest first, so a newer failure removes an older pass.
    """
    found: dict = {}
    for path in sorted(RESULTS_DIR.glob("eval-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not data.get("llm"):
            continue
        for rec in data.get("cases", []):
            if rec.get("model") != model or not rec.get("case_hash"):
                continue
            if rec.get("passed"):
                found[rec["id"]] = rec
            else:
                found.pop(rec["id"], None)
    return found


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="IncidentLens triage evaluation")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM: only redaction + prompt checks")
    ap.add_argument("--only", help="run a single case id")
    ap.add_argument("--model", help="model under test for this run (default: LLM_MODEL from .env)")
    ap.add_argument("--allow-fallback", action="store_true",
                    help="let the fallback model answer too (default off: the score measures ONE model)")
    ap.add_argument("--resume", action="store_true", help="reuse cases that already passed with this model")
    ap.add_argument("--sleep", type=float, default=13.0, help="seconds between LLM calls (free-tier per-minute limits)")
    ap.add_argument("--rate-limit-wait", type=float, default=65.0, help="seconds to wait after a 429")
    ap.add_argument("--rate-limit-retries", type=int, default=2, help="429 retries before the run stops")
    ap.add_argument("--min-pass", type=float, default=1.0, help="exit 1 below this pass rate (1.0 = 100%%)")
    args = ap.parse_args()

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            print(f"no case with id {args.only!r}")
            return 2

    run_llm = not args.no_llm
    if args.model:
        settings.LLM_MODEL = args.model
    if not args.allow_fallback:
        settings.LLM_FALLBACK_MODEL = None      # one model per run

    if run_llm:
        from app.ai.client import is_configured
        from app.workers.tasks import (
            _cap_confidence,  # the SAME guardrail production uses
        )
        if not is_configured():
            print("LLM is not configured (set LLM_API_KEY / LLM_BASE_URL in .env), or use --no-llm")
            return 2
        fallback = f"ON ({settings.LLM_FALLBACK_MODEL})" if settings.LLM_FALLBACK_MODEL else "off"
        print(f"model under test: {settings.LLM_MODEL} | fallback: {fallback}\n")
    else:
        print("offline mode: AI answer quality is NOT scored, only redaction and prompt building\n")

    reusable = load_reusable(settings.LLM_MODEL) if (args.resume and run_llm) else {}
    records: list = []
    llm_calls_made = 0
    stopped_by_rate_limit = False

    for case in cases:
        digest = case_hash(case)
        old = reusable.get(case["id"])
        if old and old.get("case_hash") == digest:
            records.append({**old, "reused": True})
            print(f"PASS  {case['id']:<28}  (passed earlier with {old['model']}, reused)")
            continue

        now = datetime.now(UTC)
        incident, annotations, similar = build_incident(case, now)
        system, user = prompts.build_triage_messages(incident, annotations, similar)
        checks = prompt_checks(case, user)
        rec = {"id": case["id"], "case_hash": digest, "reused": False, "latency_ms": 0, "tokens_in": 0,
               "tokens_out": 0, "cost_usd": 0.0, "model": None, "confidence_raw": None,
               "confidence_final": None, "output": None, "error": None}

        if run_llm:
            if llm_calls_made:
                time.sleep(args.sleep)          # stay under the per-minute limit
            llm_calls_made += 1
            result, limited = ask(system, user, args)
            if limited:
                print(f"STOP  {case['id']}: still rate limited after {args.rate_limit_retries} retries "
                      "(the quota is probably used up)")
                stopped_by_rate_limit = True
                break
            rec["latency_ms"] = sum(c.latency_ms for c in result.calls)
            rec["tokens_in"] = sum(c.input_tokens for c in result.calls)
            rec["tokens_out"] = sum(c.output_tokens for c in result.calls)
            rec["cost_usd"] = sum(c.cost_usd for c in result.calls)
            rec["model"] = result.model
            checks.append(("valid_schema", result.data is not None, result.error or "ok"))
            if result.data is not None:
                rec["confidence_raw"] = result.data.confidence
                rec["confidence_final"] = _cap_confidence(result.data.confidence, incident.context)
                rec["output"] = result.data.model_dump()
                checks += answer_checks(case, result.data, rec["confidence_final"])
            rec["error"] = result.error

        passed = all(ok for _, ok, _ in checks)
        rec["passed"] = passed
        rec["checks"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks]
        records.append(rec)

        seconds = f"{rec['latency_ms'] / 1000:.1f}s" if run_llm else "-"
        print(f"{'PASS' if passed else 'FAIL'}  {case['id']:<28} {seconds:>7}  {rec['model'] or ''}")
        for n, ok, d in checks:
            if not ok:
                print(f"        x {n}: {d}")

    total = len(cases)
    n_pass = sum(r["passed"] for r in records)
    n_fail = len(records) - n_pass
    n_not_run = total - len(records)
    rate = n_pass / total

    print("\n== Summary ==")
    print(f"cases: {total} | passed: {n_pass} | failed: {n_fail} | not run: {n_not_run} | pass rate: {rate:.0%}")
    ran = [r for r in records if not r.get("reused")]
    if run_llm and ran:
        avg = sum(r["latency_ms"] for r in ran) / len(ran) / 1000
        used: dict = {}
        for r in ran:
            used[r["model"]] = used.get(r["model"], 0) + 1
        print(f"this run: avg latency {avg:.1f}s | tokens in/out "
              f"{sum(r['tokens_in'] for r in ran)}/{sum(r['tokens_out'] for r in ran)} | "
              f"cost ${sum(r['cost_usd'] for r in ran):.6f} | models used: {used}")
    if stopped_by_rate_limit:
        print("\nThe run stopped because of the rate limit. Wait for the quota to reset (or use "
              "--model with another model), then continue with:  python -m eval.run_eval --resume")

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"pass_rate": rate, "llm": run_llm, "model_under_test": settings.LLM_MODEL,
                               "cases": records}, indent=2), encoding="utf-8")
    print(f"saved: {out.relative_to(ROOT)}")
    return 0 if (rate >= args.min_pass and n_not_run == 0) else 1


if __name__ == "__main__":
    sys.exit(main())