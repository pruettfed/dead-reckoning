# Dark Vessel Detection

Maritime OSINT platform: detect ships in Sentinel-1 SAR (radar) imagery, cross-reference live AIS, flag "dark vessels" (visible in radar, not broadcasting AIS). Each SAR pass is an independent snapshot correlated against AIS at its acquisition time.

- **Deeper docs:** `docs/architecture.md` (pipeline detail + file map — read before touching `backend/app/`), `README.md`, `docs/api.md`, `docs/ais-coverage.md`.

## Pipeline

1. **AIS ingest** (`ais.py`, `ingest.py`) — AISStream WebSocket → PostGIS, continuous; switching the frontend ROI is a view filter only, never narrows ingestion.
2. **SAR detect** (`sar.py`, `detect.py`) — newest Sentinel-1 IW GRDH pass fetched as VV chips (Process API, tiled), run through tiled YOLOv8 → centroids + confidence (high/med/low).
3. **Fusion** (`fusion.py`) — `ST_DWithin` match at the scene's acquisition time; dark = no AIS within 500 m / 2 h, clipped to ROI ∩ image-footprint.
4. **Surface** (frontend) — react-leaflet; selecting a scene freezes the vessel layer at its time (scene = time control); triggering analysis is admin-only.

## Status (2026-07-12) — phase weeks 4–6 wrap-up

- Coded: AIS ingestion, full pipeline (SAR fetch → YOLOv8 → fusion → analysis endpoints), react-leaflet UI skeleton.
- Blocked on 3 user inputs; analysis endpoint 503s until each present: checkpoint `backend/models/sar_ship.pt` (runbook `ml/README.md`), `CDSE_CLIENT_ID`/`CDSE_CLIENT_SECRET`, `ANALYSIS_API_KEY`.
- Next: run Colab fine-tune, then verify one live analysis on `singapore_strait`.
- Later (flag scope-creep if pulled forward): UI design pass, Railway/Vercel deploy (torch +~1.2 GB, needs image-sizing), README/demo polish.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy async, asyncpg, GeoAlchemy2. **DB:** PostGIS 3.4 / PostgreSQL 16.
- **Frontend:** Vite 5, React 18, TS, react-leaflet, TanStack Query, pnpm. **Deploy:** Railway + Vercel.

## Commands

- `docker compose up [--build]` — backend (:8000) + PostGIS (:5432); `--profile frontend up` also starts Vite (:5173).
- `docker compose down [-v]` — stop; `-v` wipes the `postgres_data` volume.
- `docker compose logs backend -f` · `exec db psql -U dvd -d dvd` · `restart backend` (pick up code changes).
- Frontend native (faster HMR): `cd frontend && pnpm install && pnpm dev` — :5173, proxies `/api` → :8000.
- Backend tests: `cd backend && .venv/bin/pytest` — pure-function suite only (no DB/network/torch).
- ML training: Colab GPU only, never local (runbook `ml/README.md`). Inference deps `backend/requirements-ml.txt`.
- Lint/CI: not configured — document here when added.

## Constraints (correctness / budget guardrails)

- **PostGIS everywhere** — never SQLite in Docker or prod.
- **Fusion in SQL** — `ST_DWithin`, 500 m / 2 h; never reimplement distance in Python.
- **Clip to ROI ∩ footprint** — a detection outside the imaged footprint is unobserved, not dark. Never mosaic passes for correlation (different times = vessels moved).
- **PU budget 30,000/mo** — only pixel fetch spends PU (~100/analysis); catalog search free. Scenes DB-cached (re-analysis = 0 PU). Never discover via Copernicus Browser (burns PU).
- **Analysis admin-gated** — `POST /api/analysis/{roi}` needs `X-Analysis-Key`; all availability checks run before any PU is spent.
- **ROIs water-centered, ≤ ~250 km** — one Sentinel-1 IW swath; keeps land returns out of detection.
- **Config from env** — CORS, CDSE creds; never hardcoded. `.env` never committed; `.env.example` is the contract. No imagery in repo (`.gitignore` excludes `*.tif`/`*.geotiff`/`*.nc`/`data/`).
- **Compose mirrors prod** — deploy is a config swap, not a rewrite; `--reload` is dev-only.

## AIS coverage (verified 2026-07-12)

- AISStream is terrestrial-receiver-based → gray-zone ROIs were silent. Live ROIs: `singapore_strait`, `north_taiwan`, `gulf_of_finland`, `skagen_kattegat`, `bosphorus_marmara`, `malta_hurds_bank`. Re-probe before adding any (`docs/ais-coverage.md`).
- One subscription covers all ROI bboxes; analysis in an ROI with no AIS data is refused (409) so fusion can't mark everything falsely dark.
