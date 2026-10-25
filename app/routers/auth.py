import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schema, security
from ..database import get_db
from ..observability import metrics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login", response_model=schema.TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Form fields: username, password. (A form, not JSON, so the /docs "Authorize" button works.)"""
    username = form.username.strip().lower()
    user = db.scalar(select(models.User).where(models.User.username == username))

    # Always verify a hash, even when the user does not exist: the response time then
    # does not reveal which usernames are real.
    password_ok = security.verify_password(user.password_hash if user else security.dummy_hash(),
                                           form.password)
    # One generic message for unknown user / wrong password / disabled user.
    if user is None or not password_ok or not user.is_active:
        metrics.AUTH_EVENTS.labels("login_failure").inc()
        logger.warning("login failed for username=%r", username[:64])
        raise HTTPException(status_code=401, detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Bearer"})

    token, expires_in = security.create_access_token(user.id)
    metrics.AUTH_EVENTS.labels("login_success").inc()
    logger.info("login ok: user=%s role=%s", user.username, user.role)
    return schema.TokenOut(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=schema.UserOut)
def me(user: models.User = Depends(security.get_current_user)):
    """Who am I? The viewer uses this to show or hide buttons."""
    return user