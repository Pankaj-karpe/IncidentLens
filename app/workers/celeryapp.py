from celery import Celery
from celery.signals import worker_ready

from ..config import settings
from ..database import engine
from ..observability import metrics, tracing

celery_app = Celery("incidentlens", broker=settings.BROKER_URL)
celery_app.conf.task_default_queue = "incidentlens"
# --- reliability (Part 5) ---
# Default Celery takes a job out of Redis and marks it "done" BEFORE running it:
# kill the worker mid-job (node drain, rollout) and the job is gone.
celery_app.conf.task_acks_late = True              # confirm a job only AFTER it finished
celery_app.conf.task_reject_on_worker_lost = True  # if the worker dies mid-job, give the job back
celery_app.conf.worker_prefetch_multiplier = 1     # hold only 1 waiting job per thread: the rest stays
                                                   # visible in the Redis list (what the autoscaler counts)
                                                   # and can go to a NEW worker
# An unconfirmed job returns to the queue after 5 min (default: 1 hour). Safe to re-run:
# triage skips incidents already OK, and postmortem skips existing drafts.
celery_app.conf.broker_transport_options = {"visibility_timeout": 300}
celery_app.autodiscover_tasks(["app.workers"])



@worker_ready.connect
def _on_worker_Ready(sender=None, **kargs):
    """The @worker_ready.connect hook executes automatically the moment the Celery 
    worker process boots up and is ready to accept tasks (such as when launched 
    with --pool=solo). Running exclusively within the worker terminal rather than 
    the FastAPI server, it initializes the application's background telemetry 
    infrastructure by spinning up a metrics server on port 9101 for Prometheus 
    scraping and setting up OpenTelemetry tracing via tracing.py to seamlessly 
    pipe background execution spans into Jaeger.
    """
    metrics.start_worker_metrics_server()                # http://localhost:9101/metrics
    tracing.setup_tracing("incidentlens-worker", engine=engine)         