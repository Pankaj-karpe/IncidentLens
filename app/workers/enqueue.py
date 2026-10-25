"""
One safe way to queue a background job from a web request.
If RabbitMQ is down, .delay() raises. We catch it and log it: the incident is already
saved in Postgres, and returning a 500 would only make Alertmanager resend the same alert.
"""
import logging

logger = logging.getLogger(__name__)


def enqueue(task, *args) -> bool:
    """Returns True if the job was queued, False if the broker refused it."""
    try:
        task.delay(*args)
        return True
    except Exception:
        logger.exception("could not enqueue %s%s", getattr(task, "name", task), args)
        return False