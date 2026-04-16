FROM python:3.11-slim AS build

WORKDIR /app

# Build-stage system deps.
#   curl      — retained for healthcheck parity if the runtime stage ever
#               collapses into this one; cheap to keep.
#   gcc       — required to compile psycopg2 from source.  We pin the
#               source build (not psycopg2-binary) because the -binary
#               wheel statically links libpq and can diverge from the
#               system libpq in subtle SSL/locale ways that are hard to
#               diagnose on GovCloud.  Source builds against the libpq
#               we control below.
#   libc6-dev — Debian slim omits the C standard library headers
#               (for example stdlib.h).  psycopg2's source build pulls
#               in Python.h, which includes those libc headers; without
#               them Railway fails during `pip install .` with
#               `fatal error: stdlib.h: No such file or directory`.
#   libpq-dev — PostgreSQL client headers, needed for psycopg2's
#               C extension build step.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libc6-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy source first — pip install needs the package present for hatchling
# to resolve the build. Splitting pyproject.toml alone does not work with
# editable-style or source-tree builds.
COPY pyproject.toml ./
COPY forge/ forge/
COPY migrations/ migrations/

RUN pip install --no-cache-dir .

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
