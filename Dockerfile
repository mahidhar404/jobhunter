# job-hunter dashboard image.
#
# Built on the official Playwright Python base so the pinned Chromium that
# playwright==1.61.0 expects is already present (no `playwright install` at
# runtime, no ~150 MB download). The tag matches the pinned Playwright version
# in requirements.txt. This base is multi-arch (linux/amd64 + linux/arm64), so
# it works on Apple Silicon and x86 hosts alike.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

# - PYTHONUNBUFFERED: logs stream immediately (the dashboard prints progress).
# - JOBHUNTER_DASHBOARD_HOST=0.0.0.0: bind all interfaces so the published port
#   is reachable from the host (server.py defaults to 127.0.0.1 on macOS).
# - JOBHUNTER_TECTONIC_BIN: resume->PDF engine installed below (also on PATH).
# - OpenClaw is intentionally NOT installed: the agent-recovery / cron / managed
#   PartyRock-browser features are host-only. server.py degrades gracefully when
#   the binary is absent (it only shells out on demand), so the dashboard starts.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JOBHUNTER_DASHBOARD_HOST=0.0.0.0 \
    JOBHUNTER_DASHBOARD_PORT=8787 \
    JOBHUNTER_DATA_DIR=/app/data \
    JOBHUNTER_TECTONIC_BIN=/usr/local/bin/tectonic

WORKDIR /app

# Tectonic (LaTeX -> PDF for tailored resumes). The official installer script
# auto-detects the CPU arch and drops a `tectonic` binary in the CWD; we move it
# onto PATH. fontconfig is needed at runtime for font resolution. If this layer
# fails in a restricted network, resume->PDF simply won't work — the rest of the
# dashboard is unaffected (see docs/DOCKER.md for an offline/apt fallback).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl fontconfig \
    && curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh \
    && mv tectonic /usr/local/bin/tectonic \
    && chmod +x /usr/local/bin/tectonic \
    && tectonic --version \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python deps first for better layer caching. The base already provides
# playwright + browsers; reinstalling the same pinned version is a no-op that
# keeps the app self-consistent.
COPY requirements.txt ./
RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install -r requirements.txt

# App source. Real PII/config/artifacts are excluded via .dockerignore; only the
# code, docs, and fixtures/*.example templates enter the build context.
COPY . .

RUN chmod +x docker/entrypoint.sh

EXPOSE 8787

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["python3", "dashboard/server.py"]
