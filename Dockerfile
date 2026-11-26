FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLEARCUT_DATA_DIR=/app/data \
    DATABASE_PATH=/app/data/deals.db \
    DEALS_DB_READ_ONLY=1 \
    DISABLE_APP_LIFESPAN=1 \
    TURSO_DIRECT_CONNECTION=1 \
    AUTH_DB_PATH=/tmp/auth_replica.db \
    PUBLIC_MODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY common ./common
COPY catalogs ./catalogs
COPY scripts ./scripts
COPY app.py render_app.py ./

RUN pip install --no-cache-dir . uvicorn && \
    python scripts/render_prepare.py --output /app/data

EXPOSE 10000
CMD ["sh", "-c", "uvicorn render_app:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
