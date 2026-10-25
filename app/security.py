"""
Authentication + authorization in one place. Two kinds of callers:

  1. Machines (Alertmanager): one shared secret, sent as "Authorization: Bearer <WEBHOOK_TOKEN>".
  2. People: log in with username + password (POST /auth/login), get a short-lived JWT,
     send it as "Authorization: Bearer <jwt>".

Design rules:
  * Fail closed: no WEBHOOK_TOKEN configured means the webhook refuses everything (503);
    a weak SECRET_KEY stops the app at startup.
  * The JWT only says WHO the user is (user id). Role and is_active are read from the
    database on EVERY request, so demoting or deactivating someone takes effect at once.
  * Passwords are hashed with argon2 (slow on purpose). Plain passwords are never stored or logged.
  * Secrets are compared with hmac.compare_digest (constant time).
"""
import functools
import hmac
import logging
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db
from .observability import metrics

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("incidentlens.audit")

# ---- roles: the single place that says who may do what ----
ROLES = ("viewer", "developer", "devops", "sre")
MODIFY_ROLES = ("developer", "devops", "sre")     # resolve incidents, edit postmortem drafts
REVIEW_ROLES = ("devops", "sre")                  # sign off a postmortem

_ALLOWED_ALGORITHMS = ("HS256", "HS384", "HS512")  # never "none", never asymmetric by accident
_hasher = PasswordHasher()
# tokenUrl makes the "Authorize" button work in the /docs page
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def validate_auth_settings() -> None:
    """Called once at startup (main.py). Crashing early beats running insecurely."""
    if not settings.SECRET_KEY or len(settings.SECRET_KEY) < 32:
        raise RuntimeError("SECRET_KEY must be set and at least 32 characters long "
                           "(python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
    if settings.ALGORITHM not in _ALLOWED_ALGORITHMS:
        raise RuntimeError(f"ALGORITHM must be one of {_ALLOWED_ALGORITHMS}")
    if settings.WEBHOOK_TOKEN and len(settings.WEBHOOK_TOKEN) < 24:
        raise RuntimeError("WEBHOOK_TOKEN is too short (use at least 24 characters)")
    if not settings.WEBHOOK_TOKEN:
        logger.warning("WEBHOOK_TOKEN is not set: POST /alerts will answer 503 until it is")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):    # wrong password, or a corrupt hash
        return False


@functools.cache
def dummy_hash() -> str:
    """Verified against when the username does not exist, so the answer takes the same
    time either way (otherwise response time would reveal which usernames are real)."""
    return _hasher.hash("not-a-real-password")


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def create_access_token(user_id: int) -> tuple[str, int]:
    """Returns (jwt, seconds_until_expiry). "sub" must be a string in JWT."""
    # Encodes the user ID into a signed JWT containing expiration and issuance timestamps.
    lifetime = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": str(user_id), "iat": now, "exp": now + lifetime},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, int(lifetime.total_seconds())


def decode_token(token: str) -> int | None:
    """User id if the token is valid and unexpired, otherwise None.
    The algorithm list comes from OUR config, never from the token header
    (otherwise an attacker could pick "none")."""
    #Securely decodes and validates the JWT using local configuration algorithms to extract the user ID.
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
                             options={"require": ["exp", "sub"]})
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies (these are what the routers use)
# ---------------------------------------------------------------------------
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    """Who is calling? 401 if the token is missing, invalid, expired or the user is disabled."""
    # Decodes the token, fetches a fresh user record from the database, and rejects invalid, expired, or disabled accounts with a 401.
    user_id = decode_token(token)
    user = db.get(models.User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def require_roles(*allowed: str):
    """Build a dependency that only lets the given roles through (403 for everyone else).
        user = Depends(require_roles("sre", "devops"))"""
    # Enforces role-based access control (RBAC) by checking if the authenticated user possesses one of the allowed roles, raising a 403 if unauthorized.
    def checker(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in allowed:
            metrics.AUTH_EVENTS.labels("forbidden").inc()
            logger.warning("forbidden: user=%s role=%s needs one of %s", user.username, user.role, allowed)
            raise HTTPException(status_code=403,
                                detail=f"Role '{user.role}' may not do this (needs one of: {', '.join(allowed)})")
        return user
    return checker


def verify_webhook(authorization: str | None = Header(default=None)) -> None:
    """Guards POST /alerts. Alertmanager sends the shared secret as a Bearer token."""
    # Authenticates incoming Alertmanager webhook requests via a timing-safe shared secret comparison (hmac.compare_digest).
    expected = settings.WEBHOOK_TOKEN
    if not expected:
        raise HTTPException(status_code=503, detail="Webhook token is not configured on the server")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), expected.encode()):
        metrics.AUTH_EVENTS.labels("webhook_rejected").inc()
        logger.warning("webhook rejected: missing or wrong token")
        raise HTTPException(status_code=401, detail="Invalid webhook token",
                            headers={"WWW-Authenticate": "Bearer"})


def audit(user: models.User, action: str, **fields) -> None:
    """One log line per state-changing action: who did what. Ship it to Loki and you have an audit trail."""
    #Generates structured, single-line compliance logs tracking who performed specific state-changing actions.
    details = " ".join(f"{k}={v}" for k, v in fields.items())
    audit_logger.info("audit user=%s role=%s action=%s %s", user.username, user.role, action, details)