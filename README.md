# Dark Vessel Detection

Maritime OSINT platform that fuses Sentinel-1 SAR imagery with public AIS data to flag "dark vessels" — ships visible in satellite imagery but not broadcasting AIS, indicating potentially illicit or military activity.

> Portfolio project. All data sources are public and legal.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), GeoAlchemy2, PostGIS 3.4 / PostgreSQL 16
- **Frontend:** Vite 5, React 18, TypeScript, react-leaflet, TanStack Query
- **Infra:** Docker Compose (local), Railway (backend), Vercel (frontend)

## Prerequisites

- Docker Desktop
- Node.js 20 + pnpm (`npm i -g pnpm`)
- Python 3.12 — only needed if running the backend natively outside Docker

## Quickstart

**Start the backend + database:**
```bash
docker compose up --build
```

Verify it's healthy:
```bash
curl http://localhost:8000/api/health   # {"status":"ok"}
```

**Start the frontend** (native, faster hot-reload):
```bash
cd frontend
pnpm install   # first time only
pnpm dev       # http://localhost:5173
```

Or spin everything up via Compose:
```bash
docker compose --profile frontend up --build
```

## Commands

**Compose**
- `docker compose up` — backend (:8000) + PostGIS (:5432)
- `docker compose up --build` — rebuild images first
- `docker compose up -d` — run in background (detached)
- `docker compose --profile frontend up` — also start Vite dev server (:5173)
- `docker compose down` — stop and remove containers
- `docker compose down -v` — also wipe the postgres_data volume (fresh DB)
- `docker compose logs backend -f` — tail backend logs
- `docker compose logs frontend -f` — tail frontend logs
- `docker compose restart backend` — restart the API without full teardown
- `docker compose exec db psql -U dvd -d dvd` — open a psql shell

**Frontend**
- `pnpm dev` — Vite dev server (:5173), proxies `/api` → backend
- `pnpm build` — production build to `dist/`
- `pnpm preview` — preview the production build locally

## Environment variables

Compose sets these automatically. For native dev, copy `.env.example` → `.env` in `backend/`:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://dvd:dvd@db:5432/dvd` | Async Postgres connection string |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `ENV` | `development` | Environment name |

## Project layout

```
backend/app/
  main.py      FastAPI app — CORS, health endpoint, vessels endpoint
  config.py    Settings loaded from environment (pydantic-settings)
  database.py  Async engine, session dependency, SQLAlchemy Base
  models.py    Database models
  ais.py       AIS ingestion pipeline
  sar.py       Sentinel-1 SAR vessel detection (YOLOv8) — via CDSE
  fusion.py    SAR ↔ AIS dark-vessel fusion
frontend/src/
  App.tsx      Root component
  api.ts       Typed fetch wrapper for /api
docs/
  scaffold-smoke-test.md   Verified boot results and known issues
```
