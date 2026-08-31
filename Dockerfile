FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/cfdns/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/cfdns/.venv \
    && addgroup --system cfdns \
    && adduser --system --ingroup cfdns --home /opt/cfdns cfdns

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

WORKDIR /opt/cfdns
COPY --chown=cfdns:cfdns alembic.ini ./
COPY --chown=cfdns:cfdns migrations ./migrations
COPY --chown=cfdns:cfdns app ./app
COPY --chown=cfdns:cfdns scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh

USER cfdns
EXPOSE 8000

ENTRYPOINT ["./scripts/docker-entrypoint.sh"]
