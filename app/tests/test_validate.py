import json

from app.ai import validate
from app.ai.client import LLMError, LLMResponse
from app.config import settings
from app.schema import TriageOutput

GOOD = json.dumps({"summary": "s", "likely_cause": "c", "confidence": "medium",
                   "evidence": ["e"], "next_steps": ["n"]})


def fake_complete(replies):
    """Each call returns the next item of `replies` (text, or an exception to raise)."""
    it = iter(replies)

    def _complete(system, messages, max_tokens=None):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, model="m", input_tokens=10, output_tokens=5, latency_ms=1)
    return _complete


def run(monkeypatch, replies):
    monkeypatch.setattr(validate, "complete", fake_complete(replies))
    monkeypatch.setattr(settings, "LLM_VALIDATION_RETRIES", 1)
    return validate.run_structured(TriageOutput, "sys", "user", purpose="triage")


def test_extract_json_handles_fences_chatter_and_nesting():
    text = 'Sure! Here you go:\n```json\n{"a": {"b": 1}}\n```\nHope that helps.'
    assert validate._extract_json(text) == {"a": {"b": 1}}


def test_valid_reply_on_first_try(monkeypatch):
    res = run(monkeypatch, [GOOD])
    assert res.data is not None and res.error is None
    assert len(res.calls) == 1 and res.calls[0].success


def test_bad_reply_is_repaired_on_retry(monkeypatch):
    res = run(monkeypatch, ["this is not json", GOOD])
    assert res.data is not None
    assert [c.success for c in res.calls] == [False, True]    # the failed attempt is still recorded


def test_gives_up_after_retries(monkeypatch):
    res = run(monkeypatch, ["nope", "still nope"])
    assert res.data is None and res.error.startswith("invalid output")
    assert len(res.calls) == 2


def test_llm_error_is_not_retried(monkeypatch):
    res = run(monkeypatch, [LLMError("down")])
    assert res.data is None and res.error == "down"
    assert len(res.calls) == 1 and not res.calls[0].success