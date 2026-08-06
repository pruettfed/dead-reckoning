# Build context is the repository root, not backend/ — the SPA is built here and
# served by the API so the deployment has one origin and one service. See
# app/spa.py for why that is worth a two-stage build.
FROM node:20-alpine AS frontend

RUN corepack enable
WORKDIR /build

# Lockfile first: the dependency install is the slow layer and only needs to
# rerun when the lockfile moves, not on every source edit.
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build


FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/requirements-ml.txt .
# headless opencv: ultralytics pulls opencv-python, whose GUI deps (libxcb etc.)
# don't exist in slim images
RUN pip install --no-cache-dir -r requirements-ml.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir opencv-python-headless

COPY backend/app ./app
# The detector checkpoint. Without it the scheduler reports idle and the SAR
# half of the app silently does nothing, so it ships in the image rather than
# relying on the bind mount that only exists under compose.
COPY backend/models ./models
COPY --from=frontend /build/dist ./static

# Nothing here needs root, and this container writes nothing to its filesystem.
RUN useradd --system --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

# Shell form so ${PORT} expands: hosting platforms assign the port at runtime and
# the exec form has no shell to substitute it. --proxy-headers makes
# request.client.host the real caller behind the platform's load balancer, which
# is what the rate limiter meters on.
#
# One worker, deliberately. The scheduler, AIS ingest and retention sweep are
# lifespan tasks inside this process: a second worker would open a second
# AISStream subscription and race the PU ledger.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
