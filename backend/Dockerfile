# Build context is the repo root — this stage builds the SPA served by the API. See app/spa.py.
FROM node:20-alpine AS frontend

RUN corepack enable
WORKDIR /build

# Lockfile first so dependency install caches across source edits.
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
# Checkpoint ships in the image; without it the scheduler reports idle.
COPY backend/models ./models
COPY --from=frontend /build/dist ./static

RUN useradd --system --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

# Shell form so ${PORT} expands at runtime. --proxy-headers makes
# request.client.host real behind the platform's load balancer.
# One worker: the scheduler and AIS ingest are lifespan tasks in this process.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
