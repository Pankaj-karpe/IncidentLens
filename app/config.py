from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- database / broker / auth ---
    DATABASE_URL: str | None = None
    ALEMBIC_URL: str | None = None
    BROKER_URL: str | None = None
    SECRET_KEY: str | None = None
    ALGORITHM: str | None = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int | None = None

    # --- collectors (defaults are real values, never None) ---
    PROMETHEUS_URL: str = "http://localhost:9090"
    LOKI_URL: str = "http://localhost:3100"
    LOKI_SERVICE_LABEL: str = "app"
    GITHUB_TOKEN: str | None = None
    GITHUB_DEPLOY_REPO: str | None = None
    COLLECTOR_TIMEOUT_SECONDS: float = 5.0
    LOOKBACK_MINUTES: int = 15
    DEPLOY_LOOKBACK_HOURS: int = 6
    MAX_LOG_LINES: int = 30
    MAX_LOG_LINE_CHARS: int = 300
    MAX_PROMPT_CHARS: int = 12000

    # --- primary LLM ---
    # "openai" means the OpenAI-compatible protocol (OpenAI, Gemini, Ollama, Groq...).
    LLM_PROVIDER: str = "openai"
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "gemini-2.5-flash"
    LLM_TEMPERATURE: float | None = None          # None = provider default
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_MAX_RETRIES: int = 2                      # network / 429 / 5xx retries per target
    LLM_VALIDATION_RETRIES: int = 1               
    LLM_MAX_OUTPUT_TOKENS: int = 2048             # Gemini "thinking" also uses this budget
    LLM_PRICE_INPUT_PER_MTOK: float = 0.0         # USD per 1M tokens (0 = free / untracked)
    LLM_PRICE_OUTPUT_PER_MTOK: float = 0.0

    # --- fallback LLM (own provider, URL and key, e.g. local Ollama) ---
    LLM_FALLBACK_PROVIDER: str = "openai"
    LLM_FALLBACK_BASE_URL: str | None = None
    LLM_FALLBACK_API_KEY: str | None = None
    LLM_FALLBACK_MODEL: str | None = None         # None = no fallback
    LLM_FALLBACK_TIMEOUT_SECONDS: float = 120.0   # local CPU models are slow

    # --- embeddings (similar-incident search) ---
    CACHE_TTL_MINUTES: int = 60      # 0 disables the cache
    EMBEDDING_API_KEY: str | None = None 
    EMBEDDING_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    SIMILAR_INCIDENT_LIMIT: int = 3
    SIMILARITY_MIN_SCORE: float = 0.75
    EMBEDDING_DIMENSIONS: int | None = 1536

    # --- notifications (Slack and/or email; each is optional: app will still work) ---
    NOTIFY_ENABLED: bool = True
    PUBLIC_BASE_URL: str | None = None
    SLACK_WEBHOOK_URL: str | None = None    # SECRET: anyone with it can post to your channel
    NOTIFY_TIMEOUT_SECONDS: float = 10.0
    NOTIFY_MAX_RETRIES: int = 2             # Slack only: retries for network errors / 429 / 5xx
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_SECURITY: Literal["starttls", "ssl", "none"] = "starttls"
    EMAIL_FROM: str | None = None
    EMAIL_TO: str | None = None             # comma separated: "a@x.com,b@x.com"

    # --- observability ---
    METRICS_WORKER_PORT: int = 9101         # the worker serves its own /metrics here
    OTEL_ENABLED: bool = False              # true = send traces to Jaeger
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4318"


    # --- postmortem ---
    POSTMORTEM_MAX_OUTPUT_TOKENS: int = 4096   # postmortems are longer than triage answers
    POSTMORTEM_WAIT_SECONDS: int = 30          # re-check delay while triage is still running

    # --- auth ---
    SECRET_KEY: str | None = None             # signs login tokens (JWT); at least 32 characters
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    WEBHOOK_TOKEN: str | None = None          # shared secret Alertmanager sends as "Authorization: Bearer ..."
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

settings = Settings()