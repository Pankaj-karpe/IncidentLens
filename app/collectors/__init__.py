import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import deploys, loki, prometheus

logger = logging.getLogger(__name__)

def _safe(fn, *args):
    # accepts any function(fn) and its args and catches if any error occurs to ensure triage system is not killed
    try:
        return fn(*args)
    except Exception as e:  # a collector bug must never kill triage
        logger.exception("collector %s crashed", fn.__module__)
        return {"available": False, "error": f"collector crashed: {type(e).__name__}"}


def collect_context(service: str, started_at: datetime) -> dict:
    """Run all collectors in parallel; always returns a dict, never raises."""

    # Spawns up to 3 worker threads so it can fetch data from Prometheus, Loki, and your deployment tracker simultaneously
    with ThreadPoolExecutor(max_workers=3) as pool:

        def submit(fn, *args):
            # New threads start with an EMPTY context, which would disconnect the tracing
            # parent span. copy_context() carries it over tp child so the traces flow seamlessly into Jaeger.
            # One copy per thread: a single
            # Context object cannot be entered by two threads at once.
            ctx = contextvars.copy_context()
            return pool.submit(ctx.run, _safe, fn, *args)
        
        # Each collector task wrapped with the _safe function before being submitted to the worker pool.
        metrics_f = submit(prometheus.collect, service, started_at)
        logs_f = submit(loki.collect, service, started_at)
        deploys_f = submit(deploys.collect, started_at)

        # Waits for all three threads to finish, grabs their output via .result(), and packages them 
        # into a clean dictionary to feed into the AI triage engine.
        return {
            "metrics": metrics_f.result(),
            "logs": logs_f.result(),
            "deploys": deploys_f.result(),
        }