from celery import Celery
from celery.signals import worker_ready
from ..config import settings
from ..observability import metrics, tracing
from ..database import engine

celery_app = Celery("incidentlens", broker=settings.BROKER_URL)
celery_app.conf.task_default_queue = "incidentlens"
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