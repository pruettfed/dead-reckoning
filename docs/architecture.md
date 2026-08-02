# Architecture

Deeper reference for `backend/app/`. User-facing overview lives in `README.md`; every-session summary and guardrails live in `CLAUDE.md`.

## Four-stage pipeline

1. **AIS ingestion** (`ais.py`, `ingest.py`) — AISStream WebSocket filtered by the union of every ROI's `ais_bbox`; positions stream continuously into PostGIS (persistent connection, no polling). Short retention (`AIS_RETENTION_DAYS`, default 2) since correlation only needs AIS bracketing each ~daily pass. Switching the frontend's ROI is a pure view filter — ingestion never narrows.
2. **SAR detection** (`sar.py`, `detect.py`) — the newest Sentinel-1 IW GRDH pass is fetched via the Sentinel Hub Process API as dB-scaled uint8 chips (VV, 10 m/px, tiled ≤2400 px, single-pass timeRange), then run through YOLOv8 with 800 px sliding-window tiling + global NMS. Returns centroids + confidence (bucketed high/med/low). Ships read as bright returns on dark water; one IW swath (~250 km) covers a right-sized ROI whole.
   - *Fetch-path decision:* Process API chosen for server-side calibration + orthorectification (no local GDAL). If the PU budget tightens, a 0-PU GRD/COG download backend can replace it behind the same `fetch_scene_pixels` signature — swap the seam, don't rewrite the path.
2b. **Land mask** (`landmask.py`) — detections falling inside `land_polygons` (OSM coastline clipped to the ROI boxes by `scripts/load_land.py`) are flagged `on_land` and excluded from fusion and from counts. Rocks, breakwaters and shore structures are bright compact returns that look like small vessels at 10 m/px; the fact that separates them is geographic, not radiometric, so it is decided geometrically rather than left to the model. Flagged rather than deleted — re-detecting costs a pixel re-fetch, but re-masking is a pure recompute over stored points at 0 PU, so `LAND_MASK_BUFFER_M` can be retuned freely. Buffer defaults to 0: widening it past the coastline starts masking berthed and anchored vessels.

3. **Fusion** (`fusion.py`) — detections cross-referenced against the AIS buffer at the scene's acquisition timestamp; flagged dark if no match within 500 m / 2 h (`ST_DWithin` in SQL). Conclusions clipped to `sar_bbox` ∩ image-footprint (a detection outside the footprint is unobserved, not dark). Passes are never mosaicked for correlation (different times = vessels moved). A coverage guard refuses scenes whose correlation window predates the AIS buffer. Skipped entirely in `survey` ROIs, which have no AIS at all — there `is_dark` stays NULL and detections mean presence, not darkness.
4. **Surface** (frontend) — react-leaflet map: ROI selector, live vessel markers + tracks, scene footprints, dark/matched markers. Selecting a scene freezes the vessel layer at its acquisition time (the scene is the time control). The client is read-only — it cannot request imagery, and holds no admin key.

### Scheduling (`scheduler.py`)

Analysis is not requested; it happens. `run_scheduler` is a third lifespan task beside `run_ingest`/`run_retention`, sweeping all 14 ROIs every `SCHEDULER_INTERVAL_SECONDS` (default 900) and analyzing each new usable pass once. It runs **in the API process**, because analysis already ran here as an asyncio task — the scheduler only decides *when* one starts. Regions are swept serially, awaiting each analysis: fourteen concurrent YOLO inferences would exhaust a small container.

It holds no durable state of its own. `sar_scenes.id` is the CDSE product UUID, so a restart re-polls and skips processed scenes at 0 PU. Three guards decide each region (`decide`, pure and unit-tested):

- Already `processed` or `processing` → skip.
- `failed` **with** a `pu_ledger` entry → skip permanently. That fetch already cost money; retrying it every sweep would drain the month. Recovery is a deliberate operator action via `POST /api/analysis/{roi}`.
- `failed` **without** one → retry, since the failure preceded the fetch and costs nothing.
- Month-to-date spend + this fetch's estimate over `PU_MONTHLY_CEILING` → skip.

`pu_ledger` rows are written immediately *before* `fetch_scene_pixels`, so a request that dies mid-flight still counts — the PU is gone either way. Because in-flight state lives only in `pipeline._in_flight`, lifespan startup also reaps orphaned `processing` rows to `failed`.

One catalog call per region per sweep serves both jobs: the 14-day lookback feeds `estimate_next_pass`, and `recent_scenes` narrows it to the 7-day window `find_target_scene` expects (widening that would let a survey ROI, which has no AIS bracket to bound it, analyze fortnight-old imagery).

**Only catalog facts are cached.** `_schedule` holds `latest_scene_sensed_at` and `next_expected_at`, which genuinely only change per sweep; `snapshot()` derives `last_processed_at` and `state` per request from the database and `is_in_flight`. Caching those was a real bug — a region that finished analyzing kept reporting "analyzing" and "never analyzed" until its next sweep, up to 15 minutes later, while `/api/analysis/next-pass` already reported the truth. The rule: if the database knows it, read it; cache only what costs an external call.

## External APIs

- **AISStream** (AIS) — free beta WebSocket, sign up at aisstream.io. One subscription covers the union of all ROI bboxes.
- **Sentinel-1 SAR via Copernicus CDSE** — free, ~daily coastal coverage (S1A/S1C/S1D). Catalog search = unauthenticated OData API (0 PU); pixel fetch = OAuth2 client creds at dataspace.copernicus.eu. Product: `IW_GRDH_1SDV` (IW mode, GRD high-res, VV+VH).

## File map

```
backend/
  Dockerfile            # production-clean (no --reload); compose overrides CMD for dev
  requirements.txt      # API deps + numpy/pillow (chip handling)
  requirements-ml.txt   # CPU-only torch + ultralytics for inference (~1.2 GB image cost)
  .env.example          # env var contract — update whenever a new var is added
  models/               # drop trained checkpoint here (sar_ship.pt); volume-mounted, gitignored
  app/
    main.py             # FastAPI app, lifespan, AIS + scene/detection/analysis endpoints
    config.py           # pydantic-settings Settings; NoDecode on CORS_ORIGINS list field;
                        # ENV (defaults production) + prod invariants that refuse to boot
    security.py         # check_admin_key — constant-time key compare shared by main/devtools
    devtools.py         # Dev-only resets (scenes/AIS/PU ledger) + the /api/dev router,
                        # never registered when ENV=production
    database.py         # async engine, sessionmaker, Base, get_session() dependency
    models.py           # SQLAlchemy models — AISPosition, ShipMetadata, SarSceneRow, SarDetection, PuLedgerEntry
    ais.py              # Pure parsing — AISStream PositionReport → ParsedPosition; also Class B (types 18/19/24)
    ingest.py           # Long-running tasks — run_ingest (WebSocket) + run_retention (hourly prune)
    rois.py             # Static ROI registry: ais_bbox (free, wide, coastal) vs
                        # sar_bbox (costs PU, small, on water) + fused/survey mode
    sources.py          # In-memory per-source health tracker; surfaced at /api/health
    sar.py              # Sentinel-1 via CDSE — free catalog search + Process API pixel fetch
    detect.py           # Tiled YOLOv8 inference: iter_tiles → NMS merge → geo centroids
    fusion.py           # SAR ↔ AIS fusion — coverage guard, footprint clip, ST_DWithin match
    landmask.py         # Coastline mask: flags detections on land, excluded before fusion
    pipeline.py         # Orchestration: find scene → fetch → detect → mask → fuse; next-pass estimate; PU ledger
    scheduler.py        # Automatic analysis sweep: new pass → analyze once, under a PU ceiling
  scripts/
    dev_reset.py        # Keyless CLI for the devtools.py resets (talks to the DB directly)
    analyze.py          # Ops escape hatch: force one ROI's analysis. SPENDS PU.
                        # The only way to force a run in production, which
                        # registers no PU-spending HTTP route
  tests/                # pytest — pure-function tests only (no DB/network/torch)
frontend/src/
  main.tsx              # React root, QueryClientProvider, leaflet CSS
  App.tsx               # ROI selector, scene-as-time control, layout
  api.ts                # apiGet fetch wrapper (read-only client — no POST/DELETE)
  types.ts              # API response types
  countdown.ts          # formatCountdown + useNow tick for the next-pass countdown
  components/           # MapView, VesselLayer, SceneLayer, ScenePanel, SchedulePanel
ml/
  README.md             # Colab runbook — fine-tune YOLOv8 on xView3/SARFish
  safe_to_db.py         # Sentinel-1 GRD .SAFE → calibrated sigma0-dB GeoTIFF
  prepare_xview3.py     # dB GeoTIFF + labels → chipped YOLO dataset
  train.py / eval.py    # ultralytics train + mAP eval
docs/
  api.md                # endpoint reference
  ais-coverage.md       # per-ROI AISStream coverage probe method + results
  architecture.md       # this file
```
