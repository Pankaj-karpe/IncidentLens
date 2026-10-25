from types import SimpleNamespace

import httpx
import pytest

from app.ai import client
from app.config import settings

OK_BODY = {"choices": [{"message": {"content": '{"ok": true}'}}],
           "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, ""

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "http://x")
            raise httpx.HTTPStatusError("err", request=req, response=httpx.Response(self.status_code, request=req))

    def json(self):
        return self._body


@pytest.fixture()
def llm(monkeypatch):
    for name, value in {
        "LLM_BASE_URL": "https://primary.test/v1", "LLM_API_KEY": "k", "LLM_MODEL": "primary-model",
        "LLM_FALLBACK_BASE_URL": "http://fallback.test/v1", "LLM_FALLBACK_MODEL": "fallback-model",
        "LLM_MAX_RETRIES": 1, "LLM_TEMPERATURE": None,
        "LLM_PRICE_INPUT_PER_MTOK": 1.0, "LLM_PRICE_OUTPUT_PER_MTOK": 2.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(client.time, "sleep", lambda s: None)   # no real backoff waiting

    calls, behaviour = [], {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        status = behaviour.get("primary" if "primary.test" in url else "fallback", 200)
        return FakeResp(status, OK_BODY if status == 200 else {})

    monkeypatch.setattr(client.httpx, "post", fake_post)
    return SimpleNamespace(calls=calls, behaviour=behaviour)


def primary_calls(llm):
    return [c for c in llm.calls if "primary.test" in c]


def test_primary_success_and_cost(llm):
    r = client.complete("sys", [{"role": "user", "content": "hi"}])
    assert r.model == "primary-model"
    assert r.cost_usd == pytest.approx(0.0002)          # (100*1 + 50*2) / 1e6
    assert len(llm.calls) == 1


def test_transient_errors_are_retried_then_fail_over(llm):
    llm.behaviour["primary"] = 500
    r = client.complete("sys", [])
    assert r.model == "fallback-model" and r.cost_usd == 0
    assert len(primary_calls(llm)) == settings.LLM_MAX_RETRIES + 1


def test_permanent_errors_are_not_retried(llm):
    llm.behaviour["primary"] = 401                       # bad key: retrying cannot help
    r = client.complete("sys", [])
    assert r.model == "fallback-model"
    assert len(primary_calls(llm)) == 1


def test_all_targets_failing_raises(llm):
    llm.behaviour.update(primary=500, fallback=500)
    with pytest.raises(client.LLMError):
        client.complete("sys", [])


def test_not_configured_raises():
    assert client.is_configured() is False               # conftest removed every key
    with pytest.raises(client.LLMError):
        client.complete("sys", [])