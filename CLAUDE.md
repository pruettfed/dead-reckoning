# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---------------------------------------

# Project Dark Vessel Detection

Maritime OSINT platform that detects ships in satellite SAR (radar) imagery and cross-references against live AIS data to flag "dark vessels" — ships visible in imagery but not broadcasting AIS. Detection is single-snapshot correlation: each Sentinel-1 pass is an independent event, and detections are compared against AIS interpolated to the acquisition timestamp. Portfolio project; all data sources are public and legal.

## Repo state

Scaffold complete and smoke-tested (2026-05-09). AIS ingestion landed, hardened, and multi-ROI (2026-05-10 → 2026-05-18): one AISStream subscription covers every ROI, bounded DB-write retry, per-source health tracker (`app/sources.py`) surfaced via `/api/health`.

**AIS coverage reality (verified 2026-07-11/12):** AISStream is terrestrial-receiver-based — the original gray-zone ROIs (Fujairah, Taiwan Strait mid-channel, Spratlys, Kerch) were completely silent, so the registry was rebuilt around regions where narrative AND coverage overlap: `singapore_strait`, `north_taiwan`, `gulf_of_finland`, `skagen_kattegat`, `bosphorus_marmara`, `malta_hurds_bank` (each probe-verified live; data + method in `docs/ais-coverage.md` — re-probe before adding any ROI). Analysis in an ROI with no AIS data is refused per-ROI (409) so fusion can never mark everything falsely dark.

**Full pipeline landed (2026-07-11):** SAR pixel fetch (Sentinel Hub Process API on CDSE, tiled ≤2400 px chips), tiled YOLOv8 inference (`app/detect.py`), `ST_DWithin` fusion with footprint clipping and an AIS-coverage guard (`app/fusion.py`), orchestration (`app/pipeline.py`), admin-gated `POST /api/analysis/{roi}` plus scene/detection/next-pass endpoints, and a react-leaflet frontend skeleton (ROI selector, live vessels + tracks, scene footprints, dark/matched markers, scene-as-time control). Detection confidence buckets (high/med/low) are stored per detection.

**Blocked on user inputs:** a trained checkpoint at `backend/models/sar_ship.pt` (Colab runbook in `ml/README.md`), `CDSE_CLIENT_ID/SECRET`, and `ANALYSIS_API_KEY` — the analysis endpoint answers 503 with a reason until each is present. Live end-to-end analysis (PU-spending fetch + real detections) is unverified until then.

Current phase: **weeks 4–6 wrap-up** — run the Colab fine-tune, then verify one live analysis on `singapore_strait`.

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
- `.venv/bin/pytest` — run unit suite. Pure-function tests only; live AIS / DB / network / torch tests are intentionally out of scope here (would belong in a separate integration suite).

**ML (training runs on Colab GPU, never locally/in Docker)**
- `ml/README.md` — full Colab runbook: HRSID download → convert → train → eval → drop `best.pt` at `backend/models/sar_ship.pt` (volume-mounted, no rebuild)
- `backend/requirements-ml.txt` — CPU-only torch + ultralytics, installed in the backend image for inference

Lint and CI are not yet configured — document here when added.

## Layout

```
CLAUDE.md
README.md
docker-compose.yml
backend/
  Dockerfile            # production-clean (no --reload); compose overrides CMD for dev
  requirements.txt      # API deps + numpy/pillow (chip handling)
  requirements-ml.txt   # CPU-only torch + ultralytics for inference (~1.2 GB image cost)
  .env.example          # env var contract — update here whenever a new var is added
  models/               # drop trained checkpoint here (sar_ship.pt); volume-mounted, gitignored
  app/
    main.py             # FastAPI app, lifespan, AIS + scene/detection/analysis endpoints
    config.py           # pydantic-settings Settings; NoDecode on CORS_ORIGINS list field
    database.py         # async engine, sessionmaker, Base, get_session() dependency
    models.py           # SQLAlchemy models — AISPosition, ShipMetadata, SarSceneRow, SarDetection
    ais.py              # Pure parsing helpers — AISStream PositionReport → ParsedPosition
    ingest.py           # Long-running tasks — run_ingest (WebSocket) + run_retention (hourly prune)
    rois.py             # Static ROI registry; AISStream subscribes to all ROIs at once
    sources.py          # In-memory per-source health tracker; surfaced at /api/health
    sar.py              # Sentinel-1 via CDSE — free catalog search + Process API pixel fetch
    detect.py           # Tiled YOLOv8 inference: iter_tiles → NMS merge → geo centroids
    fusion.py           # SAR ↔ AIS fusion — coverage guard, footprint clip, ST_DWithin match
    pipeline.py         # Orchestration: find scene → fetch → detect → fuse; next-pass estimate
  tests/                # pytest suite — pure-function tests only (no DB/network/torch)
frontend/
  Dockerfile            # used by --profile frontend only; runs pnpm dev inside container
  package.json          # packageManager: pnpm
  vite.config.ts        # /api proxy → VITE_API_PROXY_TARGET ?? localhost:8000
  src/
    main.tsx            # React root, QueryClientProvider, leaflet CSS
    App.tsx             # ROI selector, scene-as-time control, layout
    api.ts              # apiGet/apiPost fetch wrappers
    types.ts            # API response types
    components/         # MapView, VesselLayer, SceneLayer, ScenePanel
ml/
  README.md             # Colab runbook — fine-tune YOLOv8 on HRSID (LS-SSDD fallback)
  prepare_dataset.py    # COCO → YOLO converter (HRSID / LS-SSDD / SSDD)
  train.py / eval.py    # ultralytics train + mAP eval
docs/
  api.md
```

## Architecture

Four-stage pipeline — understand this before touching `backend/app/`:

1. **AIS ingestion** (`ais.py`, `ingest.py`) — AISStream WebSocket filtered by the union of every ROI's bounding box; positions streamed continuously into PostGIS. No polling — persistent connection, positions arrive as vessels broadcast. Short retention (`AIS_RETENTION_DAYS`, default 2) since correlation only needs AIS bracketing each ~daily SAR pass. Switching the frontend's selected ROI is a pure view filter — ingestion never narrows.
2. **SAR detection** (`sar.py`, `detect.py`) — the newest Sentinel-1 IW GRDH pass over an ROI is fetched via the Sentinel Hub Process API as dB-scaled uint8 chips (VV, 10 m/px, tiled ≤2400 px, single-pass timeRange), then run through YOLOv8 (fine-tuned per `ml/README.md`) with 800 px sliding-window tiling + global NMS; returns vessel centroids + confidence (bucketed high/med/low). Ships read as bright returns on dark water; one IW swath (~250 km) covers a right-sized ROI whole.
3. **Fusion** (`fusion.py`) — SAR detections cross-referenced against the AIS buffer at the scene's acquisition timestamp; flagged dark if no AIS match within 500m / 2h (`ST_DWithin` in SQL — never reimplement in Python). Conclusions are clipped to `ROI ∩ image-footprint` (a detection outside the imaged footprint is *unobserved*, not dark); passes are never mosaicked for correlation (different times = vessels moved). A coverage guard refuses scenes whose correlation window predates the AIS buffer — otherwise everything would look falsely dark.
4. **Surface** (frontend) — react-leaflet map, ROI selector, live vessel markers + tracks, scene list with footprints, dark/matched detection markers. Selecting a scene freezes the vessel layer at its acquisition time (the scene *is* the time control). Analysis triggering is admin-only (`X-Analysis-Key`); regular users only ever read results + pass times.

## Constraints

- **PostGIS everywhere** — SQLite acceptable in week 1 only; never in Docker or production paths
- **Compose mirrors prod** — Railway/Supabase deploy must be a config swap, not a rewrite; push back on prod-only code paths
- **CORS from env** — `localhost:5173` in dev, Vercel domain in prod; never hardcoded
- **No imagery in repo** — `.gitignore` excludes `*.tif`, `*.geotiff`, `*.nc`, `data/`; redirect any tool that tries to write imagery to `data/`
- **`.env` never committed** — `.env.example` is the contract; update it when adding env vars
- **`--reload` for dev only** — the Dockerfile CMD omits it; compose overrides the command for local dev
- **SAR source via env** — `sar.py` reads `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` for authenticated pixel fetch; catalog search needs no credentials
- **Copernicus credit budget** — 30,000 Processing Units (PU) / month. Catalog search is free (0 PU); only pixel fetch spends PU (~100 PU per ROI analysis via the Sentinel Hub Process API — the chosen fetch path, decided 2026-07-11: calibrated + orthorectified server-side, seconds per chip, no GDAL). A 0-PU GRD/COG download backend can replace it later behind the same `fetch_scene_pixels` signature if budget ever tightens. Processed scenes are cached in the DB — re-analysis costs 0 PU. Never drive discovery through the Copernicus Browser (its rendering also burns PU)
- **Analysis is admin-gated** — `POST /api/analysis/{roi}` requires `X-Analysis-Key` (= `ANALYSIS_API_KEY`); it spends the operator's PU, so it must never be publicly triggerable. All availability checks (key, CDSE creds, checkpoint, AIS coverage) run before any PU is spent
- **ROIs are water-centered and ≤ ~250 km** — one Sentinel-1 IW swath; keeps each ROI fully covered per pass and keeps land returns out of SAR ship detection

## External APIs

- **AISStream** (AIS) — free beta, WebSocket, sign up at aisstream.io with GitHub
- **Sentinel-1 SAR via Copernicus Data Space Ecosystem** (primary, radar) — all-weather/night ship detection, free, ~daily coverage over coastal ROIs via the S1A/S1C/S1D constellation. Catalog search uses the unauthenticated CDSE OData API (0 PU); pixel fetch uses OAuth2 client credentials (`CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET`) at dataspace.copernicus.eu. Product: `IW_GRDH_1SDV` (IW mode, GRD high-res, dual-pol VV+VH).

## Phase context

- **Weeks 1–3 (done):** AIS ingestion (AISStream WebSocket → PostGIS); training pipeline built (`ml/`), Colab fine-tune run pending
- **Weeks 4–6 (code done 2026-07-11):** SAR pipeline (Process API fetch → YOLOv8 inference → detections), fusion logic, analysis endpoints; live verification blocked on checkpoint + CDSE creds
- **Weeks 7–9:** Leaflet UI skeleton done (2026-07-11); remaining: design pass, Vercel + Railway deploy (deploy needs an ML-capable image sizing pass — torch adds ~1.2 GB)
- **Weeks 10–12:** caching, README polish, demo video

Flag scope-creep if a request pulls work forward out of phase.
