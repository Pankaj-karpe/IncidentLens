"""
security check where everything goes to LLM passes through here first.
Logs print sensitive information so replace it with [RDACTED].
Now LLM understand the logs too, over-redacting is acceptable; leaking is not.
"""

import re
from typing import Any

REDACTED = "[REDACTED]"

# 1. PEM private keys (multi-line, so DOTALL lets "." match newlines)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

# 2. Credentials inside URLs: postgresql://user:pass@host -> keep scheme and host only.
#    Must run BEFORE the email rule, or "pass@host.com" would look like an email.
_URL_CREDS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@")

# 3. JWTs: three base64url parts separated by dots, the first starts with "eyJ" ({"...)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")

# 4. "Authorization: Bearer xxxx" / "Basic xxxx"
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")

# 5. Well-known token formats: AWS, GitHub, Slack, OpenAI/Anthropic-style, Google
_KNOWN_TOKENS = re.compile(
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    r"|\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"
    r"|\bsk-[A-Za-z0-9_\-]{20,}\b"
    r"|\bAIza[0-9A-Za-z_\-]{35}\b"
)

# 6. key=value / key: value / "key": "value" where the key NAME looks sensitive
#    (password, db_password, access_token, api-key, ...). Group 1 (key + separator)
#    is kept, group 2 (the value) is replaced. The lookahead makes it idempotent.
_KEY_NAMES = (
    r"password|passwd|pwd|secret|token|api[-_]?key|access[-_]?key|"
    r"private[-_]?key|authorization|credential"
)
_KEY_VALUE = re.compile(
    r"(?i)([a-z0-9_\-]*(?:" + _KEY_NAMES + r")[\"']?\s*[:=]\s*[\"']?)"
    r"(?!\[REDACTED\])([^\s\"',;&}\]]+)"
)

# 7. Emails (personal data, no reason to send it to a third party)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# For structured data: if a dict KEY looks sensitive, hide the whole value.
_SENSITIVE_KEY = re.compile(r"(?i)(" + _KEY_NAMES + r")")

def redact_text(text: str) -> str:
    """Apply every rule to one string. Order matters (see comments above)."""
    if not text:
        return text
    text = _PRIVATE_KEY.sub(REDACTED, text)
    text = _URL_CREDS.sub(lambda m: f"{m.group(1)}{REDACTED}:{REDACTED}@", text)
    text = _JWT.sub(REDACTED, text)
    text = _AUTH_SCHEME.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _KNOWN_TOKENS.sub(REDACTED, text)
    text = _KEY_VALUE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _EMAIL.sub(REDACTED, text)
    return text

def redact_obj(obj: Any) -> Any:
    """
    Recursively redact dicts/lists/strings (the incident context is a nested dict).
    Returns a NEW structure; the original (stored in the DB) is never modified.
    Numbers, bools and None pass through untouched.
    """
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else redact_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj