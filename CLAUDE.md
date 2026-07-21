# Dark Vessel Detection

Maritime OSINT platform: detect ships in Sentinel-1 SAR (radar) imagery, cross-reference live AIS, flag "dark vessels" (visible in radar, not broadcasting AIS). Each SAR pass is an independent snapshot correlated against AIS at its acquisition time.

- **Deeper docs:** `docs/architecture.md` (pipeline detail + file map — read before touching `backend/app/`), `README.md`, `docs/api.md`, `docs/ais-coverage.md`.

## Pipeline

1. **AIS ingest** (`ais.py`, `ingest.py`) — AISStream WebSocket → PostGIS, continuous; switching the frontend ROI is a view filter only, never narrows ingestion.
2. **SAR detect** (`sar.py`, `detect.py`) — newest Sentinel-1 IW GRDH pass fetched as VV chips (Process API, tiled), run through tiled YOLOv8 → centroids + confidence (high/med/low).
3. **Land mask** (`landmask.py`) — detections inside `land_polygons` flagged `on_land`, excluded from fusion and counts. Load with `scripts/load_land.py` (0 PU).
4. **Fusion** (`fusion.py`) — `ST_DWithin` match at the scene's acquisition time; dark = no AIS within 500 m / 2 h, clipped to `sar_bbox` ∩ image-footprint. Skipped entirely for `survey` ROIs (`is_dark` stays NULL).
5. **Surface** (frontend) — react-leaflet; selecting a scene freezes the vessel layer at its time (scene = time control); triggering analysis is admin-only. The map draws both ROI boxes and drapes the stored SAR overview under the detections.

## Status (2026-07-20) — region rework landed

- Coded: AIS ingestion, full pipeline (SAR fetch → YOLOv8 → fusion → analysis endpoints), react-leaflet UI, 15-region registry with the fused/survey split, SAR overview retention + map overlay.
- **Schema changed** (`imaged_bbox`, `overview_png` on `sar_scenes`) and there is no Alembic — `create_all` won't alter live tables. Run `docker compose down -v` before the next `up`.
- Blocked on 3 user inputs; analysis endpoint 503s until each present: checkpoint `backend/models/sar_ship.pt` (runbook `ml/README.md`), `CDSE_CLIENT_ID`/`CDSE_CLIENT_SECRET`, `ANALYSIS_API_KEY`.
- Next: run Colab fine-tune → one live analysis on `singapore_strait` (~55 PU, fused control case) → one on `hormuz_strait` (~49 PU, survey path). Re-probe all 15 `ais_bbox`es when AISStream is back up.
- Later (flag scope-creep if pulled forward): UI design pass, Railway/Vercel deploy (torch +~1.2 GB, needs image-sizing), README/demo polish. A 0-PU GRD/COG fetch backend is researched and deliberately deferred as a *fallback only* — see the deferred section in `.claude/plans/ok-i-want-to-generic-stallman.md`.

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
- **PU budget 30,000/mo** — only pixel fetch spends PU (~18–107/analysis, scales with `sar_bbox` area — see `estimate_pu`); catalog search free. Scenes DB-cached (re-analysis = 0 PU). Never discover via Copernicus Browser (burns PU). Cost is *usable passes × PU/pass* — price with `backend/scripts/probe_regions.py` (free), never by intuition. `test_monthly_pu_within_budget` fails the build if the registry exceeds budget.
- **A pass must actually image the box** — the catalog's "intersects" is not enough: a swath clipping one corner costs full PU and returns a black chip. `find_target_scene` computes the mosaicked footprint's coverage of `sar_bbox` in PostGIS and refuses below `MIN_FOOTPRINT_COVERAGE` (85%), before any spend. Coverage is the union over the Process API's `[-1 min, +10 min]` window (`PROCESS_WINDOW_BACK`/`FWD`), not one slice — keep those constants in sync with `build_process_request`. Roughly half of all passes fail this; `passes_per_month` in `rois.py` counts only the ones that pass.
- **Analysis admin-gated** — `POST /api/analysis/{roi}` needs `X-Analysis-Key`; all availability checks run before any PU is spent.
- **Two ROI boxes, opposite shapes** (`rois.py`) — `ais_bbox` is free, so it stays wide and hugs the coast (terrestrial receivers do); `sar_bbox` costs PU, so it stays small and on water. `sar_bbox` ⊆ `ais_bbox`, both ≤ ~250 km (one IW swath). Never water-center an `ais_bbox` — it collapses coverage. **Placing a `sar_bbox` inside a real swath track matters more than making it small** — repositioning `north_taiwan` took it from 3/11 usable passes at 179 PU to 11/11 at 65 PU. Open ocean gets no IW coverage at all; open-water corridors get grazed, never imaged. Always re-probe after moving one.
- **Land is masked geometrically, never learned** — a rock is not a vessel, and the fact that separates them is geographic. `landmask.py` flags detections inside `land_polygons`; fusion and counts skip them. Detections are *flagged, not deleted*: re-detecting needs a pixel re-fetch (the full-res chip is never persisted, only the overview), but re-masking is a free SQL recompute, so `LAND_MASK_BUFFER_M` retunes at 0 PU via `scripts/load_land.py`. Buffer defaults to 0 — widening it masks berthed and anchored vessels, which is a real loss in the port ROIs. The mask is a no-op until coastline data is loaded.
- **Survey ROIs never say "dark"** — regions with no AIS coverage run with `mode="survey"`: fusion is skipped, `is_dark` stays NULL, and the UI shows amber "observed vessel" plus a no-ground-truth banner. Red is reserved for a claim we can actually support.
- **Config from env** — CORS, CDSE creds; never hardcoded. `.env` never committed; `.env.example` is the contract. No imagery in repo (`.gitignore` excludes `*.tif`/`*.geotiff`/`*.nc`/`data/`).
- **Compose mirrors prod** — deploy is a config swap, not a rewrite; `--reload` is dev-only.

## Regions (14; 10,688 PU/mo = 36% of budget, measured 2026-07-21)

- **Fused (6)** — AIS verified live 2026-07-12: `singapore_strait` (demo default), `north_taiwan`, `gulf_of_finland`, `skagen_kattegat`, `bosphorus_marmara`, `malta_hurds_bank`. Analysis is refused (409) if the ROI's AIS buffer is empty, so fusion can't mark everything falsely dark.
- **Survey (8)** — no terrestrial AIS, vessel presence only: `hormuz_strait`, `fujairah_anchorage`, `musandam_stage`, `kharg_island`, `eopl_tompok_utara`, `kerch_strait`, `syria_coast_sts`, `somali_coast`. They still subscribe an `ais_bbox` (free) so one can be promoted to fused on evidence — `eopl_tompok_utara` is the likeliest, its box reaches the Singapore receivers.
- Every `sar_bbox` now sits ≥85% inside real swath tracks (median 85–100%). `gulf_of_aden_irtc` was dropped 2026-07-21: the IRTC corridor is open water and passes covered a median 3% of it.
- One AISStream subscription covers the union of all `ais_bbox`es. Re-probe before adding a region (`docs/ais-coverage.md`).
