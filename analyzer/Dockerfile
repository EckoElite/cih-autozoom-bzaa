# syntax=docker/dockerfile:1
#
# CIH Auto-Zoom analyzer. Build context is this folder (analyzer/).
#
#   docker build -t cih-analyzer .                        the image that runs on Unraid
#   docker build --target test -t cih-analyzer-test . && docker run --rm cih-analyzer-test
#                                                          the whole test suite, inside the same ffmpeg
#
# ffmpeg comes from Debian 13 (apt, signature-checked, 7.1.x) rather than a downloaded static build: nothing is
# fetched from a third party at build time, and the test stage runs the suite against exactly this ffmpeg.

FROM python:3.12-slim-trixie AS base

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    CONFIG_DIR=/config \
    MEDIA_ROOTS=/media \
    PORT=8765

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY src ./src

# Unraid's own owner for appdata and shares is nobody:users (99:100); the service never runs as root
RUN useradd --uid 99 --gid 100 --no-create-home --shell /usr/sbin/nologin cih \
 && mkdir -p /config && chown 99:100 /config


FROM base AS test
COPY requirements-dev.txt pyproject.toml ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
USER 99:100
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


FROM base AS runtime
USER 99:100
VOLUME /config
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8765'), timeout=4)"]
CMD ["python", "-m", "cih_analyzer.server"]
