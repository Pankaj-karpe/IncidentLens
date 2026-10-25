"""
Never trust LLM output. This module:
  1. calls the client,
  2. pulls a JSON object out of the reply (models sometimes add ```json fences or chatter),
  3. validates it with a Pydantic class,
  4. on bad output, sends the error back to the model and asks it to fix it (bounded retries).
It NEVER raises for LLM problems; it returns a StructuredResult so the caller
can fall back. It also returns one CallRecord per API call for the llm_calls table
(failed calls included, because they still cost tokens).
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.client import LLMError, complete
from app.config import settings

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

# It’s a Python data container that maps LLM usage metrics and performance data into a single object for database logging.
@dataclass
class CallRecord:
    """One row for models.Llm_Call (field names match the columns)."""
    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    success: bool
    error: str | None = None


#It’s a container that bundles the validated AI response, API call history, successful model name, and any error message
@dataclass
class StructuredResult:
    data: BaseModel | None                      # validated object, or None on failure
    calls: list[CallRecord] = field(default_factory=list)
    model: str | None = None                    # model that produced the valid output
    error: str | None = None                    # why we failed (None on success)



def _extract_json(text: str) -> dict:
    """Strip code fences, then take everything from the first { to the last }."""
    text = _FENCE.sub("", text.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the reply")
    return json.loads(text[start : end + 1])


# It converts exceptions into a short, readable text description of why an AI response failed.
def _describe(exc: Exception) -> str:
    """Short human/LLM-readable reason a reply was rejected."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}" for e in exc.errors()
		)[:600]
    return str(exc)[:600]


def run_structured(
    schema_cls: type[T], system: str,
    user: str, purpose: str, max_tokens: int | None = None,
) -> StructuredResult:
    messages = [{"role": "user", "content": user}]  # Creates the initial chat history starting with your user prompt.
    calls: list[CallRecord] = []
    last_error = "unknown"	
                   
    # attempt 0 = normal call; attempts 1..N = "your JSON was invalid, fix it"
    for _ in range(settings.LLM_VALIDATION_RETRIES + 1):
        started = time.perf_counter()
        try:
            # Sends the actual request to the AI model using the system instructions and message history.
            resp = complete(system, messages, max_tokens)
        except LLMError as e:
            # The client already retried the network/API. Asking again won't help.
            calls.append(CallRecord(
                purpose, settings.LLM_MODEL, 0, 0, 0.0,
                int((time.perf_counter() - started) * 1000), False, str(e)[:500],
            ))
            return StructuredResult(None, calls, None, str(e))

        try:
            data = schema_cls.model_validate(_extract_json(resp.text))
        except (ValueError, ValidationError) as e: 
            last_error = f"invalid output: {_describe(e)}"
            calls.append(CallRecord(
                purpose, resp.model, resp.input_tokens, resp.output_tokens,
                resp.cost_usd, resp.latency_ms, False, last_error[:500],
			))
            # Show the model its own bad answer plus the exact problem.
            messages += [
                {"role": "assistant", "content": resp.text or "(empty reply)"},
                {"role": "user", "content": (
                    f"Your reply was rejected: {_describe(e)}. "
                    "Reply again with ONLY one valid JSON object that matches the schema."
                )},
            ]
            continue
        calls.append(CallRecord(
            purpose, resp.model, resp.input_tokens, resp.output_tokens,
            resp.cost_usd, resp.latency_ms, True, None,
        ))
        return StructuredResult(data, calls, resp.model, None)

    return StructuredResult(None, calls, None, last_error)