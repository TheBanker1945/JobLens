# The web app, as Cloud Run runs it (7.8.2). Settings come from the
# environment at run time (scripts/hosted.py lists them); nothing secret and
# nothing from data/ is ever copied in (.dockerignore).
FROM python:3.12-slim

# uv, pinned to the version the lockfile was written with.
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The dependencies first, in a layer of their own: a code change rebuilds
# only the layers below it. The runtime ones only: no dev tools, no scraper.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY scripts/hosted.py ./scripts/hosted.py
RUN uv sync --frozen --no-dev

# Not root: a hole in the app should not be a hole in the container.
RUN useradd --system --uid 10001 --no-create-home joblens
USER joblens

ENV PORT=8080
EXPOSE 8080
CMD ["/app/.venv/bin/python", "scripts/hosted.py"]
