FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
COPY examples /app/examples

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="EV Fleet Charging Optimization Engine" \
      org.opencontainers.image.description="EV fleet charging optimization engine" \
      org.opencontainers.image.source="https://github.com/MalekBENMARZOUK/EV_fleet_charging_optimisation_engine" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup --home /app appuser \
    && apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/state && chown appuser:appgroup /app/state

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/examples /app/examples
COPY README.md LICENSE /app/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

CMD ["uvicorn", "smart_charging_optimization_engine.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

CMD ["uvicorn", "smart_charging_optimization_engine.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", \
     "--timeout-keep-alive", "65", \
     "--access-log", \
     "--limit-concurrency", "100"]
