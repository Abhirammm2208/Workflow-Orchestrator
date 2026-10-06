# =============================================================================
# Workflow Orchestrator — Dockerfile
# Python 3.12-slim (matches local development environment)
# =============================================================================

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# ---------------------------------------------------------------------------
# System dependencies
# libpq-dev  — PostgreSQL client headers (needed by psycopg-binary build)
# gcc        — C compiler for any native extensions
# curl       — health-check utility
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python dependencies — installed before copying source so this layer
# is cached and not re-built on every code change
# ---------------------------------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Application source
# ---------------------------------------------------------------------------
COPY . .

# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------
EXPOSE 8000

# Default: run the FastAPI server
# This is overridden per-service in docker-compose.yml
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
