# Dark Vessel Detection

Maritime OSINT platform that detects ships in satellite optical imagery and cross-references against live AIS data to flag "dark vessels" — ships visible in imagery but not broadcasting AIS. Portfolio project; all data sources are public and legal.

## Repo state

Scaffold complete and smoke-tested (2026-05-09). FastAPI backend, Vite + React frontend, and docker-compose are all wired up and passing. See `docs/scaffold-smoke-test.md` for verified results.

Current phase: **weeks 1–3** — AIS ingestion and YOLOv8 fine-tune on HRSC2016.

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
    models.py           # SQLAlchemy models (AISPosition lands here in weeks 1-3)
    ais.py              # AIS ingestion — AISStream WebSocket (stub)
    optical.py          # Optical vessel detection via YOLOv8 (stub)
                        #   active source: Sentinel-2 (interim) → Planet Labs (when key set)
    fusion.py           # Optical detections ↔ AIS cross-reference — stub
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

Four-stage pipeline — understand this before touching `backend/app/`:

1. **AIS ingestion** (`ais.py`) — AISStream WebSocket filtered by ROI bounding box; positions streamed continuously into PostGIS. No polling — persistent connection, positions arrive as vessels broadcast.
2. **Optical detection** (`optical.py`) — YOLOv8 inference on satellite imagery chips; returns vessel centroids + confidence. Active source is config-driven:
   - `PLANET_API_KEY` set → Planet Labs PlanetScope (3–5m, daily revisit) — primary, access pending
   - `CDSE_CLIENT_ID` set → Sentinel-2 (10m, ~5-day revisit) — interim fallback, free via CDSE
3. **Fusion** (`fusion.py`) — optical detections cross-referenced against AIS buffer; flagged dark if no AIS match within 500m / 2h; use `ST_DWithin` in SQL — do not reimplement in Python.
4. **Surface** (frontend) — react-leaflet map, ROI selector, vessel markers, AIS tracks, imagery footprints, timeline scrubber.

## ROIs (Regions of Interest)

Users select a named ROI from the frontend. The selected ROI's bounding box drives both the AISStream subscription filter and the imagery query. Predefined ROIs live in the backend as a static config (not DB). Examples:

- South China Sea
- Strait of Hormuz
- Gulf of Guinea
- Eastern Mediterranean

AISStream supports live bounding box filter updates on the same WebSocket connection — no reconnect needed when the user switches ROI.

## Constraints

- **PostGIS everywhere** — SQLite acceptable in week 1 only; never in Docker or production paths
- **Compose mirrors prod** — Railway/Supabase deploy must be a config swap, not a rewrite; push back on prod-only code paths
- **CORS from env** — `localhost:5173` in dev, Vercel domain in prod; never hardcoded
- **No imagery in repo** — `.gitignore` excludes `*.tif`, `*.geotiff`, `*.nc`, `data/`; redirect any tool that tries to write imagery to `data/`
- **`.env` never committed** — `.env.example` is the contract; update it when adding env vars
- **`--reload` for dev only** — the Dockerfile CMD omits it; compose overrides the command for local dev
- **Optical source is config-driven** — `optical.py` checks for `PLANET_API_KEY` first, falls back to CDSE/Sentinel-2; no hardcoded source

## External APIs

- **AISStream** (AIS) — free beta, WebSocket, sign up at aisstream.io with GitHub
- **Copernicus Data Space Ecosystem / Sentinel-2** (optical, interim) — free (10k requests/month), OAuth2 client credentials, register at dataspace.copernicus.eu
- **Planet Labs PlanetScope** (optical, primary) — access pending approval; will replace Sentinel-2 when `PLANET_API_KEY` is set
- **SAR (future, optional)** — Sentinel-1 via CDSE; same credentials as Sentinel-2. Adds all-weather/night coverage. Deferred — implement if time permits after weeks 7–9.

## Phase context

- **Weeks 1–3 (current):** AIS ingestion (AISStream WebSocket → PostGIS) + YOLOv8 fine-tune on HRSC2016
- **Weeks 4–6:** Optical pipeline (Sentinel-2 imagery → YOLOv8 inference → detections), fusion logic
- **Weeks 7–9:** Leaflet UI (ROI selector, vessel markers, tracks), FastAPI endpoints, Vercel + Railway deploy
- **Weeks 10–12:** caching, README polish, demo video; SAR layer if time permits

Flag scope-creep if a request pulls work forward out of phase.
