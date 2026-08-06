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
- **Infra:** Docker Compose (local), Railway (production — API and SPA on one origin, PostGIS alongside)

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
3. `ANALYSIS_API_KEY` — a secret of at least 32 characters (production refuses to boot on a weaker one); callers pass it as `X-Analysis-Key`. Only used outside production.

Then drop a trained checkpoint at `backend/models/sar_ship.pt` — the full Colab fine-tune runbook is in [`ml/README.md`](ml/README.md). The `backend/models/` directory is volume-mounted, so no rebuild is needed.

Analysis is scheduled automatically; the frontend has no trigger and holds no key. To force a run:
```bash
# any environment — the only way in production
cd backend && .venv/bin/python scripts/analyze.py north_taiwan

# outside production, the same thing over HTTP (404 when ENV=production)
curl -X POST -H "X-Analysis-Key: $ANALYSIS_API_KEY" \
  http://localhost:8000/api/analysis/north_taiwan
```

Production exposes **no endpoint that spends Processing Units** — a network-reachable spend button bypasses `PU_MONTHLY_CEILING`, and a scene that fails after its pixel fetch would be re-bought on every retry.

Full endpoint reference: [`docs/api.md`](docs/api.md).

### Environments

`ENV` selects the posture and **defaults to `production`**, so a forgotten value fails closed.

| | `development` / `staging` | `production` |
|---|---|---|
| `/docs`, `/redoc`, `/openapi.json` | served | **404** |
| CORS | all methods | `GET`, `OPTIONS` only |
| `/api/dev/*` reset endpoints | available when enabled | **never registered** |
| `POST /api/analysis/{roi}` (spends PU) | available | **never registered** |
| `DEVTOOLS_ENABLED=true` | allowed | **refuses to boot** |
| Weak `ANALYSIS_API_KEY` (<32 chars) | allowed | **refuses to boot** |

Credentials are never echoed: any configured secret appearing in a connection error is redacted before `/api/health` serves it.

### Developer reset tools

Reset SAR scenes, AIS data, or the PU ledger while iterating. Locally, use the CLI — it talks to the database directly and needs no key:

```bash
cd backend
.venv/bin/python scripts/dev_reset.py pu --show
.venv/bin/python scripts/dev_reset.py scenes --roi north_taiwan --dry-run
.venv/bin/python scripts/dev_reset.py ais
```

The same operations are exposed over HTTP at `/api/dev/*` for a remote non-production deploy, gated by `DEVTOOLS_ENABLED=true` and a `DEVTOOLS_API_KEY` of at least 32 characters. See [`docs/api.md`](docs/api.md).

**Deleting scenes re-spends PU** — the scheduler treats the pass as new and re-fetches it. Set `SCHEDULER_ENABLED=false` first if you don't want that.

## Deploying

Production runs as **two Railway services**: `web` (this image) and `db`
(`postgis/postgis:16-3.4` with a volume at `/var/lib/postgresql/data`). Railway's
stock Postgres has no PostGIS, which is not optional here — four tables carry
`Geography` columns and fusion is `ST_*` throughout.

The image builds the SPA and serves it from the API process, so there is **one
origin and one public hostname**. That is the security posture, not a
convenience: the browser never makes a cross-origin request, so CORS stops being
load-bearing, and the API has no address of its own for anyone to find. A public
SPA cannot hold a secret, so there is deliberately no API token — what protects
the surface instead is rate limiting, security headers, response models that
whitelist every field, and the fact that production registers no PU-spending or
destructive route at all.

**Before the first deploy**

1. Put the detector checkpoint at `backend/models/sar_ship.pt` and commit it.
   `.gitignore` allows that one path. Without it the scheduler reports
   `idle: model checkpoint not found`, the API and AIS ingest work fine, and the
   entire SAR half of the app silently does nothing.
2. Point `dark-vessel.pruettfed.com` at the `web` service. Railway issues a CNAME
   and a TXT record to add at the registrar and provisions TLS itself.

**Environment variables on the `web` service**

```
ENV=production
DATABASE_URL=${{Postgres.DATABASE_URL}}     # Railway reference; the scheme is normalized
CORS_ORIGINS=https://dark-vessel.pruettfed.com
ALLOWED_HOSTS=dark-vessel.pruettfed.com
AISSTREAM_API_KEY=...
CDSE_CLIENT_ID=...
CDSE_CLIENT_SECRET=...
LOG_LEVEL=INFO
```

Do **not** copy `.env.example` verbatim — it sets `DEVTOOLS_ENABLED=true`, and
`ENV=production` refuses to boot with that. The refusal is the feature; a
production process must not quietly serve a destructive surface.

**What happens on first boot**

The lifespan waits for Postgres (a platform has no `depends_on`, so the app
routinely starts first), creates the PostGIS extension, creates the tables, and
loads the bundled coastline. The scheduler then **holds every region** until the
AIS buffer reaches `SCHEDULER_WARMUP_HOURS` of depth — six hours by default,
capped at eight. Expect the region rail to read "AIS warm-up" for that window on
a genuinely fresh database; a redeploy onto an existing one starts immediately.

**Forcing an analysis in production**

Production exposes no PU-spending endpoint at all — `POST /api/analysis/{roi}`
404s there even with a valid key. Forcing a run is a shell action:

```bash
railway run python scripts/analyze.py north_taiwan
```

**Cost**

The registry runs ~152 analyses/month, about 13 hours of work — a 1.7% duty
cycle. Detection runs in a subprocess that exits when it is done, so torch is
resident for that 1.7% rather than all month; on usage-metered hosting that is
the difference between roughly $17/mo and roughly $8.50/mo. Keep it that way:
loading the model in the API process undoes it. See `app/detect_worker.py`.

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
- `pnpm build` — production build to `dist/` (the Docker image runs this and serves the result)
- `pnpm preview` — preview the production build locally

## Environment variables

Compose sets the first three automatically. For native dev (and for all secrets), copy `.env.example` → `.env` in `backend/`:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://dvd:dvd@db:5432/dvd` | Async Postgres connection string |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `ENV` | `production` | `development`, `staging` or `production` — see [Environments](#environments) |
| `AISSTREAM_API_KEY` | — | AIS WebSocket key (ingest disabled without it) |
| `AIS_RETENTION_DAYS` | `2` | Rolling AIS history window |
| `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` | — | Copernicus OAuth2 credentials for pixel fetch |
| `ANALYSIS_API_KEY` | — | Shared secret gating `POST /api/analysis/{roi}` (non-production only) |
| `DEVTOOLS_ENABLED` | `false` | Register `/api/dev/*`. Forbidden when `ENV=production` |
| `DEVTOOLS_API_KEY` | — | Shared secret gating `/api/dev/*`; ≥32 chars or the router is skipped |
| `MODEL_PATH` | `models/sar_ship.pt` | YOLOv8 checkpoint path |

The complete contract lives in [`backend/.env.example`](backend/.env.example).

## Acknowledgements

This project builds on public data and research from the following sources.

- **AIS positions** — [AISStream](https://aisstream.io), a free real-time AIS WebSocket feed.
- **SAR imagery** — Contains modified Copernicus Sentinel data, via the [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu) (European Space Agency).
- **Detection model training data** — the YOLOv8 checkpoint (`backend/models/sar_ship.pt`) was fine-tuned on:
  - Cao, T.-T., Luckett, C., Williams, J., Cooke, T., Yip, B., Rajagopalan, A., Wong, S. "SARFish: Space-Based Maritime Surveillance Using Complex Synthetic Aperture Radar Imagery." *2022 International Conference on Digital Image Computing: Techniques and Applications (DICTA)*, IEEE. [doi:10.1109/DICTA56598.2022.10034640](https://doi.org/10.1109/DICTA56598.2022.10034640). Dataset (Apache 2.0): [ConnorLuckettDSTG/SARFish on HuggingFace](https://huggingface.co/datasets/ConnorLuckettDSTG/SARFish).
  - Paolo, F., Lin, T.-t. T., Gupta, R., Goodman, B., Patel, N., Kuster, D., Kroodsma, D., Dunnmon, J. "xView3-SAR: Detecting Dark Fishing Activity Using Synthetic Aperture Radar Imagery." *NeurIPS 2022 Datasets and Benchmarks Track*. [arXiv:2206.00897](https://arxiv.org/abs/2206.00897). Challenge hosted by the Defense Innovation Unit and Global Fishing Watch: [iuu.xview.us](https://iuu.xview.us).

See [`ml/README.md`](ml/README.md) for why these datasets were chosen for the fine-tune.
