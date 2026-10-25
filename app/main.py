from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import security
from .routers import alerts, incidents, health, auth
from .database import engine
from .observability import metrics, tracing

security.validate_auth_settings()    # refuse to start with a missing/weak SECRET_KEY

app = FastAPI(title="IncidentLens")

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "I am Root !"}

app.include_router(health.router)
app.include_router(alerts.router)
app.include_router(incidents.router)
app.include_router(metrics.router)      # GET /metrics for Prometheus
app.include_router(auth.router)         # open: login

# No-op unless OTEL_ENABLED=true. Adds spans for requests, SQL, Celery publishes, httpx.
tracing.setup_tracing("incidentlens-api", app=app, engine=engine)