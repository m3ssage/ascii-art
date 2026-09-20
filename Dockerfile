# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build stage: install the package into a throwaway venv.  Build tooling
# (setuptools, pip's isolated build env) stays in this stage.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS build

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir .

# ---------------------------------------------------------------------------
# Final stage: slim runtime, no compiler, no build tooling, runs as nobody.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# A dedicated unprivileged user; the service never runs as root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin ascii

WORKDIR /app

COPY --from=build /venv /venv

ENV PATH="/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASCII_ART_HOST=0.0.0.0 \
    ASCII_ART_PORT=8080

USER ascii
EXPOSE 8080

# No volumes on purpose: uploads are decoded and rendered in memory and are
# never written to disk or sent to any external service.
CMD ["python", "-m", "ascii_art.web"]
