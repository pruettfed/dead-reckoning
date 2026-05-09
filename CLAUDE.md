# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Dark Vessel Detection

Maritime OSINT platform that fuses satellite imagery with public AIS data to flag "dark vessels" — ships visible in satellite imagery but not broadcasting AIS, indicating possibly illicit or military activity. Portfolio project; deployed publicly. All data sources are public and legal.

**Repo state:** scaffold only at the moment. The directory layout described below is the target structure; most files are still empty. When asked to implement something, build toward this structure rather than reorganizing it.

## Architecture (the core idea)

The whole project hinges on a five-stage fusion pipeline. Understand this before touching `backend/app/`:

1. **SAR detection** (`sar.py`) — Pull Sentinel-1 SAR imagery from Copernicus for a region of interest. Detect vessels via backscatter thresholding (works through clouds and at night).
2. **Optical classification** (`planet.py`) — Pull PlanetScope imagery (3–5m, daily revisit) for the same locations. Run fine-tuned YOLOv8 on patches to classify ship type.
3. **AIS ingestion** (`ais.py`) — Poll MarineTraffic / AISHub for AIS positions in the same bounding box; persist to PostGIS.
4. **Fusion** (`fusion.py`) — Cross-reference satellite detections against AIS. A vessel detected via satellite with no AIS match within **500m / 2 hours** is flagged as dark. Use PostGIS `ST_DWithin` natively in SQL for the radius query — do not reinvent it in Python.
5. **Surface** (frontend) — Leaflet map with vessel markers, AIS tracks, satellite footprints, timeline scrubber.

AIS polling cadence is aligned to satellite revisit rate (every ~6 hours). Don't poll faster — it costs API quota with no analytical benefit.

## Layout (target)

```
frontend/   Vite + React + TS, react-leaflet, deployed on Vercel
backend/    FastAPI + SQLAlchemy(async) + GeoAlchemy2, deployed on Railway via Docker
            app/main.py models.py ais.py sar.py planet.py fusion.py
docker-compose.yml   spins up backend + postgis/postgis locally
```

## Commands

Once scaffolded, the local dev loop is:

- `docker compose up` — backend (`localhost:8000`) + PostGIS
- `cd frontend && npm run dev` — Vite dev server (`localhost:5173`), proxies `/api` → backend
- Backend uses `uvicorn --reload` **locally only** — strip the flag in the production Dockerfile.

Tests, lint, and a single-test runner are not yet defined; document them here when added.

## Key constraints (don't relitigate these)

- **Postgres + PostGIS everywhere except earliest local dev.** SQLite is acceptable in week 1 only; do not introduce SQLite-specific code into anything that runs in Docker.
- **Local Docker Compose mirrors production.** Deploying to Railway/Supabase should be a config swap, not a rewrite. If you find yourself adding prod-only code paths, push back.
- **CORS origin** is `localhost:5173` in dev, the Vercel domain in prod. Read from env, not hardcoded.
- **Imagery is never committed.** `.gitignore` excludes `*.tif`, `*.geotiff`, `data/`. If a tool wants to drop imagery into the repo root, redirect it to `data/`.
- **`.env` never committed; `.env.example` is the contract.** Update `.env.example` whenever a new env var is added.

## External APIs and current blockers

- **Copernicus** (Sentinel-1 SAR) — free, available now.
- **MarineTraffic / AISHub** (AIS) — free tier, available now.
- **Planet Labs** (PlanetScope optical) — student/research access **pending (~3 weeks)**. Do not block SAR or AIS work waiting on it.

**Planet Labs contingency** (in order, if access is denied):
1. Google Earth Engine (apply in parallel — faster approval).
2. Sentinel-2 at 10m — reframes CV from ship-type classification to size-category (small/medium/large); lean harder on SAR + AIS gap as the primary contribution.
3. Umbra open SAR archive (1m).

## Phase context

12-week timeline; current phase determines what's in scope:

- **Weeks 1–3 (foundations):** YOLOv8 fine-tune on HRSC2016, AIS API integration. No imagery pipeline yet.
- **Weeks 4–6 (imagery + fusion):** SAR layer, Planet inference, fusion logic. Week 6 (fusion) is the highest-risk week.
- **Weeks 7–9 (frontend + deploy):** Leaflet UI, FastAPI endpoints, Vercel + Railway deploy.
- **Weeks 10–12 (polish):** caching, README, demo video.

If a request would pull work forward out of phase (e.g. building the timeline slider during week 2), flag the tradeoff before doing it.
