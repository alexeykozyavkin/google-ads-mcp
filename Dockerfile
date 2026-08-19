FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml README.md ./
COPY google_ads_mcp ./google_ads_mcp

RUN python -m pip install --upgrade pip \
    && python -m pip install .

USER appuser

EXPOSE 8080

CMD ["python", "-m", "google_ads_mcp.remote_server"]

