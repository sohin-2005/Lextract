# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Lextract backend image.
#
# Multi-stage: wheels are built in a stage with a compiler, then copied into a
# slim runtime. python-Levenshtein and asyncpg both need gcc to build, but
# neither needs it to run, so the final image ships without a toolchain.
# ---------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq is needed at runtime by asyncpg's fallbacks; curl powers HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Never run the API as root.
RUN useradd --create-home --shell /bin/bash lextract

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

COPY --chown=lextract:lextract . .

RUN mkdir -p /app/dataset/bills \
    && chown -R lextract:lextract /app/dataset \
    # Git on Windows drops the executable bit; set it explicitly so the
    # image boots regardless of where it was checked out.
    && chmod +x /app/start.sh
USER lextract

# Railway, Render, Fly and Cloud Run all inject $PORT and route to it. The
# EXPOSE value is documentation only; the CMD below is what actually matters.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/api/health" || exit 1

# start.sh runs migrations then execs uvicorn on $PORT. It deliberately does
# NOT chain them with `&&` — see the comment in that file for why a failed
# migration must not stop the server from binding the port.
CMD ["./start.sh"]
