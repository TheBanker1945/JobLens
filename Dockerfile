# Two images from one file. Settings come from the environment at run time;
# nothing secret and nothing from data/ is ever copied in (.dockerignore, and
# .gcloudignore for what is uploaded to Cloud Build at all).
#
#   app      the web app, as Cloud Run runs it (scripts/hosted.py). The last
#            stage, so a plain `docker build` or `gcloud run deploy --source`
#            builds it.
#   nightly  the nightly update as a Cloud Run job (scripts/nightly.py): the
#            app's dependencies plus the scraper, and the fetch scripts.
FROM python:3.12-slim AS base

# uv, pinned to the version the lockfile was written with.
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The dependencies first, in a layer of their own: a code change rebuilds
# only the layers below it. The runtime ones only: no dev tools.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

# Not root: a hole in the app should not be a hole in the container.
RUN useradd --system --uid 10001 --no-create-home joblens


FROM base AS nightly
# JobSpy is pinned to a git commit (pyproject), so the build needs git.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --group scrape --no-install-project
COPY src ./src
COPY sources.toml boards.toml ./
COPY evals/extraction.toml ./evals/extraction.toml
COPY scripts/nightly.py scripts/fetch_vacancies.py scripts/index_vacancies.py \
     scripts/publish_corpus.py ./scripts/
RUN uv sync --frozen --no-dev --group scrape \
    && mkdir -p /app/data/raw /app/data/cache && chown -R joblens /app/data
USER joblens
# ENTRYPOINT, not CMD: a Cloud Run job's --args replace the CMD, so with a CMD
# `gcloud run jobs execute --args=--force` ran "--force" as the program. With
# the script as the entrypoint, arguments are appended to it.
ENTRYPOINT ["/app/.venv/bin/python", "scripts/nightly.py"]


FROM base AS app
COPY src ./src
COPY scripts/hosted.py ./scripts/hosted.py
RUN uv sync --frozen --no-dev
USER joblens
ENV PORT=8080
EXPOSE 8080
CMD ["/app/.venv/bin/python", "scripts/hosted.py"]
