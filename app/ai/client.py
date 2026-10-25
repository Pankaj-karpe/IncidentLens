"""
Thin LLM wrapper: ONE function, complete(), that hides the server details.
Both targets speak the OpenAI-compatible chat API:
  primary   = Gemini free tier   (gemini-2.5-flash)
  fallback  = local Ollama       (llama3.1:8b)
It adds what a raw HTTP call lacks:
  - timeout         (a hung API must not hang the Celery worker)
  - retry + backoff (only for errors that can succeed later: network, 429, 5xx)
  - failover        (primary first, then the local fallback if the primary fails)
  - usage numbers   (tokens, latency, cost) for the llm_calls table
It never decides what to do when everything fails; it raises LLMError and the
caller (tasks.py) degrades gracefully.
"""
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when the LLM could not produce a reply (after retries and failover)."""


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float = 0.0


@dataclass(frozen=True)
class Target:
    """Everything needed to call ONE model on ONE server."""
    base_url: str
    api_key: str | None      # Gemini needs one; Ollama does not
    model: str
    timeout: float           # local CPU models are slow, so the fallback gets a longer one
    is_primary: bool         # only the primary has a price (the fallback is local/free)


def _targets() -> list[Target]:
    """Ordered list of places to try: primary, then fallback (each only if configured)."""
    out: list[Target] = []
    if settings.LLM_MODEL and settings.LLM_BASE_URL and settings.LLM_API_KEY:
        out.append(Target(settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
                          settings.LLM_TIMEOUT_SECONDS, True))
    if settings.LLM_FALLBACK_MODEL and settings.LLM_FALLBACK_BASE_URL:
        out.append(Target(settings.LLM_FALLBACK_BASE_URL, settings.LLM_FALLBACK_API_KEY,
                          settings.LLM_FALLBACK_MODEL,
                          settings.LLM_FALLBACK_TIMEOUT_SECONDS, False))
    return out


def is_configured() -> bool:
    """Can we even try? True if at least one target is usable."""
    return bool(_targets())


def _chat(t: Target, system: str, messages: list[dict], max_tokens: int):
    """
    One HTTP call to POST {base_url}/chat/completions.
    Returns (text, input_tokens, output_tokens) and lets httpx raise on HTTP/network errors.
    """
    headers = {"content-type": "application/json"}
    if t.api_key:
        headers["Authorization"] = f"Bearer {t.api_key}"
    body = {
        "model": t.model,
        "max_tokens": max_tokens,
        # In this protocol the system prompt is simply the first chat message.
        "messages": [{"role": "system", "content": system}, *messages],
        "response_format": {"type": "json_object"},   # ask the server for JSON mode
    }
    if settings.LLM_TEMPERATURE is not None:
        body["temperature"] = settings.LLM_TEMPERATURE
    resp = httpx.post(f"{t.base_url.rstrip('/')}/chat/completions",
                      headers=headers, json=body, timeout=t.timeout)
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage") or {}
    return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def _attempt(t: Target, system: str, messages: list[dict], max_tokens: int):
    """Call one target with retries. Transient failure -> retry; permanent -> raise now."""
    last_err = "unknown"
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            return _chat(t, system, messages, max_tokens)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            # 4xx (bad key, bad request, unknown model) will fail again: don't retry.
            # 408/409/429 and 5xx are temporary: do retry.
            if code not in (408, 409, 429) and code < 500:
                raise LLMError(f"{t.model} http {code}: {e.response.text[:200]}") from e
            last_err = f"http {code}"
        except httpx.TransportError as e:   # timeouts, DNS, connection refused
            last_err = type(e).__name__
        except (KeyError, IndexError, TypeError, ValueError) as e:
            # 200 OK but the body isn't the shape we expect: retrying won't fix it
            raise LLMError(f"{t.model} unexpected response shape: {type(e).__name__}") from e

        if attempt < settings.LLM_MAX_RETRIES:
            time.sleep(min(2 ** attempt, 8))   # exponential backoff: 1s, 2s, 4s (cap 8s)
    raise LLMError(f"{t.model} failed after retries: {last_err}")


def complete(system: str, messages: list[dict], max_tokens: int | None = None) -> LLMResponse:
    """Try the primary target (Gemini), then the fallback target (Ollama)."""
    targets = _targets()
    if not targets:
        raise LLMError("LLM is not configured (set LLM_API_KEY + LLM_BASE_URL, or LLM_FALLBACK_*)")

    max_tokens = max_tokens or settings.LLM_MAX_OUTPUT_TOKENS
    errors: list[str] = []
    for t in targets:
        started = time.perf_counter()
        try:
            text, tin, tout = _attempt(t, system, messages, max_tokens)
        except LLMError as e:
            logger.warning("LLM target %s failed: %s", t.model, e)
            errors.append(str(e))
            continue   # fail over to the next target

        # Prices are per MILLION tokens. Only the primary has a price; local = free.
        cost = 0.0
        if t.is_primary:
            cost = round((tin * settings.LLM_PRICE_INPUT_PER_MTOK
                          + tout * settings.LLM_PRICE_OUTPUT_PER_MTOK) / 1_000_000, 6)
        else:
            logger.warning("LLM answered from FALLBACK target %s", t.model)
        return LLMResponse(
            text=text, model=t.model, input_tokens=tin, output_tokens=tout,
            latency_ms=int((time.perf_counter() - started) * 1000), cost_usd=cost,
        )
    raise LLMError("; ".join(errors))