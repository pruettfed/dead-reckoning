# Architecture

Deeper reference for `backend/app/`. User-facing overview lives in `README.md`; every-session summary and guardrails live in `CLAUDE.md`.

## Four-stage pipeline

1. **AIS ingestion** (`ais.py`, `ingest.py`) — AISStream WebSocket filtered by the union of every ROI's bbox; positions stream continuously into PostGIS (persistent connection, no polling). Short retention (`AIS_RETENTION_DAYS`, default 2) since correlation only needs AIS bracketing each ~daily pass. Switching the frontend's ROI is a pure view filter — ingestion never narrows.
2. **SAR detection** (`sar.py`, `detect.py`) — the newest Sentinel-1 IW GRDH pass is fetched via the Sentinel Hub Process API as dB-scaled uint8 chips (VV, 10 m/px, tiled ≤2400 px, single-pass timeRange), then run through YOLOv8 with 800 px sliding-window tiling + global NMS. Returns centroids + confidence (bucketed high/med/low). Ships read as bright returns on dark water; one IW swath (~250 km) covers a right-sized ROI whole.
   - *Fetch-path decision:* Process API chosen for server-side calibration + orthorectification (no local GDAL). If the PU budget tightens, a 0-PU GRD/COG download backend can replace it behind the same `fetch_scene_pixels` signature — swap the seam, don't rewrite the path.
3. **Fusion** (`fusion.py`) — detections cross-referenced against the AIS buffer at the scene's acquisition timestamp; flagged dark if no match within 500 m / 2 h (`ST_DWithin` in SQL). Conclusions clipped to ROI ∩ image-footprint (a detection outside the footprint is unobserved, not dark). Passes are never mosaicked for correlation (different times = vessels moved). A coverage guard refuses scenes whose correlation window predates the AIS buffer.
4. **Surface** (frontend) — react-leaflet map: ROI selector, live vessel markers + tracks, scene footprints, dark/matched markers. Selecting a scene freezes the vessel layer at its acquisition time (the scene is the time control). Analysis triggering is admin-only (`X-Analysis-Key`); regular users only read results + pass times.

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
    config.py           # pydantic-settings Settings; NoDecode on CORS_ORIGINS list field
    database.py         # async engine, sessionmaker, Base, get_session() dependency
    models.py           # SQLAlchemy models — AISPosition, ShipMetadata, SarSceneRow, SarDetection
    ais.py              # Pure parsing — AISStream PositionReport → ParsedPosition
    ingest.py           # Long-running tasks — run_ingest (WebSocket) + run_retention (hourly prune)
    rois.py             # Static ROI registry; AISStream subscribes to all ROIs at once
    sources.py          # In-memory per-source health tracker; surfaced at /api/health
    sar.py              # Sentinel-1 via CDSE — free catalog search + Process API pixel fetch
    detect.py           # Tiled YOLOv8 inference: iter_tiles → NMS merge → geo centroids
    fusion.py           # SAR ↔ AIS fusion — coverage guard, footprint clip, ST_DWithin match
    pipeline.py         # Orchestration: find scene → fetch → detect → fuse; next-pass estimate
  tests/                # pytest — pure-function tests only (no DB/network/torch)
frontend/src/
  main.tsx              # React root, QueryClientProvider, leaflet CSS
  App.tsx               # ROI selector, scene-as-time control, layout
  api.ts                # apiGet/apiPost fetch wrappers
  types.ts              # API response types
  components/           # MapView, VesselLayer, SceneLayer, ScenePanel
ml/
  README.md             # Colab runbook — fine-tune YOLOv8 on HRSID (LS-SSDD fallback)
  prepare_dataset.py    # COCO → YOLO converter (HRSID / LS-SSDD / SSDD)
  train.py / eval.py    # ultralytics train + mAP eval
docs/
  api.md                # endpoint reference
  ais-coverage.md       # per-ROI AISStream coverage probe method + results
  architecture.md       # this file
```
