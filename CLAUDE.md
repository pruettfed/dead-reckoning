# Dark Vessel Detection

Maritime OSINT platform that fuses Sentinel-1 SAR imagery with public AIS data to flag "dark vessels" — ships visible in satellite imagery but not broadcasting AIS. Portfolio project; all data sources are public and legal.

## Repo state

Scaffold complete and smoke-tested (2026-05-09). FastAPI backend, Vite + React frontend, and docker-compose are all wired up and passing. See `docs/scaffold-smoke-test.md` for verified results.

Current phase: **weeks 1–3** — AIS ingestion and YOLOv8 fine-tune. No imagery pipeline yet.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), asyncpg, GeoAlchemy2
- **Frontend:** Vite 5, React 18, TypeScript, react-leaflet, TanStack Query, pnpm
- **Database:** PostGIS 3.4 on PostgreSQL 16 (`postgis/postgis:16-3.4`)
- **Deploy target:** Railway (backend), Vercel (frontend)

## Commands

**Docker Compose**
- `docker compose up` — backend (:8000) + PostGIS (:5432)
- `docker compose up --build` — rebuild images first
- `docker compose --profile frontend up` — also start Vite dev server (:5173)
- `docker compose down` — stop and remove containers
- `docker compose down -v` — also wipe the `postgres_data` volume
- `docker compose logs backend -f` — tail backend logs
- `docker compose exec db psql -U dvd -d dvd` — open psql shell
- `docker compose restart backend` — hot-restart the API (picks up code changes outside the bind mount)

**Frontend (native — faster HMR than the container)**
- `cd frontend && pnpm install` — install deps (first time or after package.json changes)
- `pnpm dev` — Vite dev server (:5173), proxies `/api` → `localhost:8000`

Tests, lint, and CI are not yet configured — document here when added.

## Layout

```
CLAUDE.md
README.md
docker-compose.yml
backend/
  Dockerfile            # production-clean (no --reload); compose overrides CMD for dev
  requirements.txt      # lean API deps only; ML deps deferred to requirements-ml.txt
  .env.example          # env var contract — update here whenever a new var is added
  app/
    main.py             # FastAPI app, CORS middleware, lifespan (create_all), endpoints
    config.py           # pydantic-settings Settings; NoDecode on CORS_ORIGINS list field
    database.py         # async engine, sessionmaker, Base, get_session() dependency
    models.py           # SQLAlchemy models (empty until AIS Position lands)
    ais.py              # AIS ingestion — stub
    sar.py              # SAR detection — stub
    planet.py           # optical classification (YOLOv8) — stub
    fusion.py           # satellite ↔ AIS fusion — stub
frontend/
  Dockerfile            # used by --profile frontend only; runs pnpm dev inside container
  package.json          # packageManager: pnpm
  vite.config.ts        # /api proxy → VITE_API_PROXY_TARGET ?? localhost:8000
  src/
    main.tsx            # React root, QueryClientProvider
    App.tsx             # health + vessels fetch via TanStack Query (proof of proxy)
    api.ts              # apiGet<T>() fetch wrapper
docs/
  scaffold-smoke-test.md
```

## Architecture

Five-stage fusion pipeline — understand this before touching `backend/app/`:

1. **SAR detection** (`sar.py`) — Sentinel-1 via Copernicus; backscatter thresholding (works through clouds and at night)
2. **Optical classification** (`planet.py`) — PlanetScope (3–5m, daily revisit); fine-tuned YOLOv8 on patches
3. **AIS ingestion** (`ais.py`) — MarineTraffic / AISHub polling for positions in the bounding box; persisted to PostGIS
4. **Fusion** (`fusion.py`) — satellite detections cross-referenced against AIS; flagged dark if no match within 500m / 2h; use `ST_DWithin` in SQL — do not reimplement in Python
5. **Surface** (frontend) — react-leaflet map, vessel markers, AIS tracks, satellite footprints, timeline scrubber

AIS polling cadence = satellite revisit rate (~6h). Polling faster wastes API quota with no analytical benefit.

## Constraints

- **PostGIS everywhere** — SQLite acceptable in week 1 only; never in Docker or production paths
- **Compose mirrors prod** — Railway/Supabase deploy must be a config swap, not a rewrite; push back on prod-only code paths
- **CORS from env** — `localhost:5173` in dev, Vercel domain in prod; never hardcoded
- **No imagery in repo** — `.gitignore` excludes `*.tif`, `*.geotiff`, `*.nc`, `data/`; redirect any tool that tries to write imagery to `data/`
- **`.env` never committed** — `.env.example` is the contract; update it when adding env vars
- **`--reload` for dev only** — the Dockerfile CMD omits it; compose overrides the command for local dev

## External APIs

- **Copernicus** (Sentinel-1 SAR) — free, available now
- **MarineTraffic / AISHub** (AIS) — free tier, available now
- **Planet Labs** (PlanetScope optical) — access pending (~3 weeks); don't block SAR or AIS work
  - Fallback 1: Google Earth Engine (apply in parallel — faster approval)
  - Fallback 2: Sentinel-2 at 10m (reframe CV to size-category; lean on SAR + AIS gap)
  - Fallback 3: Umbra open SAR archive (1m)

## Phase context

- **Weeks 1–3 (current):** YOLOv8 fine-tune on HRSC2016, AIS ingestion
- **Weeks 4–6:** SAR layer, Planet inference, fusion logic (week 6 = highest-risk)
- **Weeks 7–9:** Leaflet UI, FastAPI endpoints, Vercel + Railway deploy
- **Weeks 10–12:** caching, README polish, demo video

Flag scope-creep if a request pulls work forward out of phase.
