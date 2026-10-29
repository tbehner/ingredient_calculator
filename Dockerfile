# syntax=docker/dockerfile:1

# Build the virtualenv in a stage with uv available, then copy just the venv
# into the runtime image.
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, so editing the source does not re-resolve them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
# `--no-editable`: uv would otherwise link the project back to /app/src, which
# the runtime stage does not have — only the venv is copied over.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Not a project dependency: the devshell runs the Flask dev server, gunicorn is
# only needed for the container.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install gunicorn


FROM python:3.12-slim AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INGREDIENTS_DB=/data/bls.sqlite3

WORKDIR /app

COPY --from=build /app/.venv /app/.venv

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

# The database comes from elsewhere and is opened read-only; mount it, e.g.
#   docker run -p 8000:8000 -v /path/to/bls.sqlite3:/data/bls.sqlite3:ro IMAGE
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", \
     "ingredients_calculator.app:create_app()"]