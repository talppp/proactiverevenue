# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for the L1 SMS-phone service.
# Works on Render, Fly.io, Railway, Google Cloud Run — any container host.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so layer cache survives source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the runtime needs.
COPY app/ ./app/
COPY migrations/ ./migrations/

# Non-root for safety.
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Cloud Run / Render set $PORT; default to 8000 for local docker run.
ENV PORT=8000
EXPOSE 8000

# Honest healthcheck — confirms the lifespan started.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8000)}/healthz').read()" || exit 1

CMD ["sh", "-c", "uvicorn app.main_l1:app --host 0.0.0.0 --port ${PORT}"]
