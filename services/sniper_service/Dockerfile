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

COPY --from=builder /build/.venv /app/.venv
COPY --from=shared . /app/services/shared/
COPY . /app/services/sniper_service/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/services"
ENV PYTHONUNBUFFERED=1

ENV CC_SNIPER_API_HOST=0.0.0.0
ENV CC_SNIPER_API_PORT=8010

EXPOSE 8010

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health')" || exit 1

CMD ["python", "-m", "uvicorn", "sniper_service.main:app", "--host", "0.0.0.0", "--port", "8010"]
