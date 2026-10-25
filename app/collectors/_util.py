import re
from datetime import datetime, timezone

_SAFE = re.compile(r"[^A-Za-z0-9_.:\-]")


def safe_label(value: str | None) -> str:
    """Strip anything that could break out of a PromQL/LogQL string literal."""
    return _SAFE.sub("", value or "")


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

def iso_z(dt: datetime) -> str:
    """formats the datetime """
    return as_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")