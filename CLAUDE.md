# Dark Vessel Detection

Maritime OSINT platform that detects ships in satellite optical imagery and cross-references against live AIS data to flag "dark vessels" — ships visible in imagery but not broadcasting AIS. Portfolio project; all data sources are public and legal.

## Repo state

Scaffold complete and smoke-tested (2026-05-09). FastAPI backend, Vite + React frontend, and docker-compose are all wired up and passing. AIS ingestion landed and verified end-to-end (2026-05-10). Multi-ROI ingestion landed (2026-05-18) — a single AISStream subscription now covers all 4 ROIs simultaneously; vessel endpoints take a `?roi=` query param (frontend selector still to be wired). AIS durability hardening landed (2026-05-18) — bounded DB-write retry in `_flush`/`_upsert_ship_metadata` so transient Postgres hiccups no longer tear down the WebSocket, plus an in-memory per-source health tracker (`app/sources.py`) surfaced via an enriched `/api/health` and a `SOURCE_STALE_AFTER_SECONDS` knob. Planet Labs PlanetScope API access acquired (2026-05-18) — primary optical source is ready to wire up. See `docs/scaffold-smoke-test.md` for verified scaffold results.

Current phase: **weeks 1–3** — AIS ingestion is live and hardened; remaining: YOLOv8 fine-tune on HRSC2016.

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

**Backend tests (native via venv)**
- `cd backend && .venv/bin/pip install -r requirements-dev.txt` — install pytest deps (first time)
- `.venv/bin/pytest` — run unit suite. Pure-function tests only; live AIS / DB tests are intentionally out of scope here (would belong in a separate integration suite).

Lint and CI are not yet configured — document here when added.

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
    models.py           # SQLAlchemy models — AISPosition (geography, mmsi+time index)
    ais.py              # Pure parsing helpers — AISStream PositionReport → ParsedPosition
    ingest.py           # Long-running tasks — run_ingest (WebSocket) + run_retention (hourly prune)
    rois.py             # Static ROI registry; AISStream subscribes to all ROIs at once
    sources.py          # In-memory per-source health tracker; surfaced at /api/health
    optical.py          # Optical vessel detection via YOLOv8 (stub)
                        #   active source: Planet Labs PlanetScope (primary)
    fusion.py           # Optical detections ↔ AIS cross-reference — stub
  tests/                # pytest suite — pure-function tests for ais.py, rois.py, sources.py
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

1. **AIS ingestion** (`ais.py`) — AISStream WebSocket filtered by the union of every ROI's bounding box; positions streamed continuously into PostGIS. No polling — persistent connection, positions arrive as vessels broadcast. Switching the frontend's selected ROI is a pure view filter — ingestion never narrows.
2. **Optical detection** (`optical.py`) — YOLOv8 inference on satellite imagery chips; returns vessel centroids + confidence. Source: Planet Labs PlanetScope (3–5m, daily revisit) via `PLANET_API_KEY`.
3. **Fusion** (`fusion.py`) — optical detections cross-referenced against AIS buffer; flagged dark if no AIS match within 500m / 2h; use `ST_DWithin` in SQL — do not reimplement in Python.
4. **Surface** (frontend) — react-leaflet map, ROI selector, vessel markers, AIS tracks, imagery footprints, timeline scrubber.

## Future features

- Confidence levels
  - Dark vessels should be marked with confidence levels based on model confidence

## Constraints

- **PostGIS everywhere** — SQLite acceptable in week 1 only; never in Docker or production paths
- **Compose mirrors prod** — Railway/Supabase deploy must be a config swap, not a rewrite; push back on prod-only code paths
- **CORS from env** — `localhost:5173` in dev, Vercel domain in prod; never hardcoded
- **No imagery in repo** — `.gitignore` excludes `*.tif`, `*.geotiff`, `*.nc`, `data/`; redirect any tool that tries to write imagery to `data/`
- **`.env` never committed** — `.env.example` is the contract; update it when adding env vars
- **`--reload` for dev only** — the Dockerfile CMD omits it; compose overrides the command for local dev
- **Optical source via env** — `optical.py` reads `PLANET_API_KEY` to enable Planet PlanetScope; the future SAR layer will follow the same pattern via CDSE OAuth credentials

## External APIs

- **AISStream** (AIS) — free beta, WebSocket, sign up at aisstream.io with GitHub
- **Planet Labs PlanetScope** (optical, primary) — access acquired 2026-05-18; set `PLANET_API_KEY` to enable
- **Sentinel-1 SAR via Copernicus Data Space Ecosystem** (future, optional) — different sensor from Sentinel-2 (radar, not optical); adds all-weather/night coverage. OAuth2 client credentials at dataspace.copernicus.eu; `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` slots already in `config.py` for when this lands. Deferred — implement if time permits after weeks 7–9.

## Phase context

- **Weeks 1–3 (current):** AIS ingestion (AISStream WebSocket → PostGIS) + YOLOv8 fine-tune on HRSC2016
- **Weeks 4–6:** Optical pipeline (Planet PlanetScope imagery → YOLOv8 inference → detections), fusion logic
- **Weeks 7–9:** Leaflet UI (ROI selector, vessel markers, tracks), FastAPI endpoints, Vercel + Railway deploy
- **Weeks 10–12:** caching, README polish, demo video; SAR layer if time permits

Flag scope-creep if a request pulls work forward out of phase.
