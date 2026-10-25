# syntax=docker/dockerfile:1

# Stage 1: build the app wheel and the wheels of its dependencies.
FROM python:3.13-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels .

# Stage 2: runtime. Installs from the wheels only (no build tools, no sources, no pip cache).
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-index --find-links /wheels cyber-news \
    && useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
USER app
EXPOSE 8000
# curl is not in the slim image, so the check uses Python's urllib; urlopen raises on a non-2xx status.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
# No --preload: each worker builds its own app and Elasticsearch client after the fork.
# --no-control-socket: gunicorn 26's admin socket (default ~/.gunicorn/gunicorn.ctl) is not needed, and the
# app user has no home directory, so without it the arbiter logs "Control server error: Permission denied".
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "--no-control-socket", "cyber_news:create_app()"]
