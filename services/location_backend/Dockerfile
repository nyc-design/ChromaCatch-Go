# ---- Stage 1: Resolve Python dependencies ----
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry==2.3.2 \
    && poetry config virtualenvs.in-project true \
    && poetry install --only=main --no-interaction --no-ansi --no-root

# ---- Stage 2: Runtime ----
FROM python:3.11-slim AS runtime

WORKDIR /app

# Python virtualenv from builder
COPY --from=builder /build/.venv /app/.venv

# Shared protocol code (from additional build context)
COPY --from=shared . /app/services/shared/

# Location service application code (from primary build context)
COPY . /app/services/location_service/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/services"
ENV PYTHONUNBUFFERED=1

ENV CC_LOCATION_HOST=0.0.0.0
ENV CC_LOCATION_PORT=8001

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["python", "-m", "uvicorn", "location_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
