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

Scaffold complete and smoke-tested (2026-05-09). FastAPI backend, Vite + React frontend, and docker-compose are all wired up and passing. AIS ingestion landed and verified end-to-end (2026-05-10). Multi-ROI ingestion landed (2026-05-18) — a single AISStream subscription now covers all 4 ROIs simultaneously; vessel endpoints take a `?roi=` query param (frontend selector still to be wired). AIS durability hardening landed (2026-05-18) — bounded DB-write retry in `_flush`/`_upsert_ship_metadata` so transient Postgres hiccups no longer tear down the WebSocket, plus an in-memory per-source health tracker (`app/sources.py`) surfaced via an enriched `/api/health` and a `SOURCE_STALE_AFTER_SECONDS` knob. See `docs/scaffold-smoke-test.md` for verified scaffold results.

The SAR source (`app/sar.py`) is scaffolded: a free, unauthenticated CDSE catalog search (`search_scenes`) is implemented; pixel fetch and detection are deferred stubs.

Current phase: **weeks 1–3** — AIS ingestion is live and hardened; remaining: YOLOv8 fine-tune on a SAR ship dataset (HRSID / SSDD).

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
    sar.py              # Sentinel-1 SAR via CDSE — free catalog search done; pixel
                        #   fetch + YOLOv8 detection are deferred stubs
    fusion.py           # SAR detections ↔ AIS cross-reference — stub
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

1. **AIS ingestion** (`ais.py`) — AISStream WebSocket filtered by the union of every ROI's bounding box; positions streamed continuously into PostGIS. No polling — persistent connection, positions arrive as vessels broadcast. Short retention (`AIS_RETENTION_DAYS`, default 2) since correlation only needs AIS bracketing each ~daily SAR pass. Switching the frontend's selected ROI is a pure view filter — ingestion never narrows.
2. **SAR detection** (`sar.py`) — Sentinel-1 IW GRDH VV+VH scenes (via CDSE) run through YOLOv8 (fine-tuned on a SAR ship dataset); returns vessel centroids + confidence. Catalog search is free; pixel fetch + detection are deferred stubs. Ships read as bright returns on dark water; one IW swath (~250 km) covers a right-sized ROI whole.
3. **Fusion** (`fusion.py`) — SAR detections cross-referenced against the AIS buffer at the scene's acquisition timestamp; flagged dark if no AIS match within 500m / 2h; use `ST_DWithin` in SQL — do not reimplement in Python. Clip every conclusion to `ROI ∩ image-footprint` (a detection outside the imaged footprint is *unobserved*, not dark), and never mosaic passes for the correlation (different times = vessels moved).
4. **Surface** (frontend) — react-leaflet map, ROI selector, vessel markers, AIS tracks, SAR footprints, timeline scrubber.

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
- **SAR source via env** — `sar.py` reads `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` for authenticated pixel fetch; catalog search needs no credentials
- **Copernicus credit budget** — 30,000 Processing Units (PU) / month. Catalog search is free (0 PU); only pixel fetch spends PU. Prefer GRD download / COG `/vsicurl` reads over the Sentinel Hub Process API, and never drive discovery through the Copernicus Browser (its rendering also burns PU)
- **ROIs are water-centered and ≤ ~250 km** — one Sentinel-1 IW swath; keeps each ROI fully covered per pass and keeps land returns out of SAR ship detection

## External APIs

- **AISStream** (AIS) — free beta, WebSocket, sign up at aisstream.io with GitHub
- **Sentinel-1 SAR via Copernicus Data Space Ecosystem** (primary, radar) — all-weather/night ship detection, free, ~daily coverage over coastal ROIs via the S1A/S1C/S1D constellation. Catalog search uses the unauthenticated CDSE OData API (0 PU); pixel fetch uses OAuth2 client credentials (`CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET`) at dataspace.copernicus.eu. Product: `IW_GRDH_1SDV` (IW mode, GRD high-res, dual-pol VV+VH).

## Phase context

- **Weeks 1–3 (current):** AIS ingestion (AISStream WebSocket → PostGIS) + YOLOv8 fine-tune on a SAR ship dataset (HRSID / SSDD)
- **Weeks 4–6:** SAR pipeline (Sentinel-1 GRD via CDSE → YOLOv8 inference → detections), fusion logic
- **Weeks 7–9:** Leaflet UI (ROI selector, vessel markers, tracks), FastAPI endpoints, Vercel + Railway deploy
- **Weeks 10–12:** caching, README polish, demo video

Flag scope-creep if a request pulls work forward out of phase.
