# ATAT API (and the optional MCP server) — one image, two commands.
#
#   docker compose --env-file instances/<name>/compose.env -p atat-<name> up -d --build
#
# The image holds code and the shared prompts only. Everything personal — database, CVs,
# personal prompt files, the Gmail token, .env — lives in the mounted instance directory
# (ATAT_HOME=/data), the CV library is mounted at /cv-library and the Poppins fonts at
# /fonts. Nothing about a person is ever baked into the image (see .dockerignore).
# Where the base image comes from. Override with BASE_REGISTRY=mirror.gcr.io (Google's Docker Hub
# mirror) on a network that can't reach registry-1.docker.io directly.
ARG BASE_REGISTRY=docker.io
FROM ${BASE_REGISTRY}/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ATAT_HOME=/data \
    CV_LIBRARY_PATH=/cv-library \
    FONTS_PATH=/fonts

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY api ./api
COPY db ./db
COPY pipeline ./pipeline
COPY mcp_server ./mcp_server
COPY prompts ./prompts

# Runs as an unprivileged user. Compose overrides the uid/gid to match the owner of the
# mounted instance directory (ATAT_UID / ATAT_GID).
RUN useradd --uid 1000 --create-home atat && mkdir -p /data /cv-library /fonts && chown atat /data
USER atat

EXPOSE 8000

# One API process only: the job runner and the schedulers run inside it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
