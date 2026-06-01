# Multi-stage Dockerfile for production

# Stage 1: Builder
FROM python:3.13-slim AS builder

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy project metadata and install production deps only
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Final production image
FROM python:3.13-slim AS final

ENV PYTHONUNBUFFERED=1
ENV PORTF_ENVIRONMENT=production
ENV PYTHONPATH="/app"
# Activate the venv created by uv
ENV PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

# Create non-root user
RUN useradd --create-home --shell /bin/bash --uid 1001 portf

WORKDIR /app

COPY portf_server/ ./portf_server/
COPY portf_manager/ ./portf_manager/
COPY settings.toml .
COPY start_server.py .

RUN mkdir -p /app/logs && \
    chown -R portf:portf /app

USER portf

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "portf_server.app:app", "-b", "0.0.0.0:8000", "--workers", "4"]
