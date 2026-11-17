# ShopMate backend image (System Design section 3.10).
#
# Standard uv pattern: copy the lockfile first, sync dependencies into a
# cacheable layer, then install the project. `--frozen` makes uv.lock the
# authoritative source (it never resolves or updates the lock at build time),
# so builds are reproducible and network access is only needed for the
# pinned wheels themselves.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer: only invalidated when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Project layer: the FastAPI app itself.
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Runtime data lives in /app/data (bind-mounted from ./backend/data by
# docker-compose.yml) so the SQLite database persists across restarts.
RUN mkdir -p /app/data

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
