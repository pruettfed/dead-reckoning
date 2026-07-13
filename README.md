# Dark Vessel Detection

Maritime OSINT platform that fuses Sentinel-1 SAR imagery with public AIS data to flag "dark vessels" — ships visible in satellite imagery but not broadcasting AIS, indicating potentially illicit or military activity.

> Portfolio project. All data sources are public and legal.

## How it works

1. **AIS ingestion** — an always-on AISStream WebSocket streams live vessel positions for six ROIs into PostGIS: Singapore Strait, North Taiwan / ECS, Gulf of Finland, Skagen (Kattegat), Bosphorus approaches, and Malta's Hurd Bank. Each pairs a dark-vessel narrative (shadow fleet, gray-zone activity, dark STS transfers) with probe-verified AISStream receiver coverage — see [`docs/ais-coverage.md`](docs/ais-coverage.md).
2. **SAR detection** — on demand, the newest Sentinel-1 pass over an ROI is fetched as calibrated radar chips (Sentinel Hub Process API via Copernicus CDSE) and run through a YOLOv8 model fine-tuned on a SAR ship dataset.
3. **Fusion** — each detected hull is cross-referenced against AIS at the acquisition timestamp (`ST_DWithin`, 500 m / ±2 h). No match → **dark vessel**, with a confidence level from the model.
4. **Map UI** — react-leaflet map with live vessels, tracks, SAR footprints, and dark/matched detection markers.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), GeoAlchemy2, PostGIS 3.4 / PostgreSQL 16, ultralytics YOLOv8 (CPU inference)
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
curl http://localhost:8000/api/health   # {"status":"ok","sources":{...}}
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

### Enabling SAR analysis

Analysis is optional and admin-gated (it spends Copernicus Processing Units). Three ingredients, all in `backend/.env`:

1. `AISSTREAM_API_KEY` — free at [aisstream.io](https://aisstream.io); without it there is no AIS buffer to fuse against.
2. `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` — OAuth2 client credentials from [dataspace.copernicus.eu](https://dataspace.copernicus.eu).
3. `ANALYSIS_API_KEY` — any secret string; callers pass it as `X-Analysis-Key`.

Then drop a trained checkpoint at `backend/models/sar_ship.pt` — the full Colab fine-tune runbook is in [`ml/README.md`](ml/README.md). The `backend/models/` directory is volume-mounted, so no rebuild is needed.

Trigger an analysis (or set `VITE_ANALYSIS_API_KEY` in `frontend/.env` to get a dev-only button in the UI):
```bash
curl -X POST -H "X-Analysis-Key: $ANALYSIS_API_KEY" \
  http://localhost:8000/api/analysis/singapore_strait
```

Full endpoint reference: [`docs/api.md`](docs/api.md).

## Commands

**Compose**
- `docker compose up` — backend (:8000) + PostGIS (:5432)
- `docker compose up --build` — rebuild images first
- `docker compose up -d` — run in background (detached)
- `docker compose --profile frontend up` — also start Vite dev server (:5173)
- `docker compose down` — stop and remove containers
- `docker compose down -v` — also wipe the postgres_data volume (fresh DB; required after schema changes — tables are created at startup, not migrated)
- `docker compose logs backend -f` — tail backend logs
- `docker compose logs frontend -f` — tail frontend logs
- `docker compose restart backend` — restart the API without full teardown
- `docker compose exec db psql -U dvd -d dvd` — open a psql shell

**Frontend**
- `pnpm dev` — Vite dev server (:5173), proxies `/api` → backend
- `pnpm build` — production build to `dist/`
- `pnpm preview` — preview the production build locally

## Environment variables

Compose sets the first three automatically. For native dev (and for all secrets), copy `.env.example` → `.env` in `backend/`:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://dvd:dvd@db:5432/dvd` | Async Postgres connection string |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `ENV` | `development` | Environment name |
| `AISSTREAM_API_KEY` | — | AIS WebSocket key (ingest disabled without it) |
| `AIS_RETENTION_DAYS` | `2` | Rolling AIS history window |
| `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` | — | Copernicus OAuth2 credentials for pixel fetch |
| `ANALYSIS_API_KEY` | — | Shared secret gating `POST /api/analysis/{roi}` |
| `MODEL_PATH` | `models/sar_ship.pt` | YOLOv8 checkpoint path |

The complete contract lives in [`backend/.env.example`](backend/.env.example).
