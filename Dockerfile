FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONFAULTHANDLER=1

# libmagic1 for python-magic; ca-certificates for outbound HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "flask>=3.0,<4" \
        "flask-sqlalchemy>=3.1,<4" \
        "sqlalchemy>=2.0,<3" \
        "gunicorn>=22,<24" \
        "torf>=4.2,<5" \
        "transmission-rpc>=7.0,<8" \
        "python-magic>=0.4.27,<0.5" \
        "humanize>=4.9,<5" \
        "short-url>=1.2.2,<2" \
        "validators>=0.22,<1" \
        "psycopg[binary]>=3.1,<4" \
        "requests>=2.31,<3"

COPY app/ ./app/
COPY scripts/ ./scripts/

# Persistent data lives at /data.
RUN mkdir -p /data/up /data/db
VOLUME ["/data"]

EXPOSE 8080

# Healthcheck against the app (compose / Railway can also probe).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

ENV GUNICORN_WORKERS=2 \
    GUNICORN_TIMEOUT=600 \
    PORT=8080

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers ${GUNICORN_WORKERS} --timeout ${GUNICORN_TIMEOUT} --access-logfile - --error-logfile - 'app:app'"]
