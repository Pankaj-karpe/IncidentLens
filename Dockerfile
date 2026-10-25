# syntax=docker/dockerfile:1

# One image runs everything (API, Celery worker, migration job): only the command differs.
# Stages:
#   builder -> installs dependencies into a virtualenv (build tools stay here)
#   test    -> builder + dev dependencies + tests/eval:  docker build --target test .
#   runtime -> small final image (the DEFAULT, because it is the last stage)

ARG PYTHON_VERSION=3.12

# ----------------------------- builder -----------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_RETRIES=10
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt


# -------------------------- test --------------------------------------
FROM builder AS test
COPY requirements-dev.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-dev.txt
WORKDIR /srv
COPY pytest.ini alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY eval ./eval
CMD ["pytest", "-v"]


# ----------------------------- runtime ----------------------------------- 
FROM python:${PYTHON_VERSION}-slim AS runtime    
LABEL org.opencontainers.image.source="https://github.com/Pankaj-karpe/incidentlens"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root, numeric id (so Kubernetes' runAsNonRoot can verify it), no shell, no home.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /srv
# Only the ready-made virtualenv comes over: no pip cache, no compilers.
COPY --from=builder /opt/venv /opt/venv
# The code stays owned by root and read-only for appuser (it cannot modify itself).
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

USER 10001
EXPOSE 8000
# No HEALTHCHECK here: the worker and the migration job use this image too.
# The API's healthcheck lives in docker-compose.yml and in the Kubernetes probes.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]