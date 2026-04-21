# Module:   FORGE-MFG API Dockerfile
# Purpose:  Multi-stage build.  Build stage installs gcc + libpq-dev
#           to compile psycopg2 from source; runtime stage ships only
#           the installed wheels + libpq5 runtime linkage.  This keeps
#           the production image narrow (no compiler toolchain in the
#           running container) and cuts the Trivy CRITICAL/HIGH
#           vulnerability surface that the staging scan gate enforces.
# Rev:      0.2.0
#
# BoundState Proprietary
# Co-Authored-By: Dr. Joseph Wilde, BoundState AI
# Co-Authored-By: Claude "Beth" (Opus 4.7)
#
# Why source-build psycopg2 (not psycopg2-binary): the -binary wheel
# statically links libpq and can diverge from the system libpq in
# subtle SSL/locale ways that are hard to diagnose on GovCloud.
# Source builds link against the libpq5 we control in the runtime
# stage, so TLS verify-full behavior matches what we deploy.

# ── Build stage ─────────────────────────────────────────────────────
FROM python:3.11-slim AS build
WORKDIR /app

# Build-stage system deps.
#   gcc       — required to compile psycopg2 from source.
#   libc6-dev — Debian slim omits libc headers; psycopg2's source
#               build pulls in Python.h which transitively includes
#               stdlib.h.
#   libpq-dev — PostgreSQL client headers for the psycopg2 C
#               extension's link step.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Source layout needed for hatchling-based source install.  Splitting
# pyproject.toml alone does not work; hatchling walks the package tree.
COPY pyproject.toml ./
COPY forge/ forge/
COPY migrations/ migrations/

# Install into an isolated prefix so the runtime stage can lift a
# clean copy without inheriting pip's cache or the build-time
# compiler toolchain.  `--prefix=/install` places site-packages under
# /install/lib/pythonX.Y/site-packages and entry-point scripts under
# /install/bin.
RUN pip install --no-cache-dir --prefix=/install .

# ── Runtime stage ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

# Runtime-only system deps.
#   curl   — healthcheck.
#   libpq5 — runtime library psycopg2's C extension links against.
#            No -dev, no compiler, no build headers.
# `apt-get upgrade` pulls current security patches so the image does
# not ship with whatever CVEs the slim base tag had on its own build
# day — this is the difference between passing today's Trivy gate and
# shipping a month-stale base.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Upgrade Python packaging tooling.  The base python:3.11-slim tag
# ships with stale `setuptools` + `wheel` (and their vendored
# jaraco.context / wheel copies) that fail a CRITICAL/HIGH Trivy gate
# on CVE-2026-23949 (jaraco.context path traversal) and CVE-2026-24049
# (wheel privilege escalation).  Upgrading here pulls the patched
# versions into site-packages in the runtime image.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy installed wheels + entry-point scripts from build stage.
# /install -> /usr/local puts site-packages on the default sys.path
# and scripts (gunicorn, alembic) on PATH for the default PYTHONPATH.
COPY --from=build /install /usr/local

# Migrations are runtime assets — alembic operates against the live
# DB during deploy/rollback, and troubleshooting can invoke them from
# inside a task-exec shell.
COPY migrations/ migrations/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8000}/health" || exit 1

CMD ["/bin/sh", "-c", "gunicorn forge.api.app:app \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers 3 \
    --timeout 120 \
    --max-requests 1000 \
    --max-requests-jitter 50"]
